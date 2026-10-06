#!/usr/bin/env python3
"""build_trending.py — 每天替 Repo X-Ray 主動收集 GitHub 熱門專案，產生 trending.json。

為什麼在部署時收集，而不是讓手機即時查：
- 手機沒 Token 時 GitHub 搜尋每小時只有 10 次，打開「熱門推薦」就用掉一半；
  由工作流（有 GITHUB_TOKEN）每天查一次、放成靜態檔，手機讀它完全不耗額度。
- 有前一次的資料就能算「這段時間多了幾顆星」，做出「竄升最快」清單——即時查詢做不到。

失敗處理（不要默默失敗）：
- 部分清單查不到：沿用上一次的那幾份並標 stale，印出是哪幾份，結束碼 0。
- 全部查不到：有上一次的資料就整份沿用（stale），結束碼 1 讓工作流亮紅燈；
  連上一次都沒有就不寫檔（不交出空檔案），結束碼 1。

用法：
  python3 web/build_trending.py --out _site/trending.json [--prev 上一份的網址或路徑]
  python3 web/build_trending.py selftest
環境變數 GITHUB_TOKEN（選用）：用 HTTP 標頭傳，不放網址。
"""

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com/search/repositories"
# {d3}/{d7}/{d30} 會換成「幾天前」的日期；per 是每份清單筆數。
LISTS = [
    {"id": "top", "title": "歷來星數最高", "desc": "全 GitHub 最多人收藏的專案", "q": "stars:>50000", "per": 30},
    {"id": "week", "title": "本週新星", "desc": "7 天內建立、星數最多", "q": "created:>{d7}", "per": 20},
    {"id": "month", "title": "本月新星", "desc": "30 天內建立、星數最多", "q": "created:>{d30}", "per": 20},
    {"id": "active", "title": "熱門且正在更新", "desc": "1 萬星以上、3 天內有更新", "q": "stars:>10000 pushed:>{d3}", "per": 20},
    {"id": "ai", "title": "AI／大模型", "desc": "標記 llm 主題的熱門專案", "q": "topic:llm", "per": 20},
    {"id": "skills", "title": "Claude／Agent 技能", "desc": "名稱或描述含 claude skills", "q": "claude skills in:name,description,topics", "per": 20},
]
KEEP = ("full_name", "description", "stargazers_count", "language", "pushed_at", "created_at", "size", "html_url")
TR_URL = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-TW&dt=t&q="
SEARCH_GAP = 2.5  # 有 Token 時搜尋上限每分鐘 30 次；保守間隔，避免連續請求被擋


def fill_dates(q, today):
    for n in (3, 7, 30):
        q = q.replace("{d%d}" % n, (today - dt.timedelta(days=n)).isoformat())
    return q


def slim(item):
    out = {k: item.get(k) for k in KEEP}
    out["description"] = (out["description"] or "")[:200]
    out["topics"] = (item.get("topics") or [])[:5]
    return out


def http_json(url, token, tries=4):
    """指數退避（exponential backoff retry）：網路錯誤、403/429（限流）、5xx 重試 2/4/8 秒，
    額度歸零時等到 x-ratelimit-reset；其他錯誤直接拋出。"""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "skl-trending"}
    if token:
        headers["Authorization"] = "Bearer " + token
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (403, 429) and e.code < 500 or i == tries - 1:
                raise
            wait = 2 ** (i + 1)
            reset = e.headers.get("x-ratelimit-reset") if e.headers else None
            if e.headers and e.headers.get("x-ratelimit-remaining") == "0" and reset:
                wait = max(wait, min(65, int(reset) - int(time.time()) + 1))
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError):
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))


def is_zh(t):
    n = sum(1 for c in t if "\u3400" <= c <= "\u9fff")
    return n > 0 and n / max(1, len(t.replace(" ", ""))) > 0.3


def google_translate(text, tries=3):
    """免費的 Google 翻譯端點（不需金鑰）；失敗時指數退避重試。"""
    for i in range(tries):
        try:
            req = urllib.request.Request(TR_URL + urllib.parse.quote(text), headers={"User-Agent": "skl-trending"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.load(r)
            return "".join(seg[0] or "" for seg in d[0])
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))


def translate_lists(lists, prev, translate, sleep=time.sleep):
    """把描述預先翻成繁中（description_zh），手機打開熱門推薦就不用再送翻譯請求。
    上一份翻過的直接沿用；批次用換行串起來送，行數對不上就整批放棄（不配錯譯文）。
    回傳翻譯失敗的筆數（不致命：失敗的顯示英文原文）。"""
    known = {}
    for l in (prev or {}).get("lists", []):
        for it in l.get("items", []):
            if it.get("description_zh"):
                known[it.get("description", "")] = it["description_zh"]
    todo = []
    for l in lists:
        for it in l["items"]:
            d = (it.get("description") or "").replace("\n", " ").strip()
            if d and not is_zh(d) and d not in known and d not in todo:
                todo.append(d)
    failed, batch, size, streak = 0, [], 0, 0

    def flush():
        nonlocal failed, batch, size, streak
        if not batch:
            return
        # 連續兩批失敗多半是雲端 IP 被 Google 限流（429）：不再硬打，剩下的交給手機端即時翻譯
        if streak >= 2:
            failed += len(batch)
            batch, size = [], 0
            return
        try:
            out = translate("\n".join(batch)).split("\n")
            if len(out) == len(batch):
                known.update({a: b.strip() for a, b in zip(batch, out)})
                streak = 0
            else:
                failed += len(batch)
        except Exception as e:
            failed += len(batch)
            streak += 1
            print(f"✗ 翻譯一批 {len(batch)} 筆失敗：{e}", file=sys.stderr)
        batch, size = [], 0
        sleep(1)

    for d in todo:
        if size + len(d) > 1800:
            flush()
        batch.append(d)
        size += len(d) + 1
    flush()
    for l in lists:
        for it in l["items"]:
            d = (it.get("description") or "").replace("\n", " ").strip()
            if d in known:
                it["description_zh"] = known[d]
    return failed


def load_prev(src):
    """上一份 trending.json：可以是網址（線上網站）或本機路徑；拿不到就當沒有。"""
    if not src:
        return None
    try:
        if src.startswith("http"):
            with urllib.request.urlopen(urllib.request.Request(src, headers={"User-Agent": "skl-trending"}), timeout=20) as r:
                d = json.load(r)
        else:
            with open(src, encoding="utf-8") as f:
                d = json.load(f)
        return d if isinstance(d, dict) and d.get("lists") else None
    except Exception:
        return None


def rising(lists, prev, now):
    """跟上一份比，每天多幾顆星；只算兩份都有的專案，間隔太短（< 6 小時）不算以免雜訊。"""
    snap = (prev or {}).get("snapshot") or {}
    try:
        then = dt.datetime.fromisoformat(prev["generated_at"])
    except Exception:
        return []
    days = (now - then).total_seconds() / 86400
    if days < 0.25 or not snap:
        return []
    seen, out = set(), []
    for lst in lists:
        for it in lst["items"]:
            n = it["full_name"]
            if n in seen or n not in snap:
                continue
            seen.add(n)
            gain = it["stargazers_count"] - snap[n]
            if gain > 0:
                out.append(dict(it, gain_per_day=round(gain / days, 1)))
    out.sort(key=lambda x: -x["gain_per_day"])
    return out[:20]


def build(fetch, prev, now, sleep=time.sleep):
    """fetch(url) -> GitHub 搜尋回應。回傳 (data 或 None, 失敗清單 id 列表)。"""
    today = now.date()
    prev_lists = {l["id"]: l for l in (prev or {}).get("lists", [])}
    lists, failed = [], []
    for i, spec in enumerate(LISTS):
        if i:
            sleep(SEARCH_GAP)
        q = fill_dates(spec["q"], today)
        url = API + "?" + urllib.parse.urlencode({"q": q, "sort": "stars", "order": "desc", "per_page": spec["per"]})
        meta = {k: spec[k] for k in ("id", "title", "desc")}
        meta["q"] = q
        try:
            items = [slim(x) for x in fetch(url).get("items", [])]
            lists.append(dict(meta, items=items, stale=False))
        except Exception as e:
            failed.append(spec["id"])
            print(f"✗ 清單「{spec['title']}」查詢失敗：{e}", file=sys.stderr)
            if spec["id"] in prev_lists:
                lists.append(dict(prev_lists[spec["id"]], stale=True))
    if len(failed) == len(LISTS):
        if not prev:
            return None, failed
        return dict(prev, stale=True), failed
    rise = rising([l for l in lists if not l.get("stale")], prev, now)
    if rise:
        lists.insert(0, {"id": "rising", "title": "竄升最快", "desc": "跟上一次收集比，平均每天多最多星",
                         "q": "", "items": rise, "stale": False})
    snapshot = {it["full_name"]: it["stargazers_count"] for l in lists for it in l["items"]}
    return {"generated_at": now.isoformat(timespec="seconds"), "stale": False, "failed": failed,
            "lists": lists, "snapshot": snapshot}, failed


def main(argv):
    if argv[1:2] == ["selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description="收集 GitHub 熱門專案，產生 Repo X-Ray 的 trending.json")
    ap.add_argument("--out", default="trending.json", help="輸出路徑")
    ap.add_argument("--prev", default="", help="上一份 trending.json（網址或路徑），用來算竄升與失敗時沿用")
    a = ap.parse_args(argv[1:])
    out = a.out
    prev = load_prev(a.prev)
    token = os.environ.get("GITHUB_TOKEN", "")
    now = dt.datetime.now(dt.timezone.utc)
    data, failed = build(lambda u: http_json(u, token), prev, now)
    tr_failed = translate_lists(data["lists"], prev, google_translate) if data and not data.get("stale") else 0
    if data is None:
        print("✗ 全部清單都查不到，也沒有上一份資料可沿用；不產生 trending.json（網頁會改用即時查詢）。"
              "請到 Actions 看錯誤；若是 GitHub 暫時故障，下次排程會自動補上。", file=sys.stderr)
        return 1
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    n = sum(len(l["items"]) for l in data["lists"])
    if len(failed) == len(LISTS):
        print(f"✗ 全部清單查詢失敗，已沿用上一份資料（{n} 筆，標為過期）。", file=sys.stderr)
        return 1
    print(f"熱門清單：{len(data['lists'])} 份、{n} 筆" + (f"；{len(failed)} 份沿用舊資料：{', '.join(failed)}" if failed else "")
          + (f"；{tr_failed} 筆描述翻譯失敗（網頁會顯示英文或即時翻譯）" if tr_failed else ""))
    if tr_failed:
        # 翻譯是加分項，網頁會在手機上即時補翻，所以只留提示、不亮紅燈
        print(f"::notice::熱門清單有 {tr_failed} 筆描述沒有預先翻成中文（多半是雲端 IP 被 Google 限流），網頁會在手機上即時翻譯")
    if failed:
        print(f"::warning::熱門清單有 {len(failed)} 份查詢失敗，已沿用上一份：{', '.join(failed)}")
    return 0


def selftest():
    ok = True

    def check(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    now = dt.datetime(2026, 10, 6, 0, 0, tzinfo=dt.timezone.utc)
    nosleep = lambda s: None

    def repo(name, stars, **kw):
        d = {"full_name": name, "stargazers_count": stars, "description": "x" * 300, "language": "Python",
             "pushed_at": "2026-10-05", "created_at": "2026-01-01", "size": 10, "html_url": "u",
             "topics": list("abcdefg"), "owner": {"login": "secret-extra-field"}}
        d.update(kw)
        return d

    calls = []

    def good(url):
        calls.append(url)
        return {"items": [repo("a/one", 500), repo("b/two", 300)]}

    data, failed = build(good, None, now, nosleep)
    check(data and failed == [] and len(data["lists"]) == len(LISTS), "全部成功：每份清單都有")
    check("created%3A%3E2026-09-29" in calls[1], "日期換成 7 天前（2026-09-29）")
    it = data["lists"][0]["items"][0]
    check("owner" not in it and len(it["description"]) == 200 and len(it["topics"]) == 5, "只留需要的欄位、描述截斷")
    check(data["snapshot"]["a/one"] == 500 and data["lists"][0]["id"] != "rising", "第一次沒有前一份 → 不出竄升榜")

    prev = dict(data, generated_at=(now - dt.timedelta(days=2)).isoformat())
    later = lambda u: {"items": [repo("a/one", 700), repo("b/two", 300), repo("c/new", 9000)]}
    d2, _ = build(later, prev, now, nosleep)
    r = d2["lists"][0]
    check(r["id"] == "rising" and [x["full_name"] for x in r["items"]] == ["a/one"], "竄升榜只算兩份都有且有成長的")
    check(r["items"][0]["gain_per_day"] == 100.0, "每天成長 =（700-500）/ 2 天 = 100")

    recent = dict(data, generated_at=(now - dt.timedelta(hours=1)).isoformat())
    d3, _ = build(later, recent, now, nosleep)
    check(d3["lists"][0]["id"] != "rising", "間隔不到 6 小時不算竄升（避免雜訊）")

    def partial(url):
        if "topic%3Allm" in url:
            raise urllib.error.URLError("boom")
        return {"items": [repo("a/one", 1)]}
    d4, f4 = build(partial, data, now, nosleep)
    ai = [l for l in d4["lists"] if l["id"] == "ai"][0]
    check(f4 == ["ai"] and ai["stale"] and ai["items"][0]["stargazers_count"] == 500, "部分失敗：該份沿用上一份並標過期")
    d5, f5 = build(partial, None, now, nosleep)
    check(f5 == ["ai"] and not any(l["id"] == "ai" for l in d5["lists"]), "部分失敗且沒有上一份：只少那一份")

    def down(url):
        raise urllib.error.URLError("down")
    d6, f6 = build(down, data, now, nosleep)
    check(d6["stale"] and len(f6) == len(LISTS) and d6["lists"] == data["lists"], "全部失敗有上一份：整份沿用、標過期")
    d7, _ = build(down, None, now, nosleep)
    check(d7 is None, "全部失敗又沒有上一份：不產生資料")

    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "prev.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f)
        check(load_prev(p)["snapshot"]["a/one"] == 500, "讀得到本機的上一份")
        with open(p, "w") as f:
            f.write("{壞")
        check(load_prev(p) is None and load_prev(os.path.join(td, "nope")) is None and load_prev("") is None,
              "上一份壞掉或不存在 → 當作沒有")
    check(fill_dates("x{d3}", dt.date(2026, 3, 2)) == "x2026-02-27", "跨月日期正確")

    sent = []

    def fake_tr(text):
        sent.append(text)
        return "\n".join("譯" + x for x in text.split("\n"))
    L = [{"items": [{"description": "Hello"}, {"description": "World"}, {"description": "已經是中文"}, {"description": ""}]}]
    f = translate_lists(L, None, fake_tr, nosleep)
    its = L[0]["items"]
    check(f == 0 and its[0]["description_zh"] == "譯Hello" and its[1]["description_zh"] == "譯World", "描述預先翻成中文")
    check("description_zh" not in its[2] and len(sent) == 1, "已是中文不送、多筆合成一批")
    P = {"lists": [{"items": [{"description": "Hello", "description_zh": "舊譯"}]}]}
    L2 = [{"items": [{"description": "Hello"}]}]
    sent.clear()
    translate_lists(L2, P, fake_tr, nosleep)
    check(L2[0]["items"][0]["description_zh"] == "舊譯" and not sent, "上一份翻過的直接沿用，不再送")
    L3 = [{"items": [{"description": "A"}, {"description": "B"}]}]
    f3 = translate_lists(L3, None, lambda t: "只有一行", nosleep)
    check(f3 == 2 and "description_zh" not in L3[0]["items"][0], "行數對不上整批放棄，不配錯譯文")
    L4 = [{"items": [{"description": "A"}]}]
    f4 = translate_lists(L4, None, lambda t: (_ for _ in ()).throw(urllib.error.URLError("x")), nosleep)
    check(f4 == 1, "翻譯服務掛掉：回報失敗筆數、不中斷")
    calls = []

    def boom(t):
        calls.append(t)
        raise urllib.error.URLError("429")
    L5 = [{"items": [{"description": ("w%d " % i) * 300} for i in range(5)]}]
    f5 = translate_lists(L5, None, boom, nosleep)
    check(f5 == 5 and len(calls) == 2, "連續兩批失敗就停手，不硬打（其餘交給手機）")
    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
