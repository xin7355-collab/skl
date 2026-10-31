#!/usr/bin/env python3
"""X-Ray 搜尋語法探針：在 GitHub Actions 上實際查 GitHub 搜尋，回答「這種寫法 GitHub 怎麼解讀」。

為什麼要有這支：雲端開發環境打不到 GitHub 搜尋 API，模糊搜尋的組字規則（OR、排除、括號、連字號）
只能靠真的查一次才知道 GitHub 怎麼解讀，不能用猜的。改 xray.html 的 fuzzyQuery 之前先跑這支。

用法：python3 web/xray_search_probe.py            （環境變數 GITHUB_TOKEN；手動工作流 X-Ray search probe）
      python3 web/xray_search_probe.py selftest   （離線：只檢查判讀邏輯）
輸出只有數量與前幾個專案名（公開資料），寫進 job summary。
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

# (標籤, 查詢, 要檢查「前 30 筆不該出現」的字)
CASES = [
    ("單字 election", "election", None),
    ("單字 選舉", "選舉", None),
    ("單字 2026", "2026", None),
    ("空格 = AND？", "2026 election", None),
    ("OR", "2026 OR election", None),
    ("OR 優先順序", "election OR 選舉 2026", None),
    ("括號在前", "(election OR 選舉) 2026", None),
    ("括號在後", "2026 (election OR 選舉)", None),
    ("排除：-字", "novel -manga", "manga"),
    ("排除：NOT", "novel NOT manga", "manga"),
    ("OR 接 -字", "novel OR -manga", None),
    ("OR 接 NOT", "novel OR NOT manga", None),
    ("括號 + 排除", "(novel OR fiction) -manga", "manga"),
    ("括號 + NOT", "(novel OR fiction) NOT manga", "manga"),
    ("連字號單字", "claude-code", None),
    ("連字號 OR", "claude-code OR skill", None),
    ("引號片語", '"claude code"', None),
    ("連字號 OR 中文", "novel-downloader OR 小說", None),
    ("六組 OR 上限", "a1 OR b2 OR c3 OR d4 OR e5 OR f6", None),
    ("七組 OR", "novel OR fiction OR ebook OR epub OR manga OR comic OR story", None),
    # 第 2 輪(2026-10-31):模糊搜尋改成「先全部都要(AND),太少才放寬(OR)」+ 排除改 NOT 之前要確認的寫法
    ("OR 後面接 NOT", "novel OR fiction NOT manga", "manga"),
    ("AND 後面接 NOT", "novel downloader NOT manga", "manga"),
    ("中文 AND", "小說 下載", None),
    ("英文 AND 三詞", "2026 election vote", None),
    ("明寫 AND", "2026 AND election", None),
    ("五個 OR + 一個 NOT", "a1 OR b2 OR c3 OR d4 OR e5 NOT f6", None),
    ("四個 OR + 一個 NOT", "novel OR fiction OR ebook OR epub NOT manga", "manga"),
    ("兩個 NOT", "novel NOT manga NOT comic", "manga"),
    ("-語法條件", "novel -language:python", None),
]


def hits_word(items, word):
    """前 30 筆裡有幾筆的名稱／描述／topics 含有 word（判斷排除有沒有作用）。"""
    word = word.lower()
    n = 0
    for it in items[:30]:
        hay = " ".join([it.get("full_name") or "", it.get("description") or "", " ".join(it.get("topics") or [])]).lower()
        if word in hay:
            n += 1
    return n


def run():
    tok = os.environ.get("GITHUB_TOKEN") or ""
    hdr = {"Accept": "application/vnd.github+json", "User-Agent": "skl-xray-probe"}
    if tok:
        hdr["Authorization"] = "Bearer " + tok   # Token 放標頭、不放網址
    out = ["## 🔎 X-Ray 搜尋語法探針", "", "| 寫法 | 查詢 | 總數 | 前 3 名 | 備註 |", "|---|---|---|---|---|"]
    bad = 0
    for label, q, excl in CASES:
        url = "https://api.github.com/search/repositories?per_page=30&q=" + urllib.parse.quote(q)
        note = ""
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30) as r:
                j = json.load(r)
            items = j.get("items") or []
            total = j.get("total_count", 0)
            top = "、".join(i["full_name"] for i in items[:3])
            if excl:
                k = hits_word(items, excl)
                note = f"前 30 筆含「{excl}」{k} 筆"
        except urllib.error.HTTPError as e:
            total, top = f"HTTP {e.code}", ""
            try:
                note = json.loads(e.read().decode("utf-8", "replace")).get("message", "")[:80]
            except Exception:
                note = ""
            if e.code not in (422,):
                bad += 1
        except Exception as e:  # 網路問題：照實寫，不當成語法結論
            total, top, note = "連線失敗", "", type(e).__name__
            bad += 1
        out.append(f"| {label} | `{q}` | {total} | {top} | {note} |")
        time.sleep(3)   # 搜尋 API 每分鐘 30 次：慢慢查
    text = "\n".join(out)
    print(text)
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        with open(sp, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    if bad:
        print(f"::error::{bad} 個查詢不是 422 的失敗（網路或額度）—— 那幾列不能當成語法結論")
        return 1
    return 0


def selftest():
    items = [{"full_name": "a/novel", "description": "manga reader", "topics": []},
             {"full_name": "b/x", "description": "", "topics": ["manga"]},
             {"full_name": "c/y", "description": "novel", "topics": []}]
    assert hits_word(items, "manga") == 2
    assert hits_word(items, "MANGA") == 2
    assert hits_word([], "x") == 0
    assert len({c[0] for c in CASES}) == len(CASES), "標籤重複"
    print("✅ selftest 通過")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if sys.argv[1:] == ["selftest"] else run())
