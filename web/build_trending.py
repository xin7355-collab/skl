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
    args = dict(zip(argv[1::2], argv[2::2]))
    out = args.get("--out", "trending.json")
    prev = load_prev(args.get("--prev"))
    token = os.environ.get("GITHUB_TOKEN", "")
    now = dt.datetime.now(dt.timezone.utc)
    data, failed = build(lambda u: http_json(u, token), prev, now)
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
    print(f"熱門清單：{len(data['lists'])} 份、{n} 筆" + (f"；{len(failed)} 份沿用舊資料：{', '.join(failed)}" if failed else ""))
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
    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
