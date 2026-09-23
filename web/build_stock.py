#!/usr/bin/env python3
"""build_stock.py — 把 tw-stock 收集的 SQLite 資料整理成台股看板用的 stock.json。

輸出用「欄位清單＋二維陣列」而不是一筆一個物件：2000 多檔股票的 JSON 可以小一半，
手機載入快。只讀需要的最新快照與近 60 個交易日收盤價，不把整個資料庫讀進記憶體。

用法：
  python3 web/build_stock.py --db data/tw_stock.db --out _site/stock.json
  python3 web/build_stock.py selftest

資料庫不存在時輸出 status=not_ready 的 stock.json（網頁顯示「還沒開始收集」），不當成錯誤。
"""

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import tempfile

FIELDS = ["code", "name", "market", "industry", "close", "change", "pe", "yield", "pb",
          "rev", "rev_yoy", "rev_mom", "cum_yoy", "ret5", "ret20", "etf"]

# 證交所產業別代碼（公司基本資料的「產業別」是代碼）；對不到就顯示代碼本身。
INDUSTRY = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械",
    "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業",
    "12": "汽車工業", "14": "建材營造", "15": "航運業", "16": "觀光餐旅", "17": "金融保險",
    "18": "貿易百貨", "19": "綜合", "20": "其他", "21": "化學工業", "22": "生技醫療業",
    "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業",
    "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
    "31": "其他電子業", "32": "文化創意業", "33": "農業科技業", "34": "電子商務",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活",
    "80": "管理股票", "91": "存託憑證",
}
# 一般股票 4 碼；ETF 以 00 開頭（可能 4～6 碼、尾端帶字母）。權證、債券等其他代號不列入看板。
CODE_RE = re.compile(r"^(\d{4}|00\d{2,4}[A-Z]?)$")


def num(v):
    """官方數字是字串，可能有千分位、正負號、空白、'--'；認不得回 None，不猜。"""
    if v is None:
        return None
    s = str(v).replace(",", "").replace("+", "").strip()
    if s in ("", "-", "--", "---", "N/A", "X", "除權息", "除息", "除權"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def latest_rows(conn, dataset):
    row = conn.execute("SELECT MAX(data_date) FROM snapshots WHERE dataset=?", (dataset,)).fetchone()
    if not row or not row[0]:
        return None, {}
    d = row[0]
    out = {}
    for code, payload in conn.execute("SELECT code, payload FROM snapshots WHERE dataset=? AND data_date=?", (dataset, d)):
        out[code] = json.loads(payload)
    return d, out


def price_history(conn, dataset, close_key, days=60):
    """回傳 {code: [由新到舊的收盤價]}，只取最近 days 個資料日期。"""
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT data_date FROM snapshots WHERE dataset=? ORDER BY data_date DESC LIMIT ?", (dataset, days))]
    if not dates:
        return {}
    hist = {}
    q = f"SELECT code, data_date, payload FROM snapshots WHERE dataset=? AND data_date>=? ORDER BY data_date DESC"
    for code, _, payload in conn.execute(q, (dataset, dates[-1])):
        c = num(json.loads(payload).get(close_key))
        if c is not None:
            hist.setdefault(code, []).append(c)
    return hist


def ret(hist, n):
    if not hist or len(hist) <= n or not hist[n]:
        return None
    return round((hist[0] / hist[n] - 1) * 100, 2)


def build(db):
    if not os.path.exists(db):
        return {"status": "not_ready", "rows": [], "fields": FIELDS}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    try:
        dq, twse = latest_rows(conn, "twse_quotes")
        dt, tpex = latest_rows(conn, "tpex_quotes")
        dv, val = latest_rows(conn, "twse_valuation")
        dr, rev = latest_rows(conn, "twse_revenue")
        _, comp = latest_rows(conn, "twse_company")
        h_twse = price_history(conn, "twse_quotes", "ClosingPrice")
        h_tpex = price_history(conn, "tpex_quotes", "Close")
        runs = conn.execute("SELECT dataset, MAX(run_at) FROM runs WHERE ok=1 GROUP BY dataset").fetchall()
    finally:
        conn.close()

    rows = []

    def industry(code):
        r = rev.get(code, {}).get("產業別") or comp.get(code, {}).get("產業別") or ""
        r = str(r).strip()
        return INDUSTRY.get(r.zfill(2), r) if r.isdigit() else r

    def revenue(code):
        r = rev.get(code)
        if not r:
            return None, None, None, None
        cur, cum, cum_ly = (num(r.get("營業收入-當月營收")), num(r.get("累計營業收入-當月累計營收")),
                            num(r.get("累計營業收入-去年累計營收")))
        # 累計年增率自己算，不依賴官方欄位名稱（這個欄位名稱各資料集不一致）。
        cum_yoy = round((cum / cum_ly - 1) * 100, 2) if cum and cum_ly else None
        return cur, num(r.get("營業收入-去年同月增減(%)")), num(r.get("營業收入-上月比較增減(%)")), cum_yoy

    for market, src, name_k, close_k, chg_k, hist in (
            ("上市", twse, "Name", "ClosingPrice", "Change", h_twse),
            ("上櫃", tpex, "CompanyName", "Close", "Change", h_tpex)):
        for code, q in src.items():
            if not CODE_RE.match(code):
                continue
            v = val.get(code, {})
            cur, yoy, mom, cum_yoy = revenue(code)
            h = hist.get(code)
            name = (comp.get(code, {}).get("公司簡稱") or q.get(name_k) or "").strip()
            rows.append([code, name, market, industry(code), num(q.get(close_k)), num(q.get(chg_k)),
                         num(v.get("PEratio")), num(v.get("DividendYield")), num(v.get("PBratio")),
                         cur, yoy, mom, cum_yoy, ret(h, 5), ret(h, 20), 1 if code.startswith("00") else 0])
    rows.sort(key=lambda r: r[0])
    return {
        "status": "ok" if rows else "empty",
        "generated_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="minutes"),
        "dates": {"twse_quotes": dq, "tpex_quotes": dt, "twse_valuation": dv, "twse_revenue": dr},
        "last_ok": {ds: t for ds, t in runs},
        "fields": FIELDS,
        "rows": rows,
    }


def selftest():
    ok = True

    def check(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "xin-toolkit", "skills", "tw-stock", "scripts"))
    import tw_stock_fetch as t  # noqa: E402

    check(num("1,234.5") == 1234.5 and num("+5.00") == 5.0 and num("--") is None and num("") is None
          and num("-0.30") == -0.3, "數字解析：千分位、正負號、缺值")

    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "t.db")
        res = build(db)
        check(res["status"] == "not_ready" and res["rows"] == [], "資料庫不存在時回 not_ready，不當錯誤")

        conn = t.open_db(db)
        # 22 個交易日的台積電收盤價：100、101 … 121（由舊到新）
        for i in range(22):
            day = f"2026-08-{i + 1:02d}"
            t.store(conn, "twse_quotes", [{"Code": "2330", "Name": "台積電", "Date": day.replace("-", ""),
                                            "ClosingPrice": str(100 + i), "Change": "+1.00"},
                                           {"Code": "030001", "Name": "某權證", "Date": day.replace("-", ""),
                                            "ClosingPrice": "1"}], "now", day)
        t.store(conn, "tpex_quotes", [{"SecuritiesCompanyCode": "6488", "CompanyName": "環球晶",
                                       "Date": "1150822", "Close": "500", "Change": "-2.50"}], "now", "x")
        t.store(conn, "twse_quotes", [{"Code": "0050", "Name": "元大台灣50", "Date": "20260822",
                                       "ClosingPrice": "180", "Change": "0"}], "now", "x")
        t.store(conn, "twse_valuation", [{"Code": "2330", "Date": "1150822", "PEratio": "25.5",
                                          "DividendYield": "1.8", "PBratio": ""}], "now", "x")
        t.store(conn, "twse_revenue", [{"公司代號": "2330", "資料年月": "11507", "產業別": "半導體業",
                                        "營業收入-當月營收": "300,000,000", "營業收入-去年同月增減(%)": "40.5",
                                        "營業收入-上月比較增減(%)": "-3.2", "累計營業收入-當月累計營收": "2200",
                                        "累計營業收入-去年累計營收": "2000"}], "now", "x")
        t.store(conn, "twse_company", [{"公司代號": "2330", "公司簡稱": "台積電", "產業別": "24",
                                        "出表日期": "1150822"}], "now", "x")
        conn.commit()
        conn.close()

        res = build(db)
        rows = {r[0]: dict(zip(res["fields"], r)) for r in res["rows"]}
        check(set(rows) == {"2330", "6488", "0050"}, "權證等非股票代號不列入，ETF 保留")
        tw = rows["2330"]
        check(tw["close"] == 121 and tw["pe"] == 25.5 and tw["pb"] is None, "最新收盤與估值，缺值為 None")
        check(tw["rev"] == 300000000 and tw["rev_yoy"] == 40.5 and tw["cum_yoy"] == 10.0, "月營收與自算累計年增率")
        check(tw["industry"] == "半導體業" and tw["market"] == "上市", "產業別與市場")
        check(tw["ret5"] == round((121 / 116 - 1) * 100, 2) and tw["ret20"] == round((121 / 101 - 1) * 100, 2),
              "近 5／20 日漲跌幅")
        check(rows["6488"]["market"] == "上櫃" and rows["6488"]["change"] == -2.5 and rows["6488"]["ret5"] is None,
              "上櫃資料；歷史不足時漲跌幅為 None")
        check(rows["0050"]["etf"] == 1 and tw["etf"] == 0, "ETF 標記")
        check(res["dates"]["twse_revenue"] == "2026-07", "資料日期")
        check(len(json.dumps(res)) < 5000, "輸出精簡（陣列格式）")

    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="產生台股看板 stock.json")
    ap.add_argument("cmd", nargs="?", default="build", choices=["build", "selftest"])
    ap.add_argument("--db", default="data/tw_stock.db")
    ap.add_argument("--out", default="web/stock.json")
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    res = build(args.db)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, separators=(",", ":"))
    print(f"stock.json：狀態 {res['status']}，{len(res['rows'])} 檔；資料日期 {res.get('dates')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
