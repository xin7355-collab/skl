#!/usr/bin/env python3
"""skill_eval.py — 自製技能的「有技能／沒技能」對照測試。

為什麼：技能有沒有用，不能只看「有被叫用」。官方建議同一個題目在乾淨的工作階段各跑一次
（有技能、把技能拿掉），用同一套標準比結果。這支程式建立兩份一模一樣的練習 App
（都放 CLAUDE.template.md 當工作守則，跟真的 App 一樣），只差有沒有把技能裝進
.claude/skills/，再用 `claude -p` 各跑一次，最後用程式評分（不靠 AI 打分，避免評審偏心）。

用法：
  python3 ops/skill_eval.py selftest                 # 離線測評分規則，不呼叫 Claude
  python3 ops/skill_eval.py run --out DIR [--repeats 2] [--jobs 6] [--case tw-stock]
  python3 ops/skill_eval.py grade DIR                # 重新評分已跑完的結果

注意：run 會用掉 Claude 訂閱用量（每次約幾分鐘）。評分是看關鍵做法有沒有出現（例如有沒有用官方
資料來源、有沒有寫死模型名稱），是粗略指標，不是完整的程式碼審查。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "xin-toolkit", "skills")
TEMPLATE = os.path.join(SKILLS, "app-bootstrap", "assets", "CLAUDE.template.md")
# 測試時把網路指到不存在的代理：題目要求「離線可跑的測試」，偷偷連網的不能算過
OFFLINE_ENV = {"HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
               "https_proxy": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9", "NO_PROXY": ""}
ALLOWED_TOOLS = ("Read,Write,Edit,Glob,Grep,Skill,Bash(python3:*),Bash(node:*),Bash(ls:*),Bash(mkdir:*),"
                 "Bash(cp:*),Bash(cat:*),Bash(find:*),Bash(head:*),Bash(grep:*),Bash(wc:*),Bash(chmod:*)")


def code_files(work, exts, skip_names=()):
    """App 自己的程式（不含裝進來的技能、不含刻意排除的檔名）。"""
    out = []
    for dp, dns, fns in os.walk(work):
        dns[:] = [d for d in dns if d not in (".claude", "node_modules", "__pycache__", ".git")]
        for fn in fns:
            if fn.endswith(exts) and fn not in skip_names:
                out.append(os.path.join(dp, fn))
    return out


def read_all(paths):
    return "\n".join(open(p, encoding="utf-8", errors="ignore").read() for p in paths)


def run_cmd(cmd, cwd, timeout=120):
    try:
        r = subprocess.run(cmd, cwd=cwd, env=dict(os.environ, **OFFLINE_ENV), capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


# ── 三個題目：都是使用者平常會講的話，評分只看「做法對不對」 ──

TW_PROMPT = ("幫我寫一支 Python 腳本 fetch_close.py：每天抓台股上市股票最新的收盤價，存進 SQLite（data/prices.db），"
             "之後要用 GitHub Actions 每天排程跑。只能用 Python 標準函式庫。這次不要連網測試，"
             "附一個離線可以跑的自我測試（python3 fetch_close.py selftest）。")


def tw_checks(work):
    src = read_all(code_files(work, (".py",)))
    return {
        "用官方 OpenAPI": "openapi.twse.com.tw" in src,
        "SQLite 開 WAL": bool(re.search(r"journal_mode\s*=\s*WAL", src, re.I)),
        "請求有逾時": bool(re.search(r"urlopen\([^)]*timeout\s*=|timeout\s*=\s*\d", src)),
        "失敗會指數退避重試": bool(re.search(r"\*\*\s*\(?\s*(attempt|i|n|retry|tries|k)\b|backoff|退避", src, re.I)),
        "同日重抓不重複": bool(re.search(r"INSERT\s+OR\s+(REPLACE|IGNORE)|ON\s+CONFLICT|PRIMARY\s+KEY|UNIQUE", src, re.I)),
        "離線自我測試會過": os.path.isfile(os.path.join(work, "fetch_close.py"))
            and run_cmd([sys.executable, "fetch_close.py", "selftest"], work),
    }


AUDIT_FILES = {
    "index.html": "<!doctype html><html><head><meta charset='utf-8'><title>記帳</title>"
                  "<link rel='manifest' href='manifest.json'></head><body><script src='upload.js'></script>"
                  "<script src='share.js'></script><script src='config.js'></script></body></html>\n",
    "db.py": "import sqlite3\n\n\ndef save(rows):\n    con = sqlite3.connect('app.db')\n"
             "    con.executemany('INSERT INTO t VALUES (?, ?)', rows)\n    con.commit()\n",
    "fetcher.py": "import json\nimport urllib.request\n\n\ndef fetch_all(ids):\n    out = []\n    for i in ids:\n"
                  "        with urllib.request.urlopen('https://api.example.com/items/%s' % i) as r:\n"
                  "            out.append(json.load(r))\n    return out\n",
    "ai.py": "import os\nimport urllib.request\n\nMODEL = 'gemini-1.5-flash'\n\n\ndef ask(prompt):\n"
             "    req = urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/models/' + MODEL + ':generateContent',\n"
             "                                 headers={'x-goog-api-key': os.environ['GEMINI_API_KEY']})\n"
             "    return urllib.request.urlopen(req, timeout=30).read()\n",
    "config.js": "const API_KEY = 'demo-key-123';\nfetch('https://api.example.com/data?api_key=' + API_KEY);\n",
    "upload.js": "async function upload(file) {\n  const buf = await file.arrayBuffer();\n"
                 "  await fetch('/upload', { method: 'POST', body: buf });\n}\n",
    "share.js": "async function shareReport(f) {\n  await navigator.share({ files: [f], title: '月報表' });\n}\n",
    ".github/workflows/daily.yml": "name: daily\non:\n  schedule:\n    - cron: '0 1 * * *'\njobs:\n  run:\n"
                                   "    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v5\n"
                                   "      - run: python3 fetcher.py | tee logs/run.log\n",
}
AUDIT_PROMPT = ("幫我健檢這個 App：會不會在 1GB RAM 的免費主機或 iPhone 上當掉、會不會被外部 API 封鎖、"
                "有沒有金鑰外洩或違反 CLAUDE.md 的地方。把發現的問題寫進 AUDIT.md（每項寫檔案名稱、問題、怎麼修），"
                "先不要改程式。")


def audit_checks(work):
    p = os.path.join(work, "AUDIT.md")
    t = open(p, encoding="utf-8", errors="ignore").read() if os.path.isfile(p) else ""
    low = t.lower()
    has = lambda *ws: all(w.lower() in low for w in ws)
    anyof = lambda *ws: any(w.lower() in low for w in ws)
    unchanged = all(
        os.path.isfile(os.path.join(work, f)) and open(os.path.join(work, f), encoding="utf-8").read() == c
        for f, c in AUDIT_FILES.items())
    return {
        "網頁缺 noindex": has("index.html", "noindex"),
        "SQLite 沒 WAL": has("db.py", "wal"),
        "請求沒逾時": has("fetcher.py") and anyof("timeout", "逾時"),
        "請求沒重試／退避": has("fetcher.py") and anyof("retry", "重試", "退避", "backoff"),
        "金鑰放在網址": has("config.js") and anyof("網址", "url", "query"),
        "整個檔案讀進記憶體": anyof("arraybuffer"),
        "iOS 分享同時帶 title": has("share.js", "title"),
        "寫死模型名稱": has("ai.py") and anyof("模型", "model"),
        "工作流沒逾時": anyof("timeout-minutes"),
        "tee 前沒 mkdir": anyof("mkdir"),
        "沒有動到程式": unchanged,
    }


WORKER_JS = ("export default {\n  async fetch(request, env) {\n    const url = new URL(request.url);\n"
             "    if (url.pathname === '/health') return new Response('ok');\n"
             "    return new Response('not found', { status: 404 });\n  },\n};\n")
AI_PROMPT = ("這個 Cloudflare Worker（worker.js）要加一個 /summary 功能：收到 POST 的文字，就用 AI（Gemini）摘要成三行繁體中文回傳。"
             "金鑰放在 env.GEMINI_API_KEY。這次不要連網測試，寫一個離線測試（node test.js）用假的 fetch 驗證。")
MODEL_RE = re.compile(r"gemini-\d|gemini-[\w.]*(flash|pro)|claude-(opus|sonnet|haiku)-\d|gpt-\d", re.I)


def ai_checks(work):
    # 複製進來的模組可能改成 .cjs／.mjs（實測就有改名成 model_router.cjs 的）：副檔名都要看
    exts = (".js", ".mjs", ".cjs", ".ts")
    files = code_files(work, exts, skip_names=("model_router.js", "model_router.cjs", "model_router.mjs"))
    app = read_all([f for f in files if not re.search(r"test", os.path.basename(f), re.I)])
    allsrc = read_all(code_files(work, exts))
    return {
        # 先要真的做了 /summary，否則原本的 worker.js 什麼都沒寫也會「沒寫死模型」
        "程式沒寫死模型名稱": "/summary" in app and not MODEL_RE.search(app),
        "金鑰走標頭不放網址": "x-goog-api-key" in allsrc.lower() and not re.search(r"[?&]key=", allsrc),
        "只寫等級（fast／smart…）": bool(re.search(r"""["'](fast|smart|best|auto)["']""", allsrc)),
        # 不能只看「404」：原本的 worker.js 回 404 not found 就會誤判；也不能看 candidates（Gemini 回應本來就有這個欄位）
        "模型下架會自動換": bool(re.search(r"NOT_FOUND|fallback|下架|候選|status\s*===?\s*404", allsrc, re.I)),
        # 只認真的請求逾時；setTimeout（等待重試）不算
        "請求有逾時": bool(re.search(r"AbortSignal\.timeout|AbortController|\bsignal\s*:", allsrc)),
        "離線測試會過": os.path.isfile(os.path.join(work, "test.js")) and run_cmd(["node", "test.js"], work),
    }


CASES = {
    "tw-stock": {"skill": "tw-stock", "prompt": TW_PROMPT, "files": {}, "checks": tw_checks},
    "app-guardrails-audit": {"skill": "app-guardrails-audit", "prompt": AUDIT_PROMPT, "files": AUDIT_FILES, "checks": audit_checks},
    "ai-model-router": {"skill": "ai-model-router", "prompt": AI_PROMPT,
                        "files": {"worker.js": WORKER_JS, "package.json": '{"name":"demo-worker","type":"module"}\n'},
                        "checks": ai_checks},
}


def setup(work, case, with_skill, template=TEMPLATE, skills_dir=SKILLS):
    os.makedirs(work, exist_ok=True)
    shutil.copyfile(template, os.path.join(work, "CLAUDE.md"))
    for rel, content in case["files"].items():
        p = os.path.join(work, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    if with_skill:
        shutil.copytree(os.path.join(skills_dir, case["skill"]), os.path.join(work, ".claude", "skills", case["skill"]),
                        ignore=shutil.ignore_patterns("__pycache__"))


def invoked(transcript_path, skill):
    """有沒有真的用到技能：叫用 Skill 工具，或打開了技能的 SKILL.md。"""
    if not os.path.isfile(transcript_path):
        return False
    for line in open(transcript_path, encoding="utf-8", errors="ignore"):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        # 紀錄裡有些事件的 message 是純文字（系統訊息），只看有 content 清單的那種
        msg = ev.get("message") if isinstance(ev, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        for b in (content if isinstance(content, list) else []):
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            inp = b.get("input") or {}
            if b.get("name") == "Skill" and skill in str(inp.get("skill", "")):
                return True
            if f".claude/skills/{skill}/" in json.dumps(inp, ensure_ascii=False):
                return True
    return False


def run_one(job):
    case_id, cond, rep, out = job
    case = CASES[case_id]
    base = os.path.join(out, f"{case_id}__{cond}__{rep}")
    work = os.path.join(base, "work")
    if os.path.isdir(base):
        shutil.rmtree(base)
    setup(work, case, cond == "with")
    t0 = time.time()
    tr = os.path.join(base, "transcript.jsonl")
    with open(tr, "w", encoding="utf-8") as f:
        try:
            subprocess.run(["claude", "-p", case["prompt"], "--output-format", "stream-json", "--verbose",
                            "--max-turns", "40", "--allowedTools", ALLOWED_TOOLS, "--disallowedTools", "WebFetch,WebSearch"],
                           cwd=work, stdout=f, stderr=subprocess.STDOUT, timeout=1200)
        except subprocess.TimeoutExpired:
            f.write(json.dumps({"type": "eval_timeout"}) + "\n")
    meta = {"seconds": round(time.time() - t0)}
    for line in open(tr, encoding="utf-8", errors="ignore"):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            meta.update({"turns": ev.get("num_turns"), "cost_usd": ev.get("total_cost_usd"), "error": ev.get("is_error")})
    with open(os.path.join(base, "meta.json"), "w") as f:
        json.dump(meta, f)
    return base


def grade_dir(out):
    rows = []
    for name in sorted(os.listdir(out)):
        m = re.match(r"(.+)__(with|without)__(\d+)$", name)
        if not m or m.group(1) not in CASES:
            continue
        case_id, cond, rep = m.group(1), m.group(2), int(m.group(3))
        base = os.path.join(out, name)
        checks = CASES[case_id]["checks"](os.path.join(base, "work"))
        meta = json.load(open(os.path.join(base, "meta.json"))) if os.path.isfile(os.path.join(base, "meta.json")) else {}
        rows.append({"case": case_id, "cond": cond, "rep": rep, "checks": checks, "score": sum(checks.values()),
                     "max": len(checks), "invoked": invoked(os.path.join(base, "transcript.jsonl"), CASES[case_id]["skill"]), **meta})
    return rows


def summary(rows):
    lines = ["| 題目 | 條件 | 第幾次 | 分數 | 有用到技能 | 回合 | 秒 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['case']} | {'有技能' if r['cond'] == 'with' else '沒技能'} | {r['rep']} | {r['score']}/{r['max']} | "
                     f"{'是' if r['invoked'] else '否'} | {r.get('turns', '?')} | {r.get('seconds', '?')} |")
    lines.append("")
    for case_id in CASES:
        for cond in ("with", "without"):
            rs = [r for r in rows if r["case"] == case_id and r["cond"] == cond]
            if rs:
                lines.append(f"- {case_id}／{'有技能' if cond == 'with' else '沒技能'}：平均 "
                             f"{sum(r['score'] for r in rs) / len(rs):.1f}/{rs[0]['max']}")
    lines.append("")
    for case_id in CASES:
        rs = [r for r in rows if r["case"] == case_id]
        if not rs:
            continue
        lines.append(f"**{case_id}** 各項通過次數（有技能／沒技能）：")
        for k in rs[0]["checks"]:
            w = sum(r["checks"][k] for r in rs if r["cond"] == "with")
            wo = sum(r["checks"][k] for r in rs if r["cond"] == "without")
            nw = sum(1 for r in rs if r["cond"] == "with")
            nwo = sum(1 for r in rs if r["cond"] == "without")
            lines.append(f"- {k}：{w}/{nw}／{wo}/{nwo}")
        lines.append("")
    return "\n".join(lines)


def selftest():
    ok = True

    def t(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    with tempfile.TemporaryDirectory() as d:
        # 建立練習 App：兩份只差在有沒有技能
        for cid, case in CASES.items():
            setup(os.path.join(d, cid, "w"), case, True)
            setup(os.path.join(d, cid, "o"), case, False)
            t(os.path.isfile(os.path.join(d, cid, "w", ".claude", "skills", case["skill"], "SKILL.md"))
              and not os.path.exists(os.path.join(d, cid, "o", ".claude")), f"{cid}：有技能那份才有 .claude/skills")
            t(open(os.path.join(d, cid, "o", "CLAUDE.md"), encoding="utf-8").read() == open(TEMPLATE, encoding="utf-8").read(),
              f"{cid}：兩份都放同一份工作守則")
        # 沒做任何事：每一項都不能過（避免評分規則太鬆）
        t(not any(v for k, v in audit_checks(os.path.join(d, "app-guardrails-audit", "o")).items() if k != "沒有動到程式"),
          "健檢題：沒寫報告就全部不過")
        t(audit_checks(os.path.join(d, "app-guardrails-audit", "o"))["沒有動到程式"], "健檢題：沒改程式會被認出來")
        t(not any(tw_checks(os.path.join(d, "tw-stock", "o")).values()), "台股題：沒寫程式就全部不過")
        t(not any(ai_checks(os.path.join(d, "ai-model-router", "o")).values()), "AI 題：原本的 worker.js 不會被當成做對")

        # 健檢題：一份涵蓋全部問題的報告要全過；改了程式要被抓到
        w = os.path.join(d, "app-guardrails-audit", "o")
        open(os.path.join(w, "AUDIT.md"), "w", encoding="utf-8").write(
            "- index.html 缺 noindex\n- db.py 沒開 WAL\n- fetcher.py 沒有 timeout、沒有 retry 退避\n"
            "- config.js 金鑰放在網址 query\n- upload.js 用 arrayBuffer 整個讀進記憶體\n- share.js 同時帶 title\n"
            "- ai.py 寫死模型名稱\n- daily.yml 沒有 timeout-minutes，tee 前要 mkdir\n")
        t(all(audit_checks(w).values()), "健檢題：完整報告全部通過")
        open(os.path.join(w, "db.py"), "a").write("# changed\n")
        t(not audit_checks(w)["沒有動到程式"], "健檢題：偷改程式會被扣分")

        # 台股題：好的寫法全過、壞的寫法不過
        w = os.path.join(d, "tw-stock", "o")
        good = ("import sqlite3, sys, time, urllib.request\nURL='https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'\n"
                "def get(u):\n    for attempt in range(4):\n        try:\n            return urllib.request.urlopen(u, timeout=20).read()\n"
                "        except OSError:\n            time.sleep(2 ** attempt)\n"
                "def db():\n    c=sqlite3.connect(':memory:'); c.execute('PRAGMA journal_mode=WAL')\n"
                "    c.execute('CREATE TABLE p(d TEXT, code TEXT, close REAL, PRIMARY KEY(d, code))'); return c\n"
                "if __name__ == '__main__' and sys.argv[1:] == ['selftest']:\n    db(); print('ok')\n")
        open(os.path.join(w, "fetch_close.py"), "w").write(good)
        t(all(tw_checks(w).values()), "台股題：官方來源＋WAL＋逾時＋退避＋不重複＋測試會過")
        open(os.path.join(w, "fetch_close.py"), "w").write(
            "import urllib.request\nurllib.request.urlopen('https://www.twse.com.tw/exchangeReport/STOCK_DAY')\n")
        r = tw_checks(w)
        t(not r["用官方 OpenAPI"] and not r["SQLite 開 WAL"] and not r["離線自我測試會過"], "台股題：非官方網址、沒 WAL、測試會連網都不過")

        # AI 題：寫死模型名稱、金鑰放網址要被抓到；只寫等級的寫法要過
        w = os.path.join(d, "ai-model-router", "o")
        open(os.path.join(w, "worker.js"), "w").write(
            "const u = 'https://x/v1beta/models/gemini-2.5-flash:generateContent?key=' + env.GEMINI_API_KEY;\n")
        r = ai_checks(w)
        t(not r["程式沒寫死模型名稱"] and not r["金鑰走標頭不放網址"], "AI 題：寫死 gemini-2.5-flash、金鑰在網址都會被抓")
        open(os.path.join(w, "worker.js"), "w").write("// /summary\nconst text = data.candidates[0].content.parts[0].text;\n")
        t(not ai_checks(w)["模型下架會自動換"], "AI 題：只讀回應的 candidates 欄位不算會自動換模型")
        open(os.path.join(w, "worker.js"), "w").write(
            "import { ModelRouter } from './model_router.js';\nconst r = new ModelRouter('gemini');\n"
            "// /summary\nexport const run = (t) => r.chat('fast', [{ role: 'user', content: t }]);\n")
        open(os.path.join(w, "model_router.js"), "w").write(
            "// 候選：gemini-2.5-flash-lite；404 就換下一個\nheaders['x-goog-api-key']=k; AbortSignal.timeout(20000);\n")
        open(os.path.join(w, "retry.js"), "w").write("setTimeout(r, 1000);\n")
        open(os.path.join(w, "test.js"), "w").write("process.exit(0);\n")
        t(all(ai_checks(w).values()), "AI 題：只寫等級（模型名稱只在 router 裡）全部通過")

        # 有沒有用到技能：Skill 工具或打開 SKILL.md 都算
        tr = os.path.join(d, "t.jsonl")
        open(tr, "w").write(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/x/.claude/skills/tw-stock/SKILL.md"}}]}}) +
                     "\nnot json\n" + json.dumps({"type": "system", "message": "純文字訊息"}) + "\n" + json.dumps([1, 2]) + "\n")
        t(invoked(tr, "tw-stock") and not invoked(tr, "ai-model-router"), "認得出有沒有用到技能")
        t(not invoked(os.path.join(d, "missing.jsonl"), "tw-stock"), "沒有紀錄檔就算沒用到")

        # 評分結果整理
        os.makedirs(os.path.join(d, "out", "tw-stock__with__1", "work"))
        rows = grade_dir(os.path.join(d, "out"))
        t(len(rows) == 1 and rows[0]["score"] == 0 and "平均 0.0" in summary(rows), "整理成表格")
    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv):
    ap = argparse.ArgumentParser(description="自製技能有／沒有對照測試")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--repeats", type=int, default=2)
    r.add_argument("--jobs", type=int, default=6)
    r.add_argument("--case", choices=sorted(CASES), action="append")
    g = sub.add_parser("grade")
    g.add_argument("out")
    a = ap.parse_args(argv[1:])
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "run":
        if not shutil.which("claude"):
            print("找不到 claude 指令：這個環境沒有安裝 Claude Code，沒辦法跑對照測試。")
            return 1
        os.makedirs(a.out, exist_ok=True)
        jobs = [(c, cond, i, a.out) for c in (a.case or CASES) for i in range(1, a.repeats + 1) for cond in ("with", "without")]
        with ThreadPoolExecutor(max_workers=a.jobs) as ex:
            for base in ex.map(run_one, jobs):
                print("完成：" + os.path.basename(base), flush=True)
        a.out = a.out
    rows = grade_dir(a.out)
    if not rows:
        print("這個資料夾裡沒有跑完的結果。")
        return 1
    with open(os.path.join(a.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(summary(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
