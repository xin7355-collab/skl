#!/usr/bin/env python3
"""skl_skill_lint.py — skl 自己加的技能格式檢查（只警告、不擋 CI）。

為什麼另寫一支、不改 scripts/check_frontmatter.py 與 check_paths.py：那兩支是上游 claude-skills 的腳本，
照原樣保留才方便同步上游。這裡只補上游沒檢查、但官方 Agent Skills 規格有寫的項目：

  - name-mismatch     frontmatter 的 name 和資料夾名不一樣（Claude 用 name 叫技能，資料夾名不同會讓人找錯檔）
  - desc-too-long     description 超過 1,024 字元（規格上限；超過的部分可能被截掉）
  - skill-md-too-long SKILL.md 超過 500 行（官方建議：太長的細節拆到 references/，用到才讀，省用量）
  - orphan-reference  （--orphans）references/ 裡的檔案沒被 SKILL.md 或其他參考檔提到：
                      Claude 根本不知道有這個檔，等於死檔。只建議對 xin-toolkit/ 跑，上游技能不改。

用法：
  python3 scripts/skl_skill_lint.py --all                 # 全庫（略過 docs、.claude 等複本資料夾）
  python3 scripts/skl_skill_lint.py --orphans xin-toolkit # 指定資料夾，加查孤兒參考檔
  python3 scripts/skl_skill_lint.py --all --json
  python3 scripts/skl_skill_lint.py selftest

結束碼：0 = 檢查完成（有警告也是 0，除非加 --strict）；1 = --strict 且有警告；2 = 參數錯誤。
在 GitHub Actions 裡會輸出 ::warning 註記，警告會顯示在執行頁面上，但不讓 CI 失敗。
"""

import argparse
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 這些資料夾是複本或不是技能（docs 是網頁產生的說明、.claude 是 vendored 複本），掃了只會重複報。
EXCLUDED_DIRS = {".git", ".codex", ".gemini", ".hermes", ".vibe", "docs", "audit", "node_modules",
                 ".github", ".claude-plugin", ".claude", "__pycache__"}
DESC_MAX = 1024
LINES_MAX = 500
FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def frontmatter(text):
    """回傳 frontmatter dict；沒有或壞掉回 None（格式錯誤由上游 check_frontmatter.py 負責報）。"""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        import yaml
        data = yaml.safe_load(m.group(1))
        return data if isinstance(data, dict) else None
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 — YAML 壞掉不是本工具要報的問題
        return None
    # 沒有 PyYAML 時的退路：只讀單層 key: value 與縮排續行（夠用來比對 name、量 description 長度）
    out, key = {}, None
    for line in m.group(1).splitlines():
        if line and not line[0].isspace() and ":" in line:
            key, _, v = line.partition(":")
            key, v = key.strip(), v.strip()
            out[key] = "" if v in (">", "|", ">-", "|-") else v.strip("\"'")
        elif key and line.strip():
            out[key] = (str(out[key]) + " " + line.strip()).strip()
    return out


def find_skills(paths):
    seen = []
    for base in paths:
        if os.path.isfile(base) and os.path.basename(base) == "SKILL.md":
            seen.append(os.path.abspath(base))
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
            if "SKILL.md" in filenames:
                seen.append(os.path.abspath(os.path.join(dirpath, "SKILL.md")))
    return sorted(set(seen))


def orphan_refs(skill_dir):
    """references/ 底下沒被 SKILL.md 或其他參考檔提到的檔案（相對技能資料夾的路徑）。"""
    ref_root = os.path.join(skill_dir, "references")
    if not os.path.isdir(ref_root):
        return []
    refs = []
    for dirpath, dirnames, filenames in os.walk(ref_root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        refs += [os.path.join(dirpath, n) for n in filenames if n != ".DS_Store"]

    def read(p):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""
    texts = {p: read(p) for p in refs}
    skill_text = read(os.path.join(skill_dir, "SKILL.md"))
    out = []
    for p in sorted(refs):
        rel = os.path.relpath(p, skill_dir).replace(os.sep, "/")
        name = os.path.basename(p)
        # 提到相對路徑、或只寫檔名（同層參考檔常這樣互相連結）都算有被引用
        needles = {name, rel, os.path.relpath(p, ref_root).replace(os.sep, "/")}
        corpus = [skill_text] + [t for q, t in texts.items() if q != p]
        if not any(n in t for n in needles for t in corpus):
            out.append(rel)
    return out


def lint(skill_md, root, orphans=False):
    """回傳 [(規則, 檔案相對路徑, 行號, 說明)]。"""
    rel = os.path.relpath(skill_md, root).replace(os.sep, "/")
    folder = os.path.basename(os.path.dirname(skill_md))
    with open(skill_md, encoding="utf-8", errors="replace") as f:
        text = f.read()
    out = []
    fm = frontmatter(text)
    if fm is not None:
        name = fm.get("name")
        if isinstance(name, str) and name.strip() and name.strip() != folder:
            out.append(("name-mismatch", rel, 1,
                        f"frontmatter name「{name.strip()}」和資料夾名「{folder}」不一樣；改成一致，免得找錯技能"))
        desc = fm.get("description")
        if isinstance(desc, str) and len(desc) > DESC_MAX:
            out.append(("desc-too-long", rel, 1,
                        f"description 有 {len(desc):,} 字元，超過規格上限 {DESC_MAX:,}；把細節移到內文或 when_to_use"))
    n_lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    if n_lines > LINES_MAX:
        out.append(("skill-md-too-long", rel, LINES_MAX + 1,
                    f"SKILL.md 有 {n_lines} 行，超過建議的 {LINES_MAX} 行；把細節拆到 references/，用到才讀"))
    if orphans:
        skill_dir = os.path.dirname(skill_md)
        for r in orphan_refs(skill_dir):
            out.append(("orphan-reference", os.path.relpath(os.path.join(skill_dir, r), root).replace(os.sep, "/"), 1,
                        "這個參考檔沒有被 SKILL.md 或其他參考檔提到，Claude 不會知道要讀它；在 SKILL.md 寫何時讀，或刪掉"))
    return out


def run(paths, root, orphans=False):
    findings = []
    skills = find_skills(paths)
    for md in skills:
        findings += lint(md, root, orphans)
    return skills, findings


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
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)

        w("good/SKILL.md", "---\nname: good\ndescription: Short.\n---\n讀 [細節](references/detail.md)。\n")
        w("good/references/detail.md", "也看 more.md\n")
        w("good/references/more.md", "x\n")
        w("good/references/sub/deep.md", "x\n")          # 沒人提到 → 孤兒
        w("good/references/lonely.md", "x\n")            # 沒人提到 → 孤兒
        w("renamed/SKILL.md", "---\nname: other-name\ndescription: y\n---\n")
        w("long-desc/SKILL.md", "---\nname: long-desc\ndescription: >\n  " + "字" * (DESC_MAX + 1) + "\n---\n")
        w("edge-desc/SKILL.md", "---\nname: edge-desc\ndescription: " + "a" * DESC_MAX + "\n---\n")
        w("long-md/SKILL.md", "---\nname: long-md\ndescription: y\n---\n" + "行\n" * (LINES_MAX - 3))
        w("edge-md/SKILL.md", "---\nname: edge-md\ndescription: y\n---\n" + "行\n" * (LINES_MAX - 4))
        w("no-fm/SKILL.md", "# 沒有 frontmatter\n")
        w("docs/copy/SKILL.md", "---\nname: wrong\ndescription: y\n---\n")   # docs 是複本，不掃
        w("no-refs/SKILL.md", "---\nname: no-refs\ndescription: y\n---\n")

        skills, f = run([d], d)
        rules = {(r, p) for r, p, _, _ in f}
        check(not any(p.startswith("docs/") for p in (os.path.relpath(s, d) for s in skills)), "docs/ 底下的複本不掃")
        check(("name-mismatch", "renamed/SKILL.md") in rules, "name 和資料夾名不同 → 警告")
        check(not any(r == "name-mismatch" and p != "renamed/SKILL.md" for r, p in rules), "name 一致的不報")
        check(("desc-too-long", "long-desc/SKILL.md") in rules, "description 超過 1,024 字元（多行寫法、中文）→ 警告")
        check(("desc-too-long", "edge-desc/SKILL.md") not in rules, "剛好 1,024 字元不報")
        check(("skill-md-too-long", "long-md/SKILL.md") in rules, "SKILL.md 501 行 → 警告")
        check(("skill-md-too-long", "edge-md/SKILL.md") not in rules, "剛好 500 行不報")
        check(not any(p == "no-fm/SKILL.md" for _, p in rules), "沒有 frontmatter 的交給上游檢查，這裡不重複報")
        check(not any(r == "orphan-reference" for r, _ in rules), "沒加 --orphans 時不查孤兒檔")
        _, f = run([os.path.join(d, "good"), os.path.join(d, "no-refs")], d, orphans=True)
        orph = sorted(p for r, p, _, _ in f if r == "orphan-reference")
        check(orph == ["good/references/lonely.md", "good/references/sub/deep.md"],
              f"--orphans：SKILL.md 與同層參考檔提到的不算，沒人提到的才列（實際：{orph}）")
        code = main(["--orphans", os.path.join(d, "good")], root=d, out=open(os.devnull, "w"))
        check(code == 0, "有警告結束碼仍是 0（不擋 CI）")
        code = main(["--strict", os.path.join(d, "good"), "--orphans"], root=d, out=open(os.devnull, "w"))
        check(code == 1, "--strict 有警告時結束碼 1")
        code = main([os.path.join(d, "nope")], root=d, out=open(os.devnull, "w"))
        check(code == 2, "路徑不存在 → 結束碼 2")

    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv=None, root=ROOT, out=sys.stdout):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description="skl 技能格式檢查（只警告）")
    ap.add_argument("paths", nargs="*", help="要檢查的資料夾或 SKILL.md")
    ap.add_argument("--all", action="store_true", help="檢查整個 repo")
    ap.add_argument("--orphans", action="store_true", help="加查 references/ 裡沒被提到的檔案（建議只對 xin-toolkit/ 跑）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="有警告就結束碼 1（本機自查用；CI 不加）")
    args = ap.parse_args(argv)
    paths = [root] if args.all else args.paths
    if not paths:
        ap.print_help(out)
        return 2
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        print(f"找不到：{', '.join(missing)}。請給技能資料夾或 SKILL.md 的路徑，或用 --all。", file=sys.stderr)
        return 2
    skills, findings = run(paths, root, args.orphans)
    if args.json:
        print(json.dumps({"skills": len(skills), "warnings": [
            {"rule": r, "file": p, "line": ln, "message": m} for r, p, ln, m in findings]},
            ensure_ascii=False, indent=2), file=out)
    else:
        gha = os.environ.get("GITHUB_ACTIONS") == "true"
        for r, p, ln, m in findings:
            print(f"::warning file={p},line={ln}::[{r}] {m}" if gha else f"⚠ [{r}] {p}:{ln} {m}", file=out)
        counts = {}
        for r, *_ in findings:
            counts[r] = counts.get(r, 0) + 1
        summary = "、".join(f"{k} {v}" for k, v in sorted(counts.items())) or "沒有警告"
        print(f"檢查 {len(skills)} 個技能：{summary}（只是提醒，不會讓 CI 失敗）", file=out)
    return 1 if (args.strict and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
