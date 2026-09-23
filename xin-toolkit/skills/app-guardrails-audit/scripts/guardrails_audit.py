#!/usr/bin/env python3
# guardrails: ignore-file  （本檔內含大量故意寫錯的測試範例）
"""guardrails_audit.py — 依「低資源 App 防禦準則」靜態掃描一個專案。

掃的是「在免費主機／iPhone／GitHub Actions 上會出事」的寫法：
記憶體爆掉、SQLite 鎖死、併發風暴打爆外部 API、金鑰外洩、iOS 分享檔案被吃掉、
Actions 被逾時砍掉等。只用標準函式庫，離線可跑。

為什麼逐行讀檔：這支工具本身也要守 OOM 準則——大型 repo 或 1GB RAM 主機上
不能把整個檔案讀進記憶體；超過 --max-file-kb 的檔案直接略過並在總結列出。

用法：
  python3 guardrails_audit.py audit [路徑] [--json] [--max-file-kb 1024]
  python3 guardrails_audit.py selftest

刻意保留的寫法（例如測試用假金鑰）：在該行加註解 `guardrails: ignore`；
整個檔案都是測試範例時，在前 5 行內加 `guardrails: ignore-file`。

結束碼：0 = 沒有 error 等級問題；1 = 至少一個 error；2 = 參數錯誤（例如路徑不存在）。
"""

import argparse
import json
import os
import re
import sys
import tempfile

# 這些資料夾不是專案自己的程式：第三方套件、建置產物、以及
# 從技能庫複製進來的 .claude/skills（裡面的範例程式會造成大量誤報）。
SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", ".venv", "venv", "env",
    "__pycache__", "site", "vendor", ".next", ".cache", "coverage",
}
SKIP_REL_PREFIXES = (os.path.join(".claude", "skills"),)

CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".htm",
            ".vue", ".svelte", ".sh", ".yml", ".yaml"}
JS_EXT = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".htm", ".vue", ".svelte"}
PY_EXT = {".py"}
HTML_EXT = {".html", ".htm"}

SEV_ORDER = {"error": 0, "warn": 1, "info": 2}


def _rule(rid, sev, exts, pattern, msg, fix):
    return {"id": rid, "sev": sev, "exts": exts,
            "re": re.compile(pattern) if pattern else None, "msg": msg, "fix": fix}


# 單行規則：一行命中就回報。
LINE_RULES = [
    _rule("secret-in-url", "error", CODE_EXT,
          r"""https?://[^\s"'`]*[?&](api[_-]?key|key|token|access_token|secret|password)=""",
          "金鑰／Token 放在網址參數裡",
          "改用 HTTP 標頭（例如 Authorization / x-api-key）；網址會出現在錯誤訊息、代理記錄與瀏覽器歷史。"),
    _rule("hardcoded-secret", "error", CODE_EXT | {".json", ".env", ".txt", ".md"},
          r"(sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AIza[0-9A-Za-z_-]{30,}|xox[bp]-[A-Za-z0-9-]{20,})",
          "疑似把真的金鑰寫進程式碼",
          "立刻到服務商後台撤銷這把金鑰，改放 GitHub Secrets / 環境變數；git 歷史裡也要清掉。"),
    _rule("log-secret", "warn", CODE_EXT,
          r"(print\(|console\.(log|info|debug)\(|echo ).*\b(token|api_?key|secret|password)\b",
          "可能把金鑰印到 log",
          "只印長度或前 4 碼遮罩，例如 f\"token=***{len(t)}\"。"),
    _rule("unbounded-promise-all", "warn", JS_EXT,
          r"Promise\.(all|allSettled)\(\s*[\w.]+\.map\(",
          "Promise.all + map：清單有多長就同時打多少個請求（併發風暴）",
          "改用固定併發數的佇列（例如一次 3 個），並對 429/5xx 做指數退避重試。"),
    _rule("unbounded-gather", "warn", PY_EXT,
          r"asyncio\.gather\(\s*\*",
          "asyncio.gather(*清單)：沒有併發上限",
          "用 asyncio.Semaphore(N) 包住每個工作，或分批（chunk）送出。"),
    _rule("file-to-arraybuffer", "warn", JS_EXT,
          r"(readAsArrayBuffer\(|\.arrayBuffer\(\)|readAsDataURL\()",
          "把整個檔案讀進記憶體（iPhone 上大檔會讓分頁當掉）",
          "上傳時直接把 File 當 body 送（XHR 可顯示進度）；需要處理內容就用 file.slice() 分段。"),
    _rule("pandas-no-chunks", "info", PY_EXT,
          r"pd\.read_(csv|json|table)\((?!.*chunksize)",
          "pandas 一次讀完整份檔案",
          "資料可能變大時加 chunksize=… 分批處理，1GB RAM 主機才不會 OOM。"),
    _rule("hardcoded-model", "warn", CODE_EXT | {".json"},
          r"""["'](claude-(opus|sonnet|haiku|fable)-[\w.-]+|gpt-\d[\w.-]*|gemini-\d[\w.-]*)["']""",
          "寫死外部 AI 模型名稱（常改名／下架）",
          "改成讀環境變數（例如 MODEL_ID），沒設定時呼叫供應商的模型清單 API 自動挑。"),
]


def _iter_files(root, max_bytes, skipped):
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                       and not os.path.normpath(os.path.join(rel_dir, d)).startswith(SKIP_REL_PREFIXES)]
        for name in filenames:
            path = os.path.join(dirpath, name)
            ext = os.path.splitext(name)[1].lower()
            if name.startswith(".env"):
                ext = ".env"
            if ext not in CODE_EXT | {".json", ".env", ".txt", ".md"}:
                continue
            try:
                size = os.path.getsize(path)
            except OSError as e:
                skipped.append((os.path.relpath(path, root), f"讀不到：{e.strerror}"))
                continue
            if size > max_bytes:
                skipped.append((os.path.relpath(path, root), f"檔案 {size // 1024} KB 超過上限，未掃描"))
                continue
            yield path, ext


def _lines(path, skipped, root):
    """逐行讀；遇到二進位或壞編碼就略過並記錄，不讓整次掃描失敗。"""
    try:
        with open(path, "r", encoding="utf-8", errors="strict") as f:
            for i, line in enumerate(f, 1):
                if "\x00" in line:
                    skipped.append((os.path.relpath(path, root), "看起來是二進位檔"))
                    return
                yield i, line
    except UnicodeDecodeError:
        skipped.append((os.path.relpath(path, root), "不是 UTF-8 文字檔"))
    except OSError as e:
        skipped.append((os.path.relpath(path, root), f"讀不到：{e.strerror}"))


def audit(root, max_kb=1024):
    root = os.path.abspath(root)
    findings, skipped = [], []
    scanned = 0
    has_pwa_marker = False
    pwa_marker_file = None
    has_app_ver = False

    for path, ext in _iter_files(root, max_kb * 1024, skipped):
        rel = os.path.relpath(path, root)
        base = os.path.basename(path)
        is_workflow = rel.replace(os.sep, "/").startswith(".github/workflows/") and ext in {".yml", ".yaml"}
        if base in {"manifest.json", "manifest.webmanifest", "sw.js", "service-worker.js"}:
            has_pwa_marker, pwa_marker_file = True, rel

        # 檔案層級規則需要的旗標，一邊逐行掃一邊累積，不另外把整檔讀進來。
        flags = {"journal_mode": False, "busy_timeout": False, "retry": False,
                 "noindex": False, "head": False, "runs_on": False, "timeout": False,
                 "mkdir": False}
        sqlite_lines, http_line, tee_lines, share_window = [], None, [], 0
        share_start, share_has_files, share_has_title = None, False, False
        scanned += 1

        for no, line in _lines(path, skipped, root):
            if no <= 5 and "guardrails: ignore-file" in line:
                skipped.append((rel, "檔案標記 guardrails: ignore-file"))
                findings[:] = [f for f in findings if f["file"] != rel]
                sqlite_lines, http_line, tee_lines, share_window = [], None, [], 0
                flags = dict.fromkeys(flags, True)
                scanned -= 1
                break
            low = line.lower()
            # 測試用的假金鑰／故意寫錯的範例，在行尾加 guardrails: ignore 就不回報單行規則。
            ignored = "guardrails: ignore" in line
            for r in LINE_RULES if not ignored else ():
                if ext in r["exts"] and r["re"].search(line):
                    findings.append(_f(r["id"], r["sev"], rel, no, r["msg"], r["fix"], line))
            if "APP_VER" in line:
                has_app_ver = True

            if ext in PY_EXT | JS_EXT:
                if "journal_mode" in low:
                    flags["journal_mode"] = True
                if "busy_timeout" in low or re.search(r"\btimeout\s*=", line):
                    flags["busy_timeout"] = True
                if re.search(r"retry|retries|backoff|tenacity|attempt", low):
                    flags["retry"] = True
                # 唯讀連線（mode=ro）不會寫入，不需要 WAL 與等鎖設定；標了 ignore 的測試範例也不算。
                if (not ignored and "mode=ro" not in line
                        and re.search(r"sqlite3\.connect\(|new\s+Database\(|better-sqlite3", line)):
                    sqlite_lines.append((no, line))
                if http_line is None and re.search(r"requests\.(get|post|put|delete)\(|urlopen\(|httpx\.|\bfetch\(|axios\.", line):
                    http_line = (no, line)
                # navigator.share 的參數可能跨好幾行，所以往後看 8 行。
                if "navigator.share(" in line and not ignored:
                    share_start, share_window = (no, line), 8
                    share_has_files = share_has_title = False
                if share_window > 0:
                    share_has_files |= bool(re.search(r"\bfiles\s*:", line))
                    share_has_title |= bool(re.search(r"\btitle\s*:", line))
                    share_window -= 1
                    if share_window == 0 or (")" in line and share_start[0] != no):
                        _share_finding(findings, rel, share_start, share_has_files, share_has_title)
                        share_window = 0
            if ext in HTML_EXT:
                if "<head" in low:
                    flags["head"] = True
                if "noindex" in low:
                    flags["noindex"] = True
            if is_workflow:
                if "runs-on:" in line:
                    flags["runs_on"] = True
                if "timeout-minutes" in line:
                    flags["timeout"] = True
                if "mkdir -p" in line:
                    flags["mkdir"] = True
                if re.search(r"\|\s*tee\s+(-a\s+)?[\w.$\{\}-]+/", line):
                    tee_lines.append((no, line))

        # 檔案在 8 行視窗內就結束時，上面的迴圈沒機會收尾，這裡補判一次。
        if share_window > 0:
            _share_finding(findings, rel, share_start, share_has_files, share_has_title)

        for no, line in sqlite_lines:
            if not flags["journal_mode"]:
                findings.append(_f("sqlite-no-wal", "warn", rel, no,
                                   "開 SQLite 連線但整個檔案沒設定 journal_mode",
                                   "連線後執行 PRAGMA journal_mode=WAL，讀寫才不會互相鎖住。", line))
            if not flags["busy_timeout"]:
                findings.append(_f("sqlite-no-timeout", "warn", rel, no,
                                   "SQLite 連線沒有等待鎖的逾時設定（遇鎖立刻丟 database is locked）",
                                   "Python 用 sqlite3.connect(path, timeout=30)；或 PRAGMA busy_timeout=30000，並對寫入加重試。", line))
        if http_line and not flags["retry"]:
            findings.append(_f("http-no-retry", "info", rel, http_line[0],
                               "有對外 HTTP 請求，但整個檔案看不到重試／退避邏輯",
                               "對 429、5xx、逾時做指數退避重試（2/4/8/16 秒，最多 4 次），並尊重 Retry-After。",
                               http_line[1]))
        if ext in HTML_EXT and flags["head"] and not flags["noindex"]:
            findings.append(_f("html-no-noindex", "warn", rel, 1,
                               "網頁沒有 noindex（公開 repo 的站會被搜尋引擎收錄）",
                               '在 <head> 加 <meta name="robots" content="noindex, nofollow">。', ""))
        if is_workflow and flags["runs_on"] and not flags["timeout"]:
            findings.append(_f("workflow-no-timeout", "warn", rel, 1,
                               "工作流沒有 timeout-minutes（預設 6 小時，卡住會燒光分鐘數）",
                               "每個 job 加 timeout-minutes，並在程式裡設時間預算、做完一個存一個進度。", ""))
        if is_workflow and tee_lines and not flags["mkdir"]:
            no, line = tee_lines[0]
            findings.append(_f("workflow-tee-no-mkdir", "warn", rel, no,
                               "tee 寫到子資料夾但沒有先 mkdir -p（pipefail 下整步失敗）",
                               "在那一步前面加 mkdir -p <資料夾>。", line))

    if has_pwa_marker and not has_app_ver:
        findings.append(_f("pwa-no-version", "info", pwa_marker_file, 1,
                           "看起來是 PWA，但找不到 APP_VER 版本號",
                           "前端加 APP_VER，每次改版更新；頁面定期比對伺服器版本，不同就自動重載。", ""))

    findings.sort(key=lambda x: (SEV_ORDER[x["severity"]], x["file"], x["line"]))
    return {"root": root, "scanned_files": scanned, "skipped": skipped, "findings": findings}


def _share_finding(findings, rel, start, has_files, has_title):
    if has_files and has_title:
        findings.append(_f("ios-share-title", "error", rel, start[0],
                           "navigator.share 同時帶 files 與 title：iOS 只會送出 title 文字，檔案被丟掉",
                           "只傳 {files:[f]}；不支援時退回 <a download>。", start[1]))


def _f(rid, sev, rel, line, msg, fix, src):
    return {"rule": rid, "severity": sev, "file": rel, "line": line,
            "message": msg, "fix": fix, "source": src.strip()[:160]}


def _print_report(res):
    counts = {s: sum(1 for f in res["findings"] if f["severity"] == s) for s in SEV_ORDER}
    label = {"error": "🔴 必修", "warn": "🟡 建議修", "info": "🔵 提醒"}
    for f in res["findings"]:
        print(f"{label[f['severity']]} [{f['rule']}] {f['file']}:{f['line']}")
        print(f"   問題：{f['message']}")
        print(f"   怎麼修：{f['fix']}")
        if f["source"]:
            print(f"   原始碼：{f['source']}")
    print()
    print(f"掃描 {res['scanned_files']} 個檔案；必修 {counts['error']}、建議修 {counts['warn']}、提醒 {counts['info']}。")
    if res["skipped"]:
        print(f"略過 {len(res['skipped'])} 個檔案：")
        for rel, why in res["skipped"][:20]:
            print(f"   - {rel}：{why}")
        if len(res["skipped"]) > 20:
            print(f"   …另外 {len(res['skipped']) - 20} 個（用 --json 看完整清單）")
    if res["scanned_files"] == 0:
        print("⚠️ 沒有掃到任何程式檔——路徑是否指錯了？")


def selftest():
    """離線自我測試：造出會觸發／不會觸發的檔案，確認每條規則的邊界。"""
    ok = True

    def check(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    with tempfile.TemporaryDirectory() as d:
        def w(rel, text):
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)

        # 空資料夾：不該當掉，也不該有問題。
        res = audit(d)
        check(res["scanned_files"] == 0 and not res["findings"], "空資料夾不報錯")

        w("bad/db.py", "import sqlite3\nconn = sqlite3.connect('a.db')\n")
        w("ok/ro.py", "import sqlite3\nc = sqlite3.connect(f'file:{p}?mode=ro', uri=True)\n")
        w("good/db.py", "import sqlite3\nc = sqlite3.connect('a.db', timeout=30)\nc.execute('PRAGMA journal_mode=WAL')\n")
        w("bad/api.py", "import requests\nr = requests.get('https://x.io/v1?api_key=' + k)\nprint('token', token)\n")  # guardrails: ignore
        w("good/api.py", "import requests\n# retry with backoff\nr = requests.get(url, headers={'x-api-key': k})\n")
        w("bad/app.js", "await Promise.all(urls.map(u => fetch(u)))\nconst b = await file.arrayBuffer()\n"
                        "navigator.share({\n  title: 'x',\n  files: [f],\n})\n")
        w("good/app.js", "const APP_VER = '3';\nnavigator.share({\n  files: [f],\n})\n// retry\nfetch(u)\n")
        w("bad/share1.js", "navigator.share({files: [f], title: 'x'})")  # guardrails: ignore
        w("ok/ignored.py", "K = 'https://a.io/?token=1'  # guardrails: " + "ignore\n")
        w("ok/fixture.py", "# guardrails: " + "ignore-file\nimport sqlite3\nsqlite3.connect('a')\n")
        w("bad/page.html", "<html><head><title>x</title></head></html>")
        w("good/page.html", '<html><head><meta name="robots" content="noindex, nofollow"></head></html>')
        w("bad/gather.py", "import asyncio\nawait asyncio.gather(*[f(x) for x in xs])\n")
        # 模型名稱用拼接產生，避免這支工具本身被 repo 的「過時模型名稱」檢查誤判。
        w("bad/model.py", "MODEL = '" + "gpt-" + "5-mini" + "'\n")
        w("bad/secret.py", "K = '" + "sk-ant-" + "A" * 24 + "'\n")
        w(".github/workflows/bad.yml", "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make | tee out/log.txt\n")
        w(".github/workflows/good.yml", "jobs:\n  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 20\n    steps:\n"
                                        "      - run: mkdir -p out && make | tee out/log.txt\n")
        # 被複製進來的技能庫範例不該被掃。
        w(".claude/skills/x/bad.py", "import sqlite3\nsqlite3.connect('a')\n")
        # 超過上限的大檔要被略過並列出，而不是默默消失。
        w("big/huge.js", "// x\n" * 400000)
        # 二進位內容要被略過並列出。
        with open(os.path.join(d, "bin.js"), "wb") as fh:
            fh.write(b"\x00\x01\x02abc")
        w("pwa/manifest.json", "{}")

        res = audit(d, max_kb=1024)
        hits = {(f["rule"], f["file"].replace(os.sep, "/")) for f in res["findings"]}
        rules_by_file = lambda p: {r for r, f in hits if f == p}  # noqa: E731

        check({"sqlite-no-wal", "sqlite-no-timeout"} <= rules_by_file("bad/db.py"), "SQLite 沒 WAL／沒 timeout 會被抓")
        check(not rules_by_file("good/db.py"), "SQLite 有 WAL + timeout 不誤報")
        check(not rules_by_file("ok/ro.py"), "SQLite 唯讀連線不報 WAL")
        check({"secret-in-url", "log-secret", "http-no-retry"} <= rules_by_file("bad/api.py"), "金鑰在網址／印金鑰／沒重試會被抓")
        check(not rules_by_file("good/api.py"), "金鑰放標頭且有重試不誤報")
        check({"unbounded-promise-all", "file-to-arraybuffer", "ios-share-title"} <= rules_by_file("bad/app.js"),
              "併發風暴／arrayBuffer／share 帶 title 會被抓")
        check(not rules_by_file("good/app.js"), "share 只帶 files 不誤報")
        check("ios-share-title" in rules_by_file("bad/share1.js"), "share 單行寫在檔尾也會被抓")
        check("html-no-noindex" in rules_by_file("bad/page.html"), "缺 noindex 會被抓")
        check(not rules_by_file("good/page.html"), "有 noindex 不誤報")
        check(not rules_by_file("ok/ignored.py"), "行尾 guardrails: ignore 會跳過")
        check("unbounded-gather" in rules_by_file("bad/gather.py"), "asyncio.gather(*) 會被抓")
        check("hardcoded-model" in rules_by_file("bad/model.py"), "寫死模型名稱會被抓")
        check("hardcoded-secret" in rules_by_file("bad/secret.py"), "寫死金鑰會被抓")
        check({"workflow-no-timeout", "workflow-tee-no-mkdir"} <= rules_by_file(".github/workflows/bad.yml"),
              "工作流沒 timeout／tee 沒 mkdir 會被抓")
        check(not rules_by_file(".github/workflows/good.yml"), "工作流正確寫法不誤報")
        check(not any(f.startswith(".claude/skills") for _, f in hits), ".claude/skills 被略過")
        skipped = dict(res["skipped"])
        check(not rules_by_file("ok/fixture.py") and "ok/fixture.py" in {k.replace(os.sep, "/") for k in skipped},
              "guardrails: ignore-file 整檔跳過且有列出")
        check(any("huge.js" in k for k in skipped), "超大檔被略過且有列出")
        check(any("bin.js" in k for k in skipped), "二進位檔被略過且有列出")
        check(not any(r == "pwa-no-version" for r, _ in hits), "有 APP_VER 時不報 PWA 版本號")
        check(any(f["severity"] == "error" for f in res["findings"]), "有 error 時結束碼應為 1（由 main 處理）")

    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="低資源 App 防禦準則靜態掃描")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("audit", help="掃描專案")
    a.add_argument("path", nargs="?", default=".")
    a.add_argument("--json", action="store_true", help="輸出 JSON")
    a.add_argument("--max-file-kb", type=int, default=1024, help="超過這個大小的檔案略過（預設 1024）")
    sub.add_parser("selftest", help="離線自我測試")
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    if args.cmd != "audit":
        ap.print_help()
        return 2
    if not os.path.isdir(args.path):
        print(f"找不到資料夾：{args.path}。請給專案根目錄的路徑，例如 audit .", file=sys.stderr)
        return 2
    res = audit(args.path, args.max_file_kb)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        _print_report(res)
    return 1 if any(f["severity"] == "error" for f in res["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
