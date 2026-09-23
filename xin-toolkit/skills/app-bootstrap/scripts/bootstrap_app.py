#!/usr/bin/env python3
"""bootstrap_app.py — 把 skl 技能庫裡挑好的技能「複製」進另一個 App 的 repo。

為什麼用複製而不是外掛市集：雲端工作階段（claude.ai/code、iPhone）從 GitHub
外掛市集安裝外掛不一定會自動完成；而 repo 裡的 `.claude/skills/` 每次開工作階段
都一定會被讀到。所以複製進去最穩，代價是要更新時重跑一次 `update`。

用法（在「別的 App」的工作階段裡）：
  git clone --depth 1 https://github.com/xin7355-collab/skl /tmp/skl
  python3 /tmp/skl/xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py list
  python3 /tmp/skl/xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py install --target . --packs core,pwa
  python3 /tmp/skl/xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py install --target . --skills engineering/skills/focused-fix
  python3 /tmp/skl/xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py update --target .
  python3 /tmp/skl/xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py selftest

結束碼：0 = 全部成功；1 = 有技能沒裝成功（會列出是哪幾個、為什麼）；2 = 參數錯誤。
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.abspath(os.path.join(SKILL_DIR, "..", "..", ".."))
PACKS_FILE = os.path.join(SKILL_DIR, "assets", "packs.json")
CLAUDE_TEMPLATE = os.path.join(SKILL_DIR, "assets", "CLAUDE.template.md")
ALWAYS = ["xin-toolkit/skills/app-guardrails-audit"]
MANIFEST = ".skl-vendor.json"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 技能裡不該有大檔；有的話多半是誤放的資料，不複製。
AUDITOR_REL = "engineering/skills/skill-security-auditor/scripts/skill_security_auditor.py"
HOOK_REL = os.path.join(".claude", "hooks", "skl-session-start.sh")
HOOK_CMD = '"$CLAUDE_PROJECT_DIR"/.claude/hooks/skl-session-start.sh'

# 每次開 Claude Code 工作階段時自動執行：離線快速健檢＋技能太久沒更新就提醒。
# 一律 exit 0：健檢失敗不能擋住使用者開工。
HOOK_SCRIPT = r"""#!/bin/bash
# 由 skl 的 app-bootstrap 安裝。開工作階段時做一次離線健檢，並提醒技能是否太久沒更新。
set -u
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
A=.claude/skills/app-guardrails-audit/scripts/guardrails_audit.py
if [ -f "$A" ]; then
  out=$(timeout 60 python3 "$A" audit . 2>/dev/null | grep '^掃描' | tail -1)
  [ -n "$out" ] && echo "[skl 健檢] $out 要看細節請說「跑一次防禦健檢」。"
fi
M=.claude/skills/.skl-vendor.json
if [ -f "$M" ]; then
  python3 - "$M" <<'PY' 2>/dev/null
import datetime, json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
t = datetime.datetime.strptime(m.get("updated_at", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
days = (datetime.datetime.now(datetime.timezone.utc) - t).days
if days >= 14:
    print(f"[skl] 技能已 {days} 天沒更新，可以說「把技能更新到最新版」。")
PY
fi
exit 0
"""


def load_packs(path=PACKS_FILE):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _source_commit(root):
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _ignore(src_dir, names):
    skip = []
    for n in names:
        p = os.path.join(src_dir, n)
        if n in {"__pycache__", ".DS_Store"} or n.endswith(".pyc"):
            skip.append(n)
        elif os.path.isfile(p) and os.path.getsize(p) > MAX_FILE_BYTES:
            skip.append(n)
    return skip


def _read_manifest(skills_root):
    p = os.path.join(skills_root, MANIFEST)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def security_scan(src, source_root=REPO_ROOT, auditor=None):
    """用技能庫內建的 skill-security-auditor 掃描一個技能。
    回傳 (verdict, 說明)；verdict 為 PASS／WARN／FAIL／ERROR。
    掃描工具壞掉時回 ERROR，由呼叫端「不安裝」——寧可少裝，也不在沒掃描的情況下裝。"""
    auditor = auditor or os.path.join(source_root, AUDITOR_REL)
    if not os.path.isfile(auditor):
        return "ERROR", "找不到安全掃描工具（skill-security-auditor）"
    try:
        out = subprocess.run([sys.executable, auditor, src, "--json"],
                             capture_output=True, text=True, timeout=120)
        rep = json.loads(out.stdout)
    except subprocess.TimeoutExpired:
        return "ERROR", "安全掃描逾時"
    except (OSError, json.JSONDecodeError, ValueError):
        return "ERROR", "安全掃描工具輸出無法解讀"
    verdict = rep.get("verdict", "ERROR")
    cats = sorted({f.get("category", "?") for f in rep.get("findings", [])})
    return verdict, ("、".join(cats) if cats else "")


def install_hook(target, res):
    """安裝 SessionStart hook；合併既有 .claude/settings.json，不覆蓋使用者原本的設定。"""
    settings_path = os.path.join(target, ".claude", "settings.json")
    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, encoding="utf-8") as f:
                settings = json.load(f)
        except (OSError, json.JSONDecodeError):
            res["notes"].append("既有 .claude/settings.json 不是合法 JSON，沒有加上開工自動健檢；修好後重跑 install。")
            return
    hook_path = os.path.join(target, HOOK_REL)
    os.makedirs(os.path.dirname(hook_path), exist_ok=True)
    with open(hook_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(HOOK_SCRIPT)
    os.chmod(hook_path, 0o755)
    groups = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
    already = any(h.get("command") == HOOK_CMD for g in groups for h in g.get("hooks", []))
    if not already:
        groups.append({"hooks": [{"type": "command", "command": HOOK_CMD}]})
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
            f.write("\n")
        res["notes"].append("已加上開工自動健檢（.claude/hooks/skl-session-start.sh）：每次開對話會先回報問題數量。")


def install(target, pack_names, force=False, dry_run=False, write_claude_md=True,
            source_root=REPO_ROOT, packs=None, extra_skills=(), scan=True, auditor=None,
            with_hook=True):
    """回傳 (結果 dict, 結束碼)。失敗不中斷，逐一記錄，最後一起報。"""
    packs = packs if packs is not None else load_packs()
    unknown = [p for p in pack_names if p not in packs]
    if unknown:
        return {"error": f"不認識的組合：{', '.join(unknown)}。可用：{', '.join(packs)}"}, 2
    if not os.path.isdir(target):
        return {"error": f"找不到目標資料夾：{target}"}, 2

    # 單選技能（網頁勾選產生的）：只接受 repo 內的相對路徑，擋掉 .. 與絕對路徑，
    # 免得貼錯的指令把 repo 外的資料夾複製進目標。
    bad = [s for s in extra_skills
           if os.path.isabs(s) or ".." in s.replace("\\", "/").split("/")]
    if bad:
        return {"error": f"技能路徑不合法：{', '.join(bad)}。請用網頁複製的路徑，例如 engineering/skills/focused-fix"}, 2

    wanted = list(ALWAYS)
    for s in extra_skills:
        if s.strip("/") not in wanted:
            wanted.append(s.strip("/"))
    for p in pack_names:
        for s in packs[p]["skills"]:
            if s not in wanted:
                wanted.append(s)

    skills_root = os.path.join(target, ".claude", "skills")
    manifest = _read_manifest(skills_root) or {"skills": []}
    owned = set(manifest.get("skills", []))
    res = {"installed": [], "updated": [], "skipped": [], "failed": [], "notes": [], "warnings": []}

    for rel in wanted:
        src = os.path.join(source_root, rel)
        name = os.path.basename(rel.rstrip("/"))
        dst = os.path.join(skills_root, name)
        if not os.path.isfile(os.path.join(src, "SKILL.md")):
            res["failed"].append((name, f"來源不存在或缺 SKILL.md：{rel}"))
            continue
        exists = os.path.exists(dst)
        # 同名但不是我們裝的：那是這個 App 自己的技能，除非明確 --force 否則不碰。
        if exists and name not in owned and not force:
            res["skipped"].append((name, "目標已有同名技能且不是本工具安裝的；要覆蓋請加 --force"))
            continue
        if scan:
            verdict, detail = security_scan(src, source_root, auditor)
            if verdict in ("FAIL", "ERROR"):
                res["failed"].append((name, f"安全掃描未通過（{verdict}：{detail}），沒有安裝。"
                                            "確認安全後可加 --no-security-scan 強制安裝"))
                continue
            if verdict == "WARN":
                res["warnings"].append((name, detail))
        if dry_run:
            (res["updated"] if exists else res["installed"]).append(name)
            continue
        try:
            os.makedirs(skills_root, exist_ok=True)
            # 先複製到暫存資料夾再換上去：複製到一半失敗時，舊版技能還在，不會留下半套。
            tmp = dst + ".skl-tmp"
            if os.path.exists(tmp):
                shutil.rmtree(tmp)
            shutil.copytree(src, tmp, ignore=_ignore)
            if exists:
                shutil.rmtree(dst)
            os.replace(tmp, dst)
            (res["updated"] if exists else res["installed"]).append(name)
            owned.add(name)
        except OSError as e:
            res["failed"].append((name, f"複製失敗：{e}"))

    if write_claude_md:
        cm = os.path.join(target, "CLAUDE.md")
        if os.path.exists(cm):
            res["notes"].append("已有 CLAUDE.md，沒有覆蓋。要套用工作守則，請把範本內容合併進去："
                                "xin-toolkit/skills/app-bootstrap/assets/CLAUDE.template.md")
        elif not dry_run:
            shutil.copyfile(CLAUDE_TEMPLATE, cm)
            res["notes"].append("已建立 CLAUDE.md（工作守則範本）。記得填第 7 節「本專案資訊」；"
                                "不是 PWA + GitHub Actions 架構就刪掉第 6 節。")

    if with_hook and not dry_run and (res["installed"] or res["updated"]):
        install_hook(target, res)

    if not dry_run and (res["installed"] or res["updated"]):
        manifest.update({
            "source_repo": "https://github.com/xin7355-collab/skl",
            "source_commit": _source_commit(source_root),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "packs": sorted(set(manifest.get("packs", [])) | set(pack_names)),
            "extra_skills": sorted(set(manifest.get("extra_skills", [])) | {s.strip("/") for s in extra_skills}),
            "skills": sorted(owned),
        })
        with open(os.path.join(skills_root, MANIFEST), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return res, (1 if res["failed"] else 0)


def update(target, dry_run=False, source_root=REPO_ROOT, packs=None, scan=True, auditor=None,
           with_hook=True):
    m = _read_manifest(os.path.join(target, ".claude", "skills"))
    if not m:
        return {"error": "這個 repo 還沒用本工具裝過技能（找不到 .claude/skills/.skl-vendor.json），請先 install。"}, 2
    return install(target, m.get("packs", []), force=False, dry_run=dry_run,
                   write_claude_md=False, source_root=source_root, packs=packs,
                   extra_skills=m.get("extra_skills", []), scan=scan, auditor=auditor,
                   with_hook=with_hook)


def _print(res, dry_run):
    if "error" in res:
        print(res["error"], file=sys.stderr)
        return
    verb = "（試跑，未實際寫入）" if dry_run else ""
    print(f"新裝 {len(res['installed'])}、更新 {len(res['updated'])}、略過 {len(res['skipped'])}、失敗 {len(res['failed'])}{verb}")
    for n in res["installed"]:
        print(f"  + {n}")
    for n in res["updated"]:
        print(f"  ↻ {n}")
    for n, why in res["skipped"]:
        print(f"  – {n}：{why}")
    for n, why in res["failed"]:
        print(f"  ✗ {n}：{why}")
    for n, why in res.get("warnings", []):
        print(f"  ⚠ {n}：安全掃描提醒（{why}）。有連網或讀寫檔案的技能常見，已安裝，建議看一下用途是否合理")
    for note in res["notes"]:
        print(f"※ {note}")
    if res["installed"] or res["updated"]:
        print("下一步：git add .claude CLAUDE.md && git commit，之後在這個 repo 開的工作階段就會自動載入這些技能。")


def selftest():
    ok = True

    def check(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    packs = load_packs()
    check(set(packs) >= {"core", "pwa", "actions", "backend", "security"}, "packs.json 可讀且有五組")
    all_paths = sorted({s for p in packs.values() for s in p["skills"]} | set(ALWAYS))
    missing = [s for s in all_paths if not os.path.isfile(os.path.join(REPO_ROOT, s, "SKILL.md"))]
    check(not missing, "packs.json 裡每個技能路徑都存在" + (f"（缺：{missing}）" if missing else ""))

    with tempfile.TemporaryDirectory() as d:
        # 自己造一個小來源庫，測試不依賴真實技能內容。
        src = os.path.join(d, "src")
        for rel in ["xin-toolkit/skills/app-guardrails-audit", "a/skills/alpha", "a/skills/beta"]:
            os.makedirs(os.path.join(src, rel, "scripts", "__pycache__"))
            with open(os.path.join(src, rel, "SKILL.md"), "w") as f:
                f.write("---\nname: x\ndescription: y\n---\n")
            open(os.path.join(src, rel, "scripts", "__pycache__", "junk.pyc"), "w").close()
        with open(os.path.join(src, "a/skills/beta/big.bin"), "wb") as f:
            f.write(b"0" * (MAX_FILE_BYTES + 1))
        # hook 測試要真的跑健檢，所以放入真正的掃描腳本。
        shutil.copyfile(os.path.join(REPO_ROOT, "xin-toolkit/skills/app-guardrails-audit/scripts/guardrails_audit.py"),
                        os.path.join(src, "xin-toolkit/skills/app-guardrails-audit/scripts/guardrails_audit.py"))
        # 假的安全掃描工具：名稱含 evil 判 FAIL、含 beta 判 WARN，其餘 PASS。
        fa = os.path.join(src, AUDITOR_REL)
        os.makedirs(os.path.dirname(fa), exist_ok=True)
        with open(fa, "w") as f:
            f.write("import json,sys,os\nn=os.path.basename(sys.argv[1].rstrip('/'))\n"
                    "v='FAIL' if 'evil' in n else ('WARN' if 'beta' in n else 'PASS')\n"
                    "print(json.dumps({'verdict':v,'findings':[{'category':'NET-EXFIL'}] if v!='PASS' else []}))\n")
        for rel in ["a/skills/evil"]:
            os.makedirs(os.path.join(src, rel))
            with open(os.path.join(src, rel, "SKILL.md"), "w") as f:
                f.write("---\nname: evil\ndescription: y\n---\n")
        fake = {"one": {"desc": "", "skills": ["a/skills/alpha", "a/skills/beta"]},
                "bad": {"desc": "", "skills": ["a/skills/ghost"]}}
        tgt = os.path.join(d, "app")
        os.makedirs(os.path.join(tgt, ".claude", "skills", "beta"))  # App 自己的同名技能

        r, code = install(tgt, ["nope"], source_root=src, packs=fake)
        check(code == 2 and "不認識" in r["error"], "未知組合回傳參數錯誤")

        r, code = install(tgt, ["one"], dry_run=True, source_root=src, packs=fake)
        check(code == 0 and not os.path.exists(os.path.join(tgt, ".claude/skills/alpha")), "試跑不寫入")

        r, code = install(tgt, ["one"], source_root=src, packs=fake)
        sk = os.path.join(tgt, ".claude", "skills")
        check(code == 0 and os.path.isfile(os.path.join(sk, "alpha", "SKILL.md")), "技能被複製進 .claude/skills")
        check(os.path.isfile(os.path.join(sk, "app-guardrails-audit", "SKILL.md")), "防禦掃描技能一律安裝")
        check(any(n == "beta" for n, _ in r["skipped"]), "App 自己的同名技能不被覆蓋")
        check(not os.path.exists(os.path.join(sk, "alpha", "scripts", "__pycache__")), "__pycache__ 不複製")
        check(os.path.isfile(os.path.join(tgt, "CLAUDE.md")), "沒有 CLAUDE.md 時建立範本")
        m = _read_manifest(sk)
        check(m and "alpha" in m["skills"] and "beta" not in m["skills"], "manifest 只記錄本工具裝的技能")

        with open(os.path.join(tgt, "CLAUDE.md"), "w") as f:
            f.write("我的規則")
        r, code = update(tgt, source_root=src, packs=fake)
        check(code == 0 and "alpha" in r["updated"], "update 會重新複製已裝技能")
        with open(os.path.join(tgt, "CLAUDE.md")) as f:
            check(f.read() == "我的規則", "update 不動既有 CLAUDE.md")

        r, code = install(tgt, ["one"], force=True, source_root=src, packs=fake)
        check("beta" in r["updated"] and not os.path.exists(os.path.join(sk, "beta", "big.bin")),
              "--force 覆蓋同名技能，且大檔不複製")

        r, code = install(tgt, ["bad"], source_root=src, packs=fake)
        check(code == 1 and r["failed"] and r["failed"][0][0] == "ghost", "來源缺失時結束碼 1 並列出是哪個")

        r, code = install(tgt, [], source_root=src, packs=fake, extra_skills=["../evil"])
        check(code == 2 and "不合法" in r["error"], "--skills 擋掉 .. 路徑")
        tgt2 = os.path.join(d, "app2")
        os.makedirs(tgt2)
        r, code = install(tgt2, [], source_root=src, packs=fake, extra_skills=["a/skills/alpha/"])
        check(code == 0 and r["installed"] == ["app-guardrails-audit", "alpha"], "--skills 單選技能可安裝")
        r, code = update(tgt2, source_root=src, packs=fake)
        check(code == 0 and "alpha" in r["updated"], "update 會帶上單選技能")

        r, code = update(os.path.join(d, "src"), source_root=src, packs=fake)
        check(code == 2, "沒裝過就 update 會提示先 install")

        # 安全掃描
        tgt3 = os.path.join(d, "app3")
        os.makedirs(os.path.join(tgt3, ".claude"))
        with open(os.path.join(tgt3, ".claude", "settings.json"), "w") as f:
            json.dump({"permissions": {"allow": ["Bash(ls)"]}}, f)
        r, code = install(tgt3, ["one"], source_root=src, packs=fake, extra_skills=["a/skills/evil"])
        check(code == 1 and any(n == "evil" and "FAIL" in w for n, w in r["failed"]), "安全掃描 FAIL 的技能不安裝")
        check(not os.path.exists(os.path.join(tgt3, ".claude/skills/evil")), "FAIL 的技能確實沒有被複製")
        check(any(n == "beta" for n, _ in r["warnings"]) and "beta" in r["installed"], "WARN 的技能照裝但列出提醒")
        r2, _ = install(tgt3, [], source_root=src, packs=fake, extra_skills=["a/skills/evil"], scan=False)
        check("evil" in r2["installed"], "--no-security-scan 可強制安裝")
        r3, code3 = install(os.path.join(d, "app2"), ["one"], source_root=src, packs=fake,
                            auditor=os.path.join(d, "nope.py"))
        check(code3 == 1 and all("ERROR" in w for _, w in r3["failed"]), "找不到掃描工具時一律不安裝（fail closed）")

        # 開工自動健檢 hook
        with open(os.path.join(tgt3, ".claude", "settings.json")) as f:
            st = json.load(f)
        cmds = [h["command"] for g in st["hooks"]["SessionStart"] for h in g["hooks"]]
        check(st["permissions"]["allow"] == ["Bash(ls)"] and cmds == [HOOK_CMD], "hook 合併進既有 settings.json，原設定保留")
        check(os.access(os.path.join(tgt3, HOOK_REL), os.X_OK), "hook 腳本可執行")
        install(tgt3, ["one"], source_root=src, packs=fake)
        with open(os.path.join(tgt3, ".claude", "settings.json")) as f:
            st = json.load(f)
        check(len(st["hooks"]["SessionStart"]) == 1, "重複安裝不會重複加 hook")
        tgt4 = os.path.join(d, "app4")
        os.makedirs(os.path.join(tgt4, ".claude"))
        with open(os.path.join(tgt4, ".claude", "settings.json"), "w") as f:
            f.write("{壞掉")
        r, _ = install(tgt4, ["one"], source_root=src, packs=fake)
        with open(os.path.join(tgt4, ".claude", "settings.json")) as f:
            check(f.read() == "{壞掉" and any("不是合法 JSON" in n for n in r["notes"]),
                  "settings.json 壞掉時不覆蓋並提示")
        hp = os.path.join(tgt3, HOOK_REL)
        out = subprocess.run(["bash", hp], capture_output=True, text=True, timeout=120,
                             env={**os.environ, "CLAUDE_PROJECT_DIR": tgt3})
        check(out.returncode == 0 and "[skl 健檢]" in out.stdout, "hook 實際執行會輸出健檢摘要且不擋開工")

    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="把 skl 技能複製進其他 App 的 repo")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="列出技能組合")
    i = sub.add_parser("install", help="安裝技能組合")
    i.add_argument("--target", default=".", help="目標 App 的 repo 根目錄（預設目前資料夾）")
    i.add_argument("--packs", default=None, help="逗號分隔，例如 core,pwa,actions（沒給 --skills 時預設 core）")
    i.add_argument("--skills", default="", help="逗號分隔的技能路徑，例如 engineering/skills/focused-fix")
    i.add_argument("--force", action="store_true", help="覆蓋目標裡同名、但不是本工具裝的技能")
    i.add_argument("--dry-run", action="store_true", help="只列出會做什麼，不寫入")
    i.add_argument("--no-claude-md", action="store_true", help="不建立 CLAUDE.md 範本")
    i.add_argument("--no-security-scan", action="store_true", help="跳過安裝前的安全掃描（確認來源可信才用）")
    i.add_argument("--no-hook", action="store_true", help="不加開工自動健檢")
    u = sub.add_parser("update", help="把已裝的技能更新成 skl 最新版")
    u.add_argument("--target", default=".")
    u.add_argument("--dry-run", action="store_true")
    u.add_argument("--no-security-scan", action="store_true")
    u.add_argument("--no-hook", action="store_true")
    sub.add_parser("selftest", help="離線自我測試")
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "list":
        for name, p in load_packs().items():
            print(f"{name:9} {p['desc']}")
            for s in p["skills"]:
                print(f"          - {os.path.basename(s)}")
        print("（app-guardrails-audit 每次都會裝）")
        return 0
    if args.cmd == "install":
        skills = [x.strip() for x in args.skills.split(",") if x.strip()]
        packs_arg = args.packs if args.packs is not None else ("" if skills else "core")
        packs = [p.strip() for p in packs_arg.split(",") if p.strip()]
        res, code = install(os.path.abspath(args.target), packs, args.force, args.dry_run,
                            not args.no_claude_md, extra_skills=skills,
                            scan=not args.no_security_scan, with_hook=not args.no_hook)
    elif args.cmd == "update":
        res, code = update(os.path.abspath(args.target), args.dry_run,
                           scan=not args.no_security_scan, with_hook=not args.no_hook)
    else:
        ap.print_help()
        return 2
    _print(res, getattr(args, "dry_run", False))
    return code


if __name__ == "__main__":
    sys.exit(main())
