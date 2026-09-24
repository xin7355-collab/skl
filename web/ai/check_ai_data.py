#!/usr/bin/env python3
"""check_ai_data.py — 檢查 AI 指南網頁的資料（models.json、features.json）格式與出處。

每週自動更新（Routine）改完資料一定要跑這支，過了才能推上去：
不讓「沒有出處的跑分」「壞掉的 JSON」「欄位缺漏」上線，網頁也不會因資料錯誤變空白。

用法：
  python3 web/ai/check_ai_data.py            # 檢查 web/ai/ 下的資料，錯誤時結束碼 1
  python3 web/ai/check_ai_data.py selftest
"""

import datetime
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TIERS = {"daily", "hardest", "balanced", "cheap", "legacy"}
KINDS = {"官方", "第三方", "Anthropic 對照表", "OpenAI 對照表"}


def check_models(d):
    errs = []
    try:
        datetime.date.fromisoformat(d.get("checked_at", ""))
    except ValueError:
        errs.append("models.checked_at 不是 YYYY-MM-DD 日期")
    ids = set()
    names = set()
    for v in d.get("vendors", []):
        if not v.get("models"):
            errs.append(f"{v.get('vendor')} 沒有任何模型")
        for m in v.get("models", []):
            tag = m.get("name", "?")
            for k in ("name", "id", "tier", "best_for", "benchmarks"):
                if k not in m:
                    errs.append(f"{tag} 缺欄位 {k}")
            if m.get("id") in ids:
                errs.append(f"模型 id 重複：{m.get('id')}")
            ids.add(m.get("id"))
            names.add(m.get("name"))
            if m.get("tier") not in TIERS:
                errs.append(f"{tag} 的 tier 不合法：{m.get('tier')}")
            for b in m.get("benchmarks", []):
                # 沒有出處的分數一律不准上線（最容易被編造的就是跑分）。
                if not re.match(r"https?://", b.get("source", "")):
                    errs.append(f"{tag} 的 {b.get('name')} 沒有出處網址")
                if b.get("kind") not in KINDS:
                    errs.append(f"{tag} 的 {b.get('name')} 來源類型不合法：{b.get('kind')}")
    if len(d.get("vendors", [])) < 2:
        errs.append("vendors 至少要有 Anthropic 與 OpenAI 兩家")
    for c in d.get("charts", []):
        if not c.get("rows"):
            errs.append(f"圖表「{c.get('title')}」沒有資料")
        for row in c.get("rows", []):
            if len(row) != 3 or not isinstance(row[2], (int, float)) or row[1] not in ("Anthropic", "OpenAI"):
                errs.append(f"圖表「{c.get('title')}」資料列格式錯誤：{row}")
    for p in d.get("pick", []):
        for k in ("need", "claude", "codex", "why"):
            if not p.get(k):
                errs.append(f"「怎麼選」缺欄位 {k}")
    return errs


def check_features(d):
    errs = []
    prods = {p.get("product") for p in d.get("products", [])}
    for need in ("Claude Code", "Codex"):
        if need not in prods:
            errs.append(f"features 缺少 {need}")
    for p in d.get("products", []):
        if len(p.get("features", [])) < 5:
            errs.append(f"{p.get('product')} 功能少於 5 個，可能更新失敗")
        for f in p.get("features", []):
            for k in ("name", "zh", "how", "when", "level", "url"):
                if not f.get(k):
                    errs.append(f"{p.get('product')}／{f.get('name', '?')} 缺欄位 {k}")
            if f.get("level") not in ("入門", "進階"):
                errs.append(f"{f.get('name')} 的 level 不合法")
            if f.get("url") and not re.match(r"https?://", f["url"]):
                errs.append(f"{f.get('name')} 的 url 不是網址")
    return errs


def check_dir(d):
    errs = []
    for fn, fnc in (("models.json", check_models), ("features.json", check_features)):
        p = os.path.join(d, fn)
        try:
            with open(p, encoding="utf-8") as f:
                errs += fnc(json.load(f))
        except FileNotFoundError:
            errs.append(f"找不到 {fn}")
        except json.JSONDecodeError as e:
            errs.append(f"{fn} 不是合法 JSON：{e}")
    return errs


def selftest():
    ok = True

    def check(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    check(check_dir(HERE) == [], "目前的正式資料通過檢查")
    good = {"checked_at": "2026-09-23", "vendors": [
        {"vendor": "Anthropic", "models": [{"name": "A", "id": "a", "tier": "daily", "best_for": ["x"],
                                            "benchmarks": [{"name": "B", "score": "1%", "source": "https://x", "kind": "官方"}]}]},
        {"vendor": "OpenAI", "models": [{"name": "O", "id": "o", "tier": "cheap", "best_for": ["y"], "benchmarks": []}]}],
        "charts": [{"title": "t", "rows": [["A", "Anthropic", 1.0]]}], "pick": []}
    check(check_models(good) == [], "正確的模型資料通過")
    bad = json.loads(json.dumps(good))
    bad["vendors"][0]["models"][0]["benchmarks"][0]["source"] = ""
    check(any("沒有出處" in e for e in check_models(bad)), "沒有出處的跑分會被擋下")
    bad = json.loads(json.dumps(good))
    bad["vendors"][1]["models"][0]["id"] = "a"
    check(any("重複" in e for e in check_models(bad)), "重複的模型 id 會被擋下")
    bad = json.loads(json.dumps(good))
    bad["charts"][0]["rows"][0][2] = "66%"
    check(any("格式錯誤" in e for e in check_models(bad)), "圖表數值不是數字會被擋下")
    bad = json.loads(json.dumps(good))
    bad["checked_at"] = "昨天"
    check(any("日期" in e for e in check_models(bad)), "檢查日期格式")
    check(any("缺少 Codex" in e for e in check_features({"products": [{"product": "Claude Code", "features": []}]})),
          "缺產品會被擋下")
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "models.json"), "w") as f:
            f.write("{壞")
        errs = check_dir(d)
        check(any("不是合法 JSON" in e for e in errs) and any("找不到 features" in e for e in errs),
              "壞掉或缺少的檔案會被列出")
    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv):
    if argv[1:2] == ["selftest"]:
        return selftest()
    errs = check_dir(HERE)
    for e in errs:
        print("✗ " + e)
    print("AI 指南資料檢查：" + ("通過" if not errs else f"{len(errs)} 個問題"))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
