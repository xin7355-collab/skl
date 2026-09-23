#!/usr/bin/env python3
"""weekly_report.py — 每週維護報告：App 健檢、上游更新、中文說明覆蓋率。

報告會貼在公開 Issue，所以只寫數量，不寫問題內容（公開 Issue 會被搜尋引擎收錄）。

用法：
  python3 ops/weekly_report.py apps --out apps.json [--budget-sec 480]
  python3 ops/weekly_report.py upstream --out upstream.json
  python3 ops/weekly_report.py coverage --out coverage.json
  python3 ops/weekly_report.py compose apps.json upstream.json coverage.json --out body.md
      → 最後一行印 ACTION=數字（需要處理的項目數），給工作流判斷要開／關 Issue。
  python3 ops/weekly_report.py selftest

私人 repo：環境變數 SKL_READ_TOKEN 有值才掃；Token 走 HTTP 標頭，不放進網址。
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AUDIT = os.path.join(ROOT, "xin-toolkit", "skills", "app-guardrails-audit", "scripts", "guardrails_audit.py")
APPS_CFG = os.path.join(ROOT, ".github", "apps.json")
UPSTREAM_CFG = os.path.join(ROOT, ".github", "upstream-sync.json")
MIRRORS = (".gemini/", ".codex/", ".vibe/", ".hermes/", "docs/")


def _git(args, cwd=None, token=None, timeout=300):
    cmd = ["git"]
    if token:
        # Token 放在 HTTP 標頭：放網址會出現在錯誤訊息與 log。
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cmd += ["-c", f"http.extraHeader=Authorization: Basic {basic}"]
    return subprocess.run(cmd + args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def scan_apps(cfg_path=APPS_CFG, base_url="https://github.com/", budget_sec=480, token=None):
    with open(cfg_path, encoding="utf-8") as f:
        apps = json.load(f)["apps"]
    token = token if token is not None else os.environ.get("SKL_READ_TOKEN", "")
    out, start = [], time.time()
    work = tempfile.mkdtemp(prefix="skl-apps-")
    try:
        for a in apps:
            repo = a["repo"]
            item = {"repo": repo}
            if a.get("private") and not token:
                item["status"] = "not_configured"
                out.append(item)
                continue
            # 設時間預算：估不完的留到下週，不讓工作流被逾時整批砍掉。
            if time.time() - start > budget_sec:
                item["status"] = "deferred"
                out.append(item)
                continue
            dest = os.path.join(work, repo.replace("/", "__"))
            r = _git(["clone", "-q", "--depth", "1", base_url + repo + ("" if base_url.startswith("file") else ".git"), dest],
                     token=token if a.get("private") else None)
            if r.returncode != 0:
                item["status"] = "clone_failed"
                out.append(item)
                continue
            a_out = subprocess.run([sys.executable, AUDIT, "audit", dest, "--json"],
                                   capture_output=True, text=True, timeout=300)
            try:
                res = json.loads(a_out.stdout)
                sev = [f["severity"] for f in res["findings"]]
                item.update(status="ok", error=sev.count("error"), warn=sev.count("warn"),
                            info=sev.count("info"), files=res["scanned_files"])
            except (json.JSONDecodeError, KeyError):
                item["status"] = "audit_failed"
            shutil.rmtree(dest, ignore_errors=True)  # 掃完就刪，免得十幾個 repo 塞滿磁碟
            out.append(item)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"apps": out}


def scan_upstream(cfg_path=UPSTREAM_CFG):
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    work = tempfile.mkdtemp(prefix="skl-up-")
    try:
        # 只要樹狀結構算差異，不下載檔案內容（blob:none），快又省空間。
        r = _git(["clone", "-q", "--filter=blob:none", "--no-checkout", cfg["repo"], work])
        if r.returncode != 0:
            return {"upstream": {"status": "clone_failed"}}
        head = _git(["rev-parse", "HEAD"], cwd=work).stdout.strip()
        if head == cfg["commit"]:
            return {"upstream": {"status": "ok", "added": 0, "modified": 0, "deleted": 0, "head": head}}
        d = _git(["diff", "--name-status", cfg["commit"], head], cwd=work)
        if d.returncode != 0:
            return {"upstream": {"status": "baseline_missing"}}
        counts = {"A": 0, "M": 0, "D": 0}
        for line in d.stdout.splitlines():
            parts = line.split("\t")
            path = parts[-1]
            if not path.endswith("/SKILL.md") or path.startswith(MIRRORS):
                continue
            k = parts[0][0]
            if k == "R":
                k = "M"
            if k in counts:
                counts[k] += 1
        return {"upstream": {"status": "ok", "added": counts["A"], "modified": counts["M"],
                             "deleted": counts["D"], "head": head}}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def scan_coverage():
    sys.path.insert(0, os.path.join(ROOT, "web"))
    import build_catalog  # noqa: E402
    cat, problems, missing = build_catalog.build()
    other = sum(1 for s in cat["skills"] if s.get("cat") == "other")
    return {"coverage": {"skills": len(cat["skills"]), "missing_zh": len(missing),
                         "uncategorized": other, "problems": len(problems)}}


def compose(parts):
    data = {}
    for p in parts:
        data.update(p)
    lines, action = [], 0
    apps = data.get("apps", [])
    if apps:
        ok = [a for a in apps if a["status"] == "ok"]
        errs = sum(a["error"] for a in ok)
        warns = sum(a["warn"] for a in ok)
        with_err = sum(1 for a in ok if a["error"])
        failed = [a for a in apps if a["status"] in ("clone_failed", "audit_failed")]
        pending = [a for a in apps if a["status"] == "not_configured"]
        deferred = [a for a in apps if a["status"] == "deferred"]
        lines += ["## App 健檢", "",
                  f"- 掃描 {len(ok)}／{len(apps)} 個 App：必修 {errs} 項（分布在 {with_err} 個 App）、建議修 {warns} 項。"]
        if failed:
            lines.append(f"- 無法掃描 {len(failed)} 個（clone 或掃描失敗）。")
        if deferred:
            lines.append(f"- 時間不夠，{len(deferred)} 個留到下週。")
        if pending:
            lines.append(f"- {len(pending)} 個私人 repo 未設定讀取權限（不算失敗）。")
        lines += ["", "| App | 必修 | 建議修 |", "|---|---|---|"]
        for a in ok:
            if a["error"] or a["warn"]:
                lines.append(f"| {a['repo'].split('/')[-1]} | {a['error']} | {a['warn']} |")
        action += with_err + len(failed)
        lines.append("")
    up = data.get("upstream")
    if up:
        lines += ["## 上游技能庫", ""]
        if up["status"] == "ok":
            n = up["added"] + up["modified"] + up["deleted"]
            lines.append(f"- 新增 {up['added']}、更新 {up['modified']}、移除 {up['deleted']} 個技能。" if n
                         else "- 沒有新變動。")
            action += 1 if n else 0
        else:
            lines.append("- 無法檢查上游（網路或基準 commit 問題）。")
            action += 1
        lines.append("")
    cov = data.get("coverage")
    if cov:
        lines += ["## 網頁目錄", "",
                  f"- {cov['skills']} 個技能；缺中文說明 {cov['missing_zh']} 個、未分類 {cov['uncategorized']} 個、"
                  f"設定問題 {cov['problems']} 個。", ""]
        action += (1 if cov["missing_zh"] or cov["uncategorized"] or cov["problems"] else 0)
    if action:
        lines.append("要處理的話，在 skl 的 Claude Code 對話說「處理每週維護報告」。")
    return "\n".join(lines).strip() + "\n", action


def selftest():
    ok = True

    def check(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    with tempfile.TemporaryDirectory() as d:
        env_git = lambda *a, cwd: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True,  # noqa: E731
                                                  env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                                       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

        def mkrepo(path, files):
            os.makedirs(path)
            env_git("init", "-q", cwd=path)
            for rel, text in files.items():
                p = os.path.join(path, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as f:
                    f.write(text)
            env_git("add", "-A", cwd=path)
            env_git("commit", "-q", "-m", "x", cwd=path)

        base = os.path.join(d, "gh") + "/"
        mkrepo(base + "me/bad", {"db.py": "import sqlite3\nsqlite3.connect('a')\n",  # guardrails: ignore
                                 "k.py": "U='https://a.io/?token=' + t\n"})  # guardrails: ignore
        mkrepo(base + "me/good", {"a.py": "print(1)\n"})
        cfg = os.path.join(d, "apps.json")
        with open(cfg, "w") as f:
            json.dump({"apps": [{"repo": "me/bad"}, {"repo": "me/good"}, {"repo": "me/gone"},
                                {"repo": "me/secret", "private": True}]}, f)
        res = scan_apps(cfg, base_url="file://" + base, token="")
        st = {a["repo"]: a for a in res["apps"]}
        check(st["me/bad"]["status"] == "ok" and st["me/bad"]["error"] == 1 and st["me/bad"]["warn"] == 2,
              "有問題的 App 算出必修／建議修數量")
        check(st["me/good"]["status"] == "ok" and st["me/good"]["error"] == 0, "沒問題的 App 為 0")
        check(st["me/gone"]["status"] == "clone_failed", "不存在的 repo 標示 clone 失敗")
        check(st["me/secret"]["status"] == "not_configured", "私人 repo 沒 Token 時標示未設定，不算失敗")
        res2 = scan_apps(cfg, base_url="file://" + base, token="", budget_sec=-1)
        check(all(a["status"] in ("deferred", "not_configured") for a in res2["apps"]), "超過時間預算的留到下週")

        # 上游：基準 → 新增一個技能、改一個、鏡像資料夾的變動不算
        up = os.path.join(d, "up")
        mkrepo(up, {"a/skills/x/SKILL.md": "1", "a/skills/y/SKILL.md": "1"})
        basec = env_git("rev-parse", "HEAD", cwd=up).stdout.strip()
        for rel, t in {"a/skills/x/SKILL.md": "2", "a/skills/z/SKILL.md": "1", ".gemini/skills/z/SKILL.md": "1"}.items():
            p = os.path.join(up, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(t)
        env_git("add", "-A", cwd=up)
        env_git("commit", "-q", "-m", "y", cwd=up)
        ucfg = os.path.join(d, "up.json")
        with open(ucfg, "w") as f:
            json.dump({"repo": "file://" + up, "commit": basec}, f)
        u = scan_upstream(ucfg)["upstream"]
        check(u["status"] == "ok" and (u["added"], u["modified"], u["deleted"]) == (1, 1, 0),
              "上游新增／更新技能數正確，鏡像資料夾不重複計算")
        with open(ucfg, "w") as f:
            json.dump({"repo": "file://" + up, "commit": "0" * 40}, f)
        check(scan_upstream(ucfg)["upstream"]["status"] == "baseline_missing", "基準 commit 不存在時明確標示")

        body, action = compose([res, {"upstream": u}, {"coverage": {"skills": 3, "missing_zh": 0,
                                                                    "uncategorized": 0, "problems": 0}}])
        check("必修 1 項" in body and "新增 1、更新 1" in body, "報告寫出數量")
        check("sqlite" not in body and "token" not in body.lower(), "報告不含問題內容（公開 Issue 安全）")
        check(action == 3, "需處理項目數 = 有必修的 App 1 + clone 失敗 1 + 上游有變動 1")
        _, a0 = compose([{"apps": [{"repo": "me/good", "status": "ok", "error": 0, "warn": 0, "info": 0}]},
                         {"upstream": {"status": "ok", "added": 0, "modified": 0, "deleted": 0}},
                         {"coverage": {"skills": 3, "missing_zh": 0, "uncategorized": 0, "problems": 0}}])
        check(a0 == 0, "全部正常時需處理項目為 0（工作流會關閉 Issue）")

    cov = scan_coverage()["coverage"]
    check(cov["skills"] > 300, "coverage 能讀取真實技能目錄")

    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="每週維護報告")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("apps")
    a.add_argument("--out", required=True)
    a.add_argument("--budget-sec", type=int, default=480)
    u = sub.add_parser("upstream")
    u.add_argument("--out", required=True)
    c = sub.add_parser("coverage")
    c.add_argument("--out", required=True)
    m = sub.add_parser("compose")
    m.add_argument("inputs", nargs="+")
    m.add_argument("--out", required=True)
    sub.add_parser("selftest")
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    if args.cmd in ("apps", "upstream", "coverage"):
        res = {"apps": lambda: scan_apps(budget_sec=args.budget_sec),
               "upstream": scan_upstream, "coverage": scan_coverage}[args.cmd]()
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if args.cmd == "compose":
        parts = []
        for p in args.inputs:
            # 某一段失敗沒產生檔案時，報告照出，只少那一段；不因一段壞掉整份報告消失。
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    parts.append(json.load(f))
            else:
                print(f"::warning::{p} 不存在，報告缺這一段。")
        body, action = compose(parts)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(body)
        print(body)
        print(f"ACTION={action}")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
