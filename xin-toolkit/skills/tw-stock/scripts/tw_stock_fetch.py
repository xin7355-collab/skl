#!/usr/bin/env python3
"""tw_stock_fetch.py — 從證交所／櫃買中心官方 OpenAPI 抓台股快照，存進 SQLite。

為什麼要每天抓：這些官方 API 只給「最新一天／最新一期」的快照，沒有歷史查詢，
想要歷史資料只能自己每天存。免金鑰、只用標準函式庫。

防禦設計（對應低資源 App 準則）：
- 限流：兩次請求之間至少間隔 --min-interval 秒（預設 3 秒）再加隨機 0~1 秒，
  證交所對密集請求會暫時封鎖 IP。
- 重試：429、5xx、逾時、連線錯誤、回傳不是 JSON（被封鎖時常回 HTML 頁）
  → 指數退避 2/4/8/16 秒，有 Retry-After 就照它；404 這類不會自己好的不重試。
- SQLite：WAL 模式＋等鎖逾時 30 秒；分批 500 筆寫入；同一天重抓用 UPSERT 覆蓋，不會重複。
- 欄位名稱不寫死：官方欄位偶爾改名，原始列整筆以 JSON 保存，只挑出股票代號與資料日期當索引。

用法：
  python3 tw_stock_fetch.py fetch --db data/tw_stock.db                 # 抓全部資料集
  python3 tw_stock_fetch.py fetch --db data/tw_stock.db --sets twse_quotes,tpex_quotes
  python3 tw_stock_fetch.py latest --db data/tw_stock.db --code 2330    # 查某檔最新資料
  python3 tw_stock_fetch.py list                                        # 列出資料集
  python3 tw_stock_fetch.py selftest                                    # 離線自我測試

結束碼：0 = 全部成功；0 + 警告 = 部分失敗（會列出哪幾個，下次執行自動重抓）；
        1 = 全部失敗；2 = 參數錯誤。
"""

import argparse
import datetime
import json
import os
import random
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

# 端點都查證過兩個以上來源；只給最新快照、免金鑰。
DATASETS = {
    "twse_quotes": ("上市股票每日收盤行情", "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"),
    "twse_valuation": ("上市股票本益比、殖利率、股價淨值比", "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"),
    "twse_revenue": ("上市公司每月營業收入彙總（單位：千元）", "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"),
    "twse_company": ("上市公司基本資料", "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"),
    "tpex_quotes": ("上櫃股票每日收盤行情", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"),
}
CODE_KEYS = ("Code", "SecuritiesCompanyCode", "公司代號", "證券代號", "股票代號")
DATE_KEYS = ("Date", "資料年月", "出表日期", "日期")
RETRY_DELAYS = (2, 4, 8, 16)
UA = "Mozilla/5.0 (compatible; tw-stock-fetch/1.0; +https://github.com/xin7355-collab/skl)"
TPE = datetime.timezone(datetime.timedelta(hours=8))


class FetchError(Exception):
    def __init__(self, msg, retryable, retry_after=None):
        super().__init__(msg)
        self.retryable = retryable
        self.retry_after = retry_after


def roc_to_iso(s):
    """民國日期轉西元：1150730 → 2026-07-30；11506 → 2026-06（月資料）。認不得就原樣回傳。"""
    s = (s or "").strip().replace("/", "")
    if not s.isdigit():
        return s
    if len(s) == 8 and s[:2] in ("19", "20"):  # 已經是西元 YYYYMMDD
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if len(s) in (6, 7):  # 民國 YYMMDD／YYYMMDD
        return f"{int(s[:-4]) + 1911}-{s[-4:-2]}-{s[-2:]}"
    if len(s) in (4, 5):  # 民國 YYMM／YYYMM（月資料）
        return f"{int(s[:-2]) + 1911}-{s[-2:]}"
    return s


def pick(row, keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return None


def http_get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        ra = e.headers.get("Retry-After") if e.headers else None
        retry_after = int(ra) if ra and ra.isdigit() else None
        raise FetchError(f"HTTP {e.code}", e.code == 429 or e.code >= 500, retry_after)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise FetchError(f"連線失敗：{getattr(e, 'reason', e)}", True)
    try:
        data = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # 被暫時封鎖時證交所常回 HTML 頁面，當成可重試。
        raise FetchError("回傳內容不是 JSON（可能被暫時封鎖或維護中）", True)
    if not isinstance(data, list):
        raise FetchError("回傳格式不是清單，官方 API 可能改版了", False)
    return data


def fetch_with_retry(url, sleep=time.sleep, getter=http_get_json):
    last = None
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            return getter(url)
        except FetchError as e:
            last = e
            if not e.retryable or attempt == len(RETRY_DELAYS):
                break
            sleep(e.retry_after if e.retry_after else RETRY_DELAYS[attempt])
    raise last


def open_db(path):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        dataset TEXT NOT NULL, data_date TEXT NOT NULL, code TEXT NOT NULL,
        payload TEXT NOT NULL, fetched_at TEXT NOT NULL,
        PRIMARY KEY (dataset, data_date, code))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_code ON snapshots(code, dataset, data_date)")
    conn.execute("""CREATE TABLE IF NOT EXISTS runs (
        dataset TEXT NOT NULL, run_at TEXT NOT NULL, ok INTEGER NOT NULL, rows INTEGER, message TEXT)""")
    return conn


def store(conn, dataset, rows, now_iso, today):
    batch, stored, skipped = [], 0, 0
    sql = ("INSERT INTO snapshots(dataset,data_date,code,payload,fetched_at) VALUES(?,?,?,?,?) "
           "ON CONFLICT(dataset,data_date,code) DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at")
    with conn:
        for row in rows:
            if not isinstance(row, dict):
                skipped += 1
                continue
            code = pick(row, CODE_KEYS)
            if not code:
                skipped += 1
                continue
            raw_date = pick(row, DATE_KEYS)
            data_date = roc_to_iso(raw_date) if raw_date else today
            batch.append((dataset, data_date, code, json.dumps(row, ensure_ascii=False), now_iso))
            if len(batch) >= 500:
                conn.executemany(sql, batch)
                stored += len(batch)
                batch = []
        if batch:
            conn.executemany(sql, batch)
            stored += len(batch)
    return stored, skipped


def run_fetch(db, sets, min_interval=3.0, urls=None, sleep=time.sleep, getter=http_get_json, out=print):
    urls = urls or {k: v[1] for k, v in DATASETS.items()}
    conn = open_db(db)
    now = datetime.datetime.now(TPE)
    now_iso, today = now.isoformat(timespec="seconds"), now.date().isoformat()
    ok, failed = [], []
    for i, name in enumerate(sets):
        if i:
            sleep(min_interval + random.random())
        try:
            rows = fetch_with_retry(urls[name], sleep=sleep, getter=getter)
            if not rows:
                raise FetchError("回傳 0 筆（可能是假日或官方尚未更新）", False)
            stored, skipped = store(conn, name, rows, now_iso, today)
            if stored == 0:
                raise FetchError(f"{len(rows)} 筆都找不到股票代號欄位，官方欄位可能改名了", False)
            ok.append((name, stored, skipped))
            conn.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (name, now_iso, 1, stored, None))
        except FetchError as e:
            failed.append((name, str(e)))
            conn.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (name, now_iso, 0, None, str(e)))
        conn.commit()
    conn.close()

    for name, stored, skipped in ok:
        extra = f"（{skipped} 筆缺代號略過）" if skipped else ""
        out(f"✓ {name} {DATASETS[name][0]}：{stored} 筆{extra}")
    for name, why in failed:
        out(f"✗ {name} {DATASETS[name][0]}：{why}")
    out(f"成功 {len(ok)}／{len(sets)} 個資料集。")
    if failed and ok and os.environ.get("GITHUB_ACTIONS"):
        out(f"::warning::部分資料集失敗 {len(failed)} 個：{', '.join(n for n, _ in failed)}；下次排程會自動重抓。")
    return 1 if failed and not ok else 0


def latest(db, code):
    if not os.path.exists(db):
        print(f"找不到資料庫 {db}，請先執行 fetch。", file=sys.stderr)
        return 2
    conn = sqlite3.connect(db, timeout=30)
    cur = conn.execute("""SELECT s.dataset, s.data_date, s.payload FROM snapshots s
        JOIN (SELECT dataset, MAX(data_date) d FROM snapshots WHERE code=? GROUP BY dataset) m
        ON s.dataset=m.dataset AND s.data_date=m.d WHERE s.code=?""", (code, code))
    res = {ds: {"data_date": d, "data": json.loads(p)} for ds, d, p in cur}
    conn.close()
    if not res:
        print(f"資料庫裡沒有代號 {code} 的資料。", file=sys.stderr)
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


def selftest():
    import http.server

    ok = True

    def check(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    check(roc_to_iso("1150730") == "2026-07-30", "民國 7 碼日期轉西元")
    check(roc_to_iso("11506") == "2026-06", "民國 5 碼年月轉西元")
    check(roc_to_iso("20260730") == "2026-07-30", "西元 8 碼日期")
    check(roc_to_iso("abc") == "abc", "認不得的日期原樣保留")

    # 本機假伺服器：依路徑模擬正常、先 429 再成功、一直 500、404、HTML 封鎖頁、欄位改名。
    hits = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits[self.path] = hits.get(self.path, 0) + 1
            n = hits[self.path]
            if self.path == "/ok":
                body = json.dumps([{"Code": "2330", "Date": "1150730", "ClosingPrice": "1000"},
                                   {"Code": "2317", "Date": "1150730", "ClosingPrice": "200"},
                                   {"Name": "沒有代號"}]).encode()
            elif self.path == "/rev":
                body = json.dumps([{"公司代號": "2330", "資料年月": "11506", "營業收入-當月營收": "1"}]).encode()
            elif self.path == "/flaky" and n == 1:
                self.send_response(429)
                self.send_header("Retry-After", "1")
                self.end_headers()
                return
            elif self.path == "/flaky":
                body = json.dumps([{"SecuritiesCompanyCode": "6488 ", "Date": "1150730"}]).encode()
            elif self.path == "/down":
                self.send_response(500)
                self.end_headers()
                return
            elif self.path == "/html":
                body = "<html>請稍後再試</html>".encode()
            elif self.path == "/renamed":
                body = json.dumps([{"Ticker": "2330"}]).encode()
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    slept = []
    fake_sleep = slept.append  # 不真的睡，只記錄退避秒數

    def run(sets, urls, db):
        lines = []
        code = run_fetch(db, sets, min_interval=0, urls=urls, sleep=fake_sleep, out=lines.append)
        return code, lines

    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "sub", "t.db")  # 資料夾不存在也要能建立
        urls = {"twse_quotes": base + "/ok", "twse_revenue": base + "/rev", "tpex_quotes": base + "/flaky",
                "twse_valuation": base + "/down", "twse_company": base + "/nope"}
        code, lines = run(list(urls), urls, db)
        text = "\n".join(lines)
        check(code == 0, "部分失敗時結束碼 0（下次重抓），不整批當掉")
        check("成功 3／5" in text, "總結寫出成功幾個")
        check("twse_valuation" in text and "HTTP 500" in text, "持續 500 會列出是哪個資料集")
        check(hits.get("/down") == 5, "500 會重試 4 次（共 5 次）")
        check(hits.get("/nope") == 1, "404 不重試")
        check(1 in slept, "429 有照 Retry-After 等待")
        check("1 筆缺代號略過" in text, "缺代號的列會被略過並計數")
        conn = sqlite3.connect(db)
        check(conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal", "資料庫是 WAL 模式")
        rows = dict(((ds, c), dt) for ds, c, dt in conn.execute("SELECT dataset, code, data_date FROM snapshots"))
        check(rows.get(("twse_quotes", "2330")) == "2026-07-30", "行情日期由民國轉西元")
        check(rows.get(("twse_revenue", "2330")) == "2026-06", "月營收用資料年月當日期")
        check(rows.get(("tpex_quotes", "6488")) == "2026-07-30", "上櫃代號前後空白會去掉")
        before = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        conn.close()

        run(["twse_quotes"], urls, db)
        conn = sqlite3.connect(db)
        after = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        check(before == after, "同一天重抓不會重複寫入")
        conn.close()

        code, lines = run(["twse_valuation"], {"twse_valuation": base + "/html"}, db)
        check(code == 1 and "不是 JSON" in "\n".join(lines), "全部失敗時結束碼 1，且說明可能被封鎖")
        code, lines = run(["twse_company"], {"twse_company": base + "/renamed"}, db)
        check(code == 1 and "欄位可能改名" in "\n".join(lines), "欄位改名時明確提示，不默默存 0 筆")

        buf = []
        old = sys.stdout
        try:
            import io
            sys.stdout = io.StringIO()
            rc = latest(db, "2330")
            buf.append(sys.stdout.getvalue())
        finally:
            sys.stdout = old
        got = json.loads(buf[0]) if rc == 0 else {}
        check(set(got) == {"twse_quotes", "twse_revenue"}, "latest 回傳該代號各資料集最新一筆")
    srv.shutdown()

    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="台股官方 OpenAPI 快照抓取")
    sub = ap.add_subparsers(dest="cmd")
    f = sub.add_parser("fetch", help="抓資料存進 SQLite")
    f.add_argument("--db", default="data/tw_stock.db")
    f.add_argument("--sets", default=",".join(DATASETS), help="逗號分隔，預設全部")
    f.add_argument("--min-interval", type=float, default=3.0, help="兩次請求最少間隔秒數（預設 3）")
    q = sub.add_parser("latest", help="查某檔股票的最新資料")
    q.add_argument("--db", default="data/tw_stock.db")
    q.add_argument("--code", required=True)
    sub.add_parser("list", help="列出資料集")
    sub.add_parser("selftest", help="離線自我測試")
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "list":
        for k, (zh, url) in DATASETS.items():
            print(f"{k:15} {zh}\n{'':15} {url}")
        return 0
    if args.cmd == "latest":
        return latest(args.db, args.code.strip())
    if args.cmd == "fetch":
        sets = [s.strip() for s in args.sets.split(",") if s.strip()]
        bad = [s for s in sets if s not in DATASETS]
        if bad or not sets:
            print(f"不認識的資料集：{', '.join(bad) or '（空）'}。可用：{', '.join(DATASETS)}", file=sys.stderr)
            return 2
        if args.min_interval < 1:
            print("--min-interval 不可小於 1 秒，太密集會被證交所封鎖 IP。", file=sys.stderr)
            return 2
        return run_fetch(args.db, sets, args.min_interval)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
