#!/usr/bin/env python3
"""檢查 App 點子頁的資料（web/ideas.json）。

為什麼要有這支：點子頁的「開工指令」會叫 Claude 用 app-bootstrap 裝技能組合，組合名寫錯就整段裝不起來；
費用寫「免費／付費」沒附官方出處，使用者就沒辦法自己查證（CLAUDE.md：費用與規定附官方出處）。

用法：python3 web/check_ideas.py            檢查 web/ideas.json，有問題 exit 1
      python3 web/check_ideas.py selftest   離線自我測試（含故意寫錯的資料，必須抓到）
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IDEAS = os.path.join(HERE, "ideas.json")
PACKS = os.path.join(ROOT, "xin-toolkit", "skills", "app-bootstrap", "assets", "packs.json")
LEVELS = ("小", "中", "大")
NEED = ("id", "emoji", "title", "cat", "level", "one", "why", "stack", "mvp", "next", "notes", "packs", "cost", "xq")


def check(d, packs, web_dir=HERE):
    """回傳問題清單（空的 = 通過）。"""
    errs = []
    srcs = d.get("srcs") or {}
    for k, s in srcs.items():
        url = (s or {}).get("url", "")
        if url.startswith("https://"):
            continue
        if url.endswith(".html") and "/" not in url and os.path.exists(os.path.join(web_dir, url)):
            continue          # 站內頁（例如 ai.html）
        errs.append(f"出處 {k}：網址要是 https:// 或本站存在的 .html（{url!r}）")
    cats = d.get("cats") or []
    if not d.get("checked_at"):
        errs.append("少了 checked_at（費用查證日）")
    if not d.get("steps"):
        errs.append("少了 steps（開工步驟）")
    seen = set()
    ideas = d.get("ideas") or []
    if len(ideas) < 3:
        errs.append(f"點子只有 {len(ideas)} 個")
    for it in ideas:
        tag = it.get("id") or it.get("title") or "?"
        for f in NEED:
            if not it.get(f):
                errs.append(f"{tag}：少了 {f}")
        if it.get("id") in seen:
            errs.append(f"{tag}：id 重複")
        seen.add(it.get("id"))
        if it.get("cat") and it["cat"] not in cats:
            errs.append(f"{tag}：類別 {it['cat']} 不在 cats")
        if it.get("level") and it["level"] not in LEVELS:
            errs.append(f"{tag}：難度要是 小／中／大（{it['level']}）")
        for p in it.get("packs") or []:
            if p not in packs:
                errs.append(f"{tag}：技能組合 {p} 不存在（packs.json 沒有）")
        for f in ("mvp", "next", "notes"):
            if it.get(f) is not None and not isinstance(it[f], list):
                errs.append(f"{tag}：{f} 要是清單")
        cost = it.get("cost") or {}
        src = cost.get("src") or []
        for k in src:
            if k not in srcs:
                errs.append(f"{tag}：費用出處 {k} 不在 srcs")
        text = cost.get("text", "")
        if any(w in text for w in ("免費", "付費", "US$", "每月", "每天")) and not src:
            errs.append(f"{tag}：費用寫了「{text[:20]}…」卻沒附官方出處")
    return errs


def load():
    with open(IDEAS, encoding="utf-8") as f:
        d = json.load(f)
    with open(PACKS, encoding="utf-8") as f:
        packs = {k for k in json.load(f) if not k.startswith("_")}
    return d, packs


def selftest():
    d, packs = load()
    assert check(d, packs) == [], check(d, packs)
    bad = copy.deepcopy(d)
    bad["ideas"][0]["packs"] = ["core", "no-such-pack"]
    assert any("no-such-pack" in e for e in check(bad, packs)), "組合名寫錯要抓到"
    bad = copy.deepcopy(d)
    bad["ideas"][0]["cost"] = {"text": "免費", "src": []}
    assert any("沒附官方出處" in e for e in check(bad, packs)), "免費沒附出處要抓到"
    bad = copy.deepcopy(d)
    bad["ideas"][1]["id"] = bad["ideas"][0]["id"]
    assert any("id 重複" in e for e in check(bad, packs)), "id 重複要抓到"
    bad = copy.deepcopy(d)
    bad["ideas"][0]["level"] = "超難"
    assert any("難度" in e for e in check(bad, packs)), "難度寫錯要抓到"
    bad = copy.deepcopy(d)
    bad["srcs"]["pages"]["url"] = "http://example.com"
    assert any("出處 pages" in e for e in check(bad, packs)), "http 出處要抓到"
    bad = copy.deepcopy(d)
    del bad["ideas"][0]["mvp"]
    assert any("少了 mvp" in e for e in check(bad, packs)), "少欄位要抓到"
    print("✅ check_ideas selftest 通過")
    return 0


def main():
    if sys.argv[1:] == ["selftest"]:
        return selftest()
    if sys.argv[1:]:
        print(__doc__)
        return 0
    d, packs = load()
    errs = check(d, packs)
    for e in errs:
        print("❌ " + e)
    print(f"{'❌' if errs else '✅'} App 點子：{len(d.get('ideas') or [])} 個，問題 {len(errs)} 個")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
