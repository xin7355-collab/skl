#!/usr/bin/env python3
"""build_catalog.py — 掃描全部 SKILL.md，產生前端網頁用的 web/catalog.json。

為什麼另外做中文對照檔（web/zh.json）而不是直接翻譯 SKILL.md：
技能本體維持上游英文，才能日後同步上游、也不會因翻譯誤差改壞指令；
網頁只負責「讓人看懂、好挑選」，所以中文只放在顯示層。

用法：
  python3 web/build_catalog.py            # 產生 web/catalog.json
  python3 web/build_catalog.py --missing  # 列出還沒有中文說明的技能路徑（JSON）
  python3 web/build_catalog.py selftest

只用標準函式庫；逐檔只讀 frontmatter 區塊，不把整份 SKILL.md 讀進記憶體。
"""

import argparse
import json
import os
import re
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "web", "catalog.json")
ZH = os.path.join(ROOT, "web", "zh.json")
PACKS = os.path.join(ROOT, "xin-toolkit", "skills", "app-bootstrap", "assets", "packs.json")

# 這些是給其他 AI 工具用的鏡像副本或文件，不是技能本體，列進來會重複。
SKIP_TOP = {".git", ".gemini", ".codex", ".vibe", ".hermes", "docs", "audit", "node_modules", "site"}

DOMAIN_ZH = {
    "engineering": "進階工程", "engineering-team": "工程團隊", "marketing-skill": "行銷",
    "marketing": "行銷", "c-level-advisor": "高階主管顧問", "c-level-agents": "高階主管角色",
    "ra-qm-team": "法規與品質", "product-team": "產品", "productivity": "個人生產力",
    "research": "學術研究", "project-management": "專案管理", "compliance-os": "法遵",
    "commercial": "商務", "business-operations": "營運", "agent-launcher": "代理啟動器",
    "research-ops": "研究營運", "markdown-html": "Markdown 轉網頁", "finance": "財務",
    "business-growth": "業務成長", "xin-toolkit": "自製工具包", "loop-library": "迴圈庫",
    "standards": "標準",
}


def read_frontmatter(path):
    """回傳 frontmatter 的 dict（只取簡單的 key: value 與 > | 區塊）；沒有就回 {}。"""
    lines = []
    with open(path, encoding="utf-8", errors="replace") as f:
        first = f.readline()
        if first.strip() != "---":
            return {}
        for line in f:
            if line.strip() == "---":
                break
            lines.append(line.rstrip("\n"))
            if len(lines) > 400:  # 壞掉的 frontmatter 沒有結尾，不要一路讀到檔尾。
                break
    data, key, block = {}, None, []

    def flush():
        if key is not None and block is not None:
            data[key] = " ".join(s.strip() for s in block if s.strip())

    for line in lines:
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            flush()
            key, val = m.group(1), m.group(2).strip()
            if val in (">", "|", ">-", "|-", ">+", "|+"):
                block = []
            else:
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                data[key] = val
                key, block = None, None
        elif key is not None and block is not None:
            block.append(line)
    flush()
    return data


def collect(root=ROOT):
    skills, problems = [], []
    for top in sorted(os.listdir(root)):
        if top in SKIP_TOP or top.startswith(".") or not os.path.isdir(os.path.join(root, top)):
            continue
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, top)):
            dirnames[:] = sorted(d for d in dirnames if d not in {"__pycache__", "node_modules"})
            if "SKILL.md" not in filenames:
                continue
            rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
            try:
                fm = read_frontmatter(os.path.join(dirpath, "SKILL.md"))
            except OSError as e:
                problems.append((rel, str(e)))
                continue
            desc = fm.get("description", "")
            if not desc:
                problems.append((rel, "沒有 description"))
            skills.append({"path": rel, "name": fm.get("name") or os.path.basename(rel),
                           "domain": top, "desc": desc[:600]})
    return skills, problems


def build(root=ROOT, zh_path=ZH, packs_path=PACKS):
    skills, problems = collect(root)
    zh = {}
    if os.path.isfile(zh_path):
        with open(zh_path, encoding="utf-8") as f:
            zh = json.load(f)
    with open(packs_path, encoding="utf-8") as f:
        packs = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    for s in skills:
        s["zh"] = zh.get(s["path"], "")
    missing = [s["path"] for s in skills if not s["zh"]]
    domains = sorted({s["domain"] for s in skills})
    catalog = {
        "skills": skills,
        "packs": packs,
        "domains": [{"id": d, "zh": DOMAIN_ZH.get(d, d),
                     "count": sum(1 for s in skills if s["domain"] == d)} for d in domains],
    }
    return catalog, problems, missing


def selftest():
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

        w("eng/skills/a/SKILL.md", "---\nname: a\ndescription: \"plain one\"\n---\nbody\n")
        w("eng/skills/b/SKILL.md", "---\nname: b\ndescription: >\n  folded\n  text here\nlicense: MIT\n---\n")
        w("eng/skills/c/SKILL.md", "no frontmatter\n")
        w("eng/skills/d/SKILL.md", "---\nname: d\n" + "x: y\n" * 1000)  # 沒有結尾的 frontmatter
        w(".gemini/skills/a/SKILL.md", "---\nname: dup\ndescription: mirror\n---\n")
        w("docs/skills/z/SKILL.md", "---\nname: z\ndescription: doc\n---\n")
        w("packs.json", json.dumps({"_說明": "x", "core": {"desc": "d", "skills": ["eng/skills/a"]}}))
        w("zh.json", json.dumps({"eng/skills/a": "中文一"}))

        cat, problems, missing = build(d, os.path.join(d, "zh.json"), os.path.join(d, "packs.json"))
        by = {s["path"]: s for s in cat["skills"]}
        check(set(by) == {"eng/skills/a", "eng/skills/b", "eng/skills/c", "eng/skills/d"}, "略過鏡像與 docs，只收技能本體")
        check(by["eng/skills/a"]["desc"] == "plain one", "引號包住的 description 會去掉引號")
        check(by["eng/skills/b"]["desc"] == "folded text here", "> 摺疊區塊會接成一行")
        check(by["eng/skills/a"]["zh"] == "中文一" and "eng/skills/b" in missing, "中文對照有就帶入、沒有就列入缺少清單")
        check({p for p, _ in problems} >= {"eng/skills/c", "eng/skills/d"}, "缺 description 的技能會被列出，不會默默消失")
        check(list(cat["packs"]) == ["core"], "packs 去掉說明欄位")
        check(cat["domains"][0]["count"] == 4, "領域計數正確")

    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="產生網頁用技能目錄")
    ap.add_argument("cmd", nargs="?", default="build", choices=["build", "selftest"])
    ap.add_argument("--missing", action="store_true", help="只列出缺中文說明的技能")
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    cat, problems, missing = build()
    if args.missing:
        print(json.dumps([{"path": s["path"], "desc": s["desc"]} for s in cat["skills"] if not s["zh"]],
                         ensure_ascii=False, indent=1))
        return 0
    if not cat["skills"]:
        print("一個技能都沒找到，不產生空的 catalog.json。請確認是在 skl repo 裡執行。", file=sys.stderr)
        return 1
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, separators=(",", ":"))
    print(f"已產生 web/catalog.json：{len(cat['skills'])} 個技能、{len(cat['domains'])} 個領域；"
          f"缺中文說明 {len(missing)} 個；frontmatter 有問題 {len(problems)} 個。")
    for p, why in problems[:20]:
        print(f"  ! {p}：{why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
