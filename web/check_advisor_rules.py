#!/usr/bin/env python3
"""check_advisor_rules.py — 檢查 Repo X-Ray「App 顧問」的規則檔（web/advisor_rules.json）。

為什麼要檢查：規則裡寫的技能路徑如果被改名或刪掉，網頁會推薦一個不存在的技能，
複製出去的安裝指令也會失敗。部署與 CI 都跑這支，壞掉就擋下來。

用法：
  python3 web/check_advisor_rules.py            # 檢查，錯誤時結束碼 1
  python3 web/check_advisor_rules.py selftest
"""

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def check(data, root=ROOT):
    errs = []
    sig = data.get("signals") or {}
    if not sig:
        errs.append("signals 是空的")
    ids = set()
    rules = data.get("rules") or []
    if not rules:
        errs.append("rules 是空的")
    for r in rules:
        rid = r.get("id", "?")
        if rid in ids:
            errs.append(f"規則 id 重複：{rid}")
        ids.add(rid)
        for k in ("title", "why"):
            if not r.get(k):
                errs.append(f"{rid} 缺欄位 {k}")
        for w in r.get("when", []):
            if w.lstrip("!") not in sig:
                errs.append(f"{rid} 用了沒定義的訊號：{w}")
        w = r.get("weight", 1)
        if not isinstance(w, (int, float)) or w < 0:
            errs.append(f"{rid} 的 weight 必須是 0 以上的數字")
        if not r.get("skills"):
            errs.append(f"{rid} 沒有推薦任何技能")
        for s in r.get("skills", []):
            if os.path.isabs(s) or ".." in s.split("/") or not os.path.isfile(os.path.join(root, s, "SKILL.md")):
                errs.append(f"{rid} 推薦的技能不存在：{s}")
        for q in r.get("search", []):
            if not q.get("q") or not q.get("why"):
                errs.append(f"{rid} 的搜尋建議缺 q 或 why")
    return errs


def selftest():
    ok = True

    def t(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    with open(os.path.join(HERE, "advisor_rules.json"), encoding="utf-8") as f:
        t(check(json.load(f)) == [], "目前的規則全部有效")
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "a/skills/x"))
        open(os.path.join(d, "a/skills/x/SKILL.md"), "w").close()
        good = {"signals": {"ui": "網頁"}, "rules": [{"id": "r", "when": ["!ui"], "title": "t", "why": "w",
                                                      "skills": ["a/skills/x"], "search": [{"q": "q", "why": "w"}]}]}
        t(check(good, d) == [], "正確的規則通過")
        bad = json.loads(json.dumps(good))
        bad["rules"][0]["skills"] = ["a/skills/gone"]
        t(any("不存在" in e for e in check(bad, d)), "技能被改名或刪掉會被擋下")
        bad = json.loads(json.dumps(good))
        bad["rules"][0]["when"] = ["nosuch"]
        t(any("沒定義的訊號" in e for e in check(bad, d)), "打錯訊號名稱會被擋下")
        bad = json.loads(json.dumps(good))
        bad["rules"].append(dict(bad["rules"][0]))
        t(any("重複" in e for e in check(bad, d)), "重複的規則 id 會被擋下")
        bad = json.loads(json.dumps(good))
        bad["rules"][0]["weight"] = -1
        t(any("weight" in e for e in check(bad, d)), "weight 不合法會被擋下")
        bad = json.loads(json.dumps(good))
        bad["rules"][0]["skills"] = ["../etc"]
        t(any("不存在" in e for e in check(bad, d)), "擋掉 .. 路徑")
    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv):
    ap = argparse.ArgumentParser(description="檢查 Repo X-Ray App 顧問規則（web/advisor_rules.json）")
    ap.add_argument("cmd", nargs="?", choices=["selftest"], help="selftest：離線自我測試")
    a = ap.parse_args(argv[1:])
    if a.cmd == "selftest":
        return selftest()
    try:
        with open(os.path.join(HERE, "advisor_rules.json"), encoding="utf-8") as f:
            errs = check(json.load(f))
    except (OSError, json.JSONDecodeError) as e:
        errs = [f"讀不到或不是合法 JSON：{e}"]
    for e in errs:
        print("✗ " + e)
    print("App 顧問規則檢查：" + ("通過" if not errs else f"{len(errs)} 個問題"))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
