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
STATUS_LABEL = {"clone_failed": "無法 clone", "audit_failed": "掃描失敗",
                "deferred": "留到下週", "not_configured": "未設定權限"}


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


def _skill_dirs(work, commit):
    r = _git(["ls-tree", "-r", "--name-only", commit], cwd=work)
    return {os.path.dirname(p) for p in r.stdout.splitlines()
            if p.endswith("/SKILL.md") and not p.startswith(MIRRORS)}


def _owner(path, dirs):
    # 歸給最深的技能資料夾：巢狀技能（外掛裡的子技能）不會被外層重複算一次。
    d = os.path.dirname(path)
    while d:
        if d in dirs:
            return d
        d = os.path.dirname(d)
    return None


def scan_upstream(cfg_path=UPSTREAM_CFG):
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    side = cfg.get("side_branch", "dev")
    work = tempfile.mkdtemp(prefix="skl-up-")
    try:
        # 只要樹狀結構算差異，不下載檔案內容（blob:none），快又省空間。
        r = _git(["clone", "-q", "--filter=blob:none", "--no-checkout", cfg["repo"], work])
        if r.returncode != 0:
            return {"upstream": {"status": "clone_failed"}}
        head = _git(["rev-parse", "HEAD"], cwd=work).stdout.strip()
        # 上游作者先把修正累積在 dev 再合進 main；只看 main 會好幾週以為「沒變動」。
        # 沒有這個分支就是 None（不算失敗）。
        side_ahead = None
        if _git(["rev-parse", "--verify", "-q", f"origin/{side}"], cwd=work).returncode == 0:
            c = _git(["rev-list", "--count", f"HEAD..origin/{side}"], cwd=work).stdout.strip()
            side_ahead = int(c) if c.isdigit() else None
        res = {"status": "ok", "added": 0, "modified": 0, "deleted": 0, "head": head,
               "side_branch": side, "side_ahead": side_ahead}
        if head == cfg["commit"]:
            return {"upstream": res}
        d = _git(["diff", "--name-only", "--no-renames", cfg["commit"], head], cwd=work)
        if d.returncode != 0:
            return {"upstream": {"status": "baseline_missing"}}
        old, new = _skill_dirs(work, cfg["commit"]), _skill_dirs(work, head)
        # 以技能資料夾為單位：只改 scripts／references 的更新也要算到（舊版只看 SKILL.md 會漏）。
        both = old | new
        touched = {_owner(p, both) for p in d.stdout.splitlines()} - {None}
        # 技能改名（資料夾搬家）算一次更新，不算「新增＋移除」，免得新技能數被灌水。
        ren = _git(["diff", "--name-status", "-M", "--diff-filter=R", cfg["commit"], head, "--", "*SKILL.md"], cwd=work)
        moved = {(os.path.dirname(l.split("\t")[1]), os.path.dirname(l.split("\t")[2]))
                 for l in ren.stdout.splitlines() if l.count("\t") == 2}
        moved = {(a, b) for a, b in moved if a in old - new and b in new - old}
        res.update(added=len(new - old) - len(moved), deleted=len(old - new) - len(moved),
                   modified=len({t for t in touched if t in old and t in new}) + len(moved))
        return {"upstream": res}
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
        # 每個 App 都列一列（含 0／0 與沒掃到的）：只列有問題的會讓「掃描 N 個」和表格列數對不上，
        # 乾淨的 App 看起來像被漏掉。
        lines += ["", "| App | 必修 | 建議修 |", "|---|---|---|"]
        for a in apps:
            name = a["repo"].split("/")[-1]
            if a["status"] == "ok":
                lines.append(f"| {name} | {a['error']} | {a['warn']} |")
            else:
                lines.append(f"| {name} | {STATUS_LABEL.get(a['status'], a['status'])} | — |")
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
            if up.get("side_ahead"):
                lines.append(f"- 上游 {up.get('side_branch', 'dev')} 分支另有 {up['side_ahead']} 個 commit 還沒合進 main"
                             "（作者合併後才會算進上面的數字）。")
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

        # 上游：基準 → 新增 z、改 x 的 SKILL.md、只改 w 的腳本、v 改名 v2、刪掉 y；鏡像資料夾的變動不算
        up = os.path.join(d, "up")
        mkrepo(up, {"a/skills/x/SKILL.md": "1", "a/skills/y/SKILL.md": "1",
                    "a/skills/w/SKILL.md": "1", "a/skills/w/scripts/run.py": "1",
                    "a/skills/v/SKILL.md": "name: v\nlong enough body for rename detection\n"})
        basec = env_git("rev-parse", "HEAD", cwd=up).stdout.strip()
        ucfg = os.path.join(d, "up.json")
        with open(ucfg, "w") as f:
            json.dump({"repo": "file://" + up, "commit": basec}, f)
        u0 = scan_upstream(ucfg)["upstream"]
        check(u0["status"] == "ok" and u0["added"] + u0["modified"] + u0["deleted"] == 0
              and u0["side_ahead"] is None, "上游沒變動、也沒有 dev 分支時為 0，不算失敗")
        for rel, t in {"a/skills/x/SKILL.md": "2", "a/skills/z/SKILL.md": "1", ".gemini/skills/z/SKILL.md": "1",
                       "a/skills/w/scripts/run.py": "2"}.items():
            p = os.path.join(up, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(t)
        os.remove(os.path.join(up, "a/skills/y/SKILL.md"))
        env_git("mv", "a/skills/v", "a/skills/v2", cwd=up)
        env_git("add", "-A", cwd=up)
        env_git("commit", "-q", "-m", "y", cwd=up)
        # dev 比 main 多 2 個 commit（模擬上游作者還沒合併的修正）
        main_br = env_git("rev-parse", "--abbrev-ref", "HEAD", cwd=up).stdout.strip()
        env_git("checkout", "-q", "-b", "dev", cwd=up)
        for i in range(2):
            env_git("commit", "-q", "--allow-empty", "-m", f"dev{i}", cwd=up)
        env_git("checkout", "-q", main_br, cwd=up)
        u = scan_upstream(ucfg)["upstream"]
        check(u["status"] == "ok" and (u["added"], u["modified"], u["deleted"]) == (1, 3, 1),
              "上游新增／更新／移除數正確：只改腳本、改名都算更新，鏡像資料夾不重複計算")
        check(u["side_ahead"] == 2, "算出 dev 比 main 多幾個 commit")
        with open(ucfg, "w") as f:
            json.dump({"repo": "file://" + up, "commit": "0" * 40}, f)
        check(scan_upstream(ucfg)["upstream"]["status"] == "baseline_missing", "基準 commit 不存在時明確標示")

        body, action = compose([res, {"upstream": u}, {"coverage": {"skills": 3, "missing_zh": 0,
                                                                    "uncategorized": 0, "problems": 0}}])
        check("必修 1 項" in body and "新增 1、更新 3、移除 1" in body, "報告寫出數量")
        check("dev 分支另有 2 個 commit" in body, "報告寫出 dev 領先的 commit 數")
        check("sqlite" not in body and "token" not in body.lower(), "報告不含問題內容（公開 Issue 安全）")
        check(action == 3, "需處理項目數 = 有必修的 App 1 + clone 失敗 1 + 上游有變動 1")
        rows = [ln for ln in body.splitlines() if ln.startswith("| ") and not ln.startswith("| App ")]
        check(len(rows) == len(res["apps"]) and "| good | 0 | 0 |" in body,
              "表格列出每個 App（含 0／0 的），列數 = App 總數")
        check("| gone | 無法 clone |" in body and "| secret | 未設定權限 |" in body, "沒掃到的 App 在表格標示原因")
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
