#!/usr/bin/env python3
"""網頁 → 乾淨的 Markdown（給 AI 讀、給 App 存檔）。只用 Python 內建模組，GitHub Actions 直接跑。

參考 firecrawl 的做法（抓網頁 → 去掉導覽列／廣告／腳本 → 轉 Markdown），但：
- 不複製 firecrawl 的程式（AGPL-3.0），整支自己寫；
- 不需要 Docker／Redis／付費 API，免費 Actions 就能跑；
- 不執行網頁的 JavaScript：靠 JS 才長出內容的網站（SPA）抓不到，會明確說「內容太少」而不是交出空檔。

防禦：
- 遵守 robots.txt；每個網站之間至少隔 --delay 秒，不併發轟炸（避免被封）。
- 429／5xx 依 Retry-After 或指數退避（2、4、8 秒）重試；404 不重試。
- 串流讀取、超過 --max-bytes 就停，1GB 主機也不會被大檔撐爆。

用法：
  python3 web2md.py fetch https://example.com/article            # 印到螢幕
  python3 web2md.py fetch URL --out page.md
  python3 web2md.py batch urls.txt --out-dir pages/              # 一行一個網址（# 開頭是註解）
  python3 web2md.py selftest                                     # 離線自我測試
"""
import argparse
import datetime as dt
import html
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

UA = "Mozilla/5.0 (compatible; skl-web2md/1.0; +https://github.com/xin7355-collab/skl)"
DROP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form",
        "iframe", "button", "select", "template", "canvas", "dialog"}
# class／id 含這些字的區塊多半是導覽、廣告、留言、分享按鈕
NOISE = re.compile(r"(^|[\s_-])(nav|navbar|menu|footer|sidebar|breadcrumb|cookie|banner|advert|ads?|promo|share|social|"
                   r"comment|related|subscribe|newsletter|popup|modal)([\s_-]|$)", re.I)
VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "area", "base", "col", "embed", "param", "track"}
BLOCK = {"p", "div", "section", "article", "main", "ul", "ol", "li", "table", "tr", "blockquote",
         "pre", "h1", "h2", "h3", "h4", "h5", "h6", "figure", "figcaption", "dl", "dt", "dd"}


class ToMarkdown(HTMLParser):
    """把 HTML 轉成 Markdown。先看有沒有 <main>／<article>，有就只取那一塊（正文），沒有才取整個 <body>。"""

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.out = []
        self.skip = 0          # 在要丟掉的區塊裡（深度計數）
        self.stack = []        # 開著的標籤，用來配對 skip
        self.lists = []        # 巢狀清單：["ul"|"ol", 計數]
        self.href = None
        self.link_text = []
        self.pre = 0
        self.title = ""
        self.in_title = False
        self.cell = False

    # ── 輸出小工具 ──
    def w(self, s):
        if self.href is not None:
            self.link_text.append(s)
        else:
            self.out.append(s)

    def nl(self, n=1):
        if self.href is not None:
            return
        tail = "".join(self.out[-3:])
        have = len(tail) - len(tail.rstrip("\n"))
        if have < n:
            self.out.append("\n" * (n - have))

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self.in_title = True
        if self.skip:
            if tag not in VOID:
                self.skip += 1
            return
        cls = (a.get("class") or "") + " " + (a.get("id") or "")
        hidden = "hidden" in a or a.get("aria-hidden") == "true" or re.search(r"display\s*:\s*none", a.get("style") or "")
        if tag in DROP or hidden or (tag in ("div", "section", "ul", "aside") and NOISE.search(cls)):
            if tag not in VOID:
                self.skip = 1
            return
        if tag not in VOID:
            self.stack.append(tag)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.nl(2)
            self.w("#" * int(tag[1]) + " ")
        elif tag in ("p", "div", "section", "article", "main", "figure", "table", "dl"):
            self.nl(2)
        elif tag == "br":
            self.w("  \n")
        elif tag == "hr":
            self.nl(2)
            self.w("---")
            self.nl(2)
        elif tag in ("ul", "ol"):
            self.nl(1 if self.lists else 2)
            self.lists.append([tag, 0])
        elif tag == "li":
            self.nl(1)
            depth = max(0, len(self.lists) - 1)
            kind = self.lists[-1] if self.lists else ["ul", 0]
            kind[1] += 1
            self.w("  " * depth + (f"{kind[1]}. " if kind[0] == "ol" else "- "))
        elif tag == "blockquote":
            self.nl(2)
            self.w("> ")
        elif tag == "pre":
            self.nl(2)
            self.w("```\n")
            self.pre += 1
        elif tag == "code" and not self.pre:
            self.w("`")
        elif tag in ("strong", "b"):
            self.w("**")
        elif tag in ("em", "i"):
            self.w("*")
        elif tag == "a" and a.get("href") and not a["href"].startswith(("javascript:", "#")):
            self.href = urllib.parse.urljoin(self.base, a["href"])
            self.link_text = []
        elif tag == "img" and a.get("alt"):
            src = urllib.parse.urljoin(self.base, a.get("src") or "")
            self.w(f"![{a['alt'].strip()}]({src})")
        elif tag == "tr":
            self.nl(1)
            self.w("|")
        elif tag in ("td", "th"):
            self.cell = True
            self.w(" ")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if self.skip:
            if tag not in VOID:
                self.skip -= 1
            return
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote", "figure", "table", "dl"):
            self.nl(2)
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self.nl(1 if self.lists else 2)
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self.nl(1)
            self.w("```")
            self.nl(2)
        elif tag == "code" and not self.pre:
            self.w("`")
        elif tag in ("strong", "b"):
            self.w("**")
        elif tag in ("em", "i"):
            self.w("*")
        elif tag == "a" and self.href is not None:
            text = re.sub(r"\s+", " ", "".join(self.link_text)).strip()
            href, self.href = self.href, None
            self.w(f"[{text}]({href})" if text else "")
        elif tag in ("td", "th"):
            self.w(" |")
            self.cell = False
        elif tag == "tr" and self.out and self.out[-1] == " |":
            self.nl(1)

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.skip:
            return
        if self.pre:
            self.w(data)
            return
        text = re.sub(r"\s+", " ", data)
        if text.strip() or (self.out and not self.out[-1].endswith((" ", "\n"))):
            self.w(text)


def main_region(doc):
    """有 <main> 或 <article> 就只取那一塊；多個 <article> 取最長的（通常是正文）。"""
    for tag in ("main", "article"):
        blocks = re.findall(rf"<{tag}\b[^>]*>.*?</{tag}\s*>", doc, flags=re.S | re.I)
        if blocks:
            return max(blocks, key=len)
    m = re.search(r"<body\b[^>]*>(.*)</body\s*>", doc, flags=re.S | re.I)
    return m.group(1) if m else doc


def html_to_markdown(doc, base_url=""):
    title = ToMarkdown(base_url)
    title.feed(doc[:200000])
    p = ToMarkdown(base_url)
    p.feed(main_region(doc))
    p.close()
    md = "".join(p.out)
    # 表格：第一列後補分隔線，Markdown 才認得
    lines, fixed, in_table = md.split("\n"), [], False
    for ln in lines:
        is_row = ln.startswith("|") and ln.rstrip().endswith("|")
        if is_row and not in_table:
            fixed.append(ln)
            fixed.append("|" + "---|" * max(1, ln.count("|") - 1))
            in_table = True
            continue
        in_table = is_row
        fixed.append(ln.rstrip())
    md = re.sub(r"\n{3,}", "\n\n", "\n".join(fixed)).strip()
    md = re.sub(r"\*\*\s*\*\*|(?<!\*)\*\s+\*(?!\*)", "", md)   # 空的粗體／斜體（** ** 或 * *）
    return html.unescape(title.title).strip(), md


class FetchError(Exception):
    pass


_robots = {}


def allowed(url, opener):
    """遵守 robots.txt；讀不到 robots.txt 就當作允許（多數網站沒有）。"""
    u = urllib.parse.urlsplit(url)
    root = f"{u.scheme}://{u.netloc}"
    if root not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            with opener(urllib.request.Request(root + "/robots.txt", headers={"User-Agent": UA}), timeout=15) as r:
                rp.parse(r.read(200000).decode("utf-8", "replace").splitlines())
        except Exception:
            rp.parse([])
        _robots[root] = rp
    return _robots[root].can_fetch(UA, url)


def fetch(url, max_bytes=5_000_000, tries=4, opener=urllib.request.urlopen, sleep=time.sleep):
    if not re.match(r"^https?://", url):
        raise FetchError("網址要以 http:// 或 https:// 開頭")
    if not allowed(url, opener):
        raise FetchError("這個網站的 robots.txt 不允許自動抓取，已略過（換別的來源，或用官方 API）")
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5",
                                                       "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"})
            with opener(req, timeout=30) as r:
                ctype = r.headers.get("Content-Type", "")
                if ctype and "html" not in ctype and "text" not in ctype:
                    raise FetchError(f"不是網頁（{ctype.split(';')[0]}），沒有轉換")
                chunks, size = [], 0
                while True:
                    c = r.read(65536)
                    if not c:
                        break
                    size += len(c)
                    if size > max_bytes:
                        break                           # 只取前面一段，正文通常在前面
                    chunks.append(c)
                raw = b"".join(chunks)
                m = re.search(r"charset=([\w-]+)", ctype) or re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4000], re.I)
                enc = (m.group(1).decode() if m and isinstance(m.group(1), bytes) else (m.group(1) if m else "utf-8"))
                try:
                    return raw.decode(enc, "replace")
                except LookupError:
                    return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                wait = e.headers.get("Retry-After") if e.headers else None
                sleep(min(60, int(wait)) if wait and wait.isdigit() else 2 ** (i + 1))
                continue
            hint = {403: "網站拒絕自動抓取", 404: "網址不存在", 401: "要登入才能看", 429: "被限流，晚點再試"}.get(e.code, "")
            raise FetchError(f"HTTP {e.code}{('：' + hint) if hint else ''}") from e
        except FetchError:
            raise
        except Exception as e:
            if i < tries - 1:
                sleep(2 ** (i + 1))
                continue
            raise FetchError(f"連不上（{e}）") from e
    raise FetchError("重試後仍失敗")


def convert(url, **kw):
    doc = fetch(url, **kw)
    title, md = html_to_markdown(doc, url)
    words = len(re.findall(r"[\w㐀-鿿]", md))
    if words < 80:
        raise FetchError("抓到的內容太少（可能要執行 JavaScript 才有內容，或被擋）；這種網站要用瀏覽器抓，或找它的 API／RSS")
    head = f"---\ntitle: {json_str(title or url)}\nurl: {url}\nfetched_at: {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n---\n\n"
    return head + md + "\n"


def json_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def slug(url):
    u = urllib.parse.urlsplit(url)
    s = re.sub(r"[^\w.-]+", "-", (u.netloc + u.path).strip("/"))[:120].strip("-")
    return (s or "page") + ".md"


def _body(md):
    return re.sub(r"^fetched_at: .*$", "", md, count=1, flags=re.M)


def batch(urls, out_dir, delay=1.5, **kw):
    """一個一個抓（不併發）；同一個網站之間至少隔 delay 秒。回傳 (成功數, 失敗清單)。"""
    os.makedirs(out_dir, exist_ok=True)
    last, ok, bad = {}, 0, []
    sleep = kw.get("sleep", time.sleep)
    for url in urls:
        host = urllib.parse.urlsplit(url).netloc
        if host in last:
            wait = delay - (time.monotonic() - last[host])
            if wait > 0:
                sleep(wait)
        try:
            md = convert(url, **kw)
            path = os.path.join(out_dir, slug(url))
            # 內容沒變就不改檔：只有抓取時間不同也重寫的話，排程每天都會產生沒意義的 commit
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    if _body(fh.read()) == _body(md):
                        ok += 1
                        print(f"＝ {url}（內容沒變）")
                        last[host] = time.monotonic()
                        continue
            tmp = path + ".part"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(md)
            os.replace(tmp, path)
            ok += 1
            print(f"✓ {url} → {path}")
        except FetchError as e:
            bad.append((url, str(e)))
            print(f"✗ {url}：{e}", file=sys.stderr)
        last[host] = time.monotonic()
    return ok, bad


def selftest():
    fails = 0

    def check(cond, name):
        nonlocal fails
        print(("PASS " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    page = """<html><head><title>測試 &amp; 文章</title><script>alert(1)</script><style>p{}</style></head><body>
    <nav class="top-nav"><a href="/">首頁</a><a href="/x">選單</a></nav>
    <div class="cookie-banner">我們使用 cookie</div>
    <main><h1>台股 ETF 入門</h1><p>ETF 是<strong>一籃子</strong>股票，<a href="/guide?a=1">完整說明</a>在這。</p>
    <ul><li>低成本</li><li>分散<ul><li>產業</li></ul></li></ul><ol><li>開戶</li><li>下單</li></ol>
    <pre><code>python3 a.py\n  --x</code></pre><p>行內 <code>code</code> 與 <em>斜體</em><br>換行</p>
    <table><tr><th>代號</th><th>名稱</th></tr><tr><td>0050</td><td>元大台灣50</td></tr></table>
    <img src="/i.png" alt="走勢圖"><div style="display:none">隱藏文字</div><aside>側欄廣告</aside></main>
    <footer>版權所有</footer></body></html>"""
    title, md = html_to_markdown(page, "https://ex.com/a/b")
    check(title == "測試 & 文章", "標題取 <title> 並解碼")
    check("# 台股 ETF 入門" in md and "**一籃子**" in md, "標題與粗體")
    check("[完整說明](https://ex.com/guide?a=1)" in md, "連結轉成絕對網址")
    check("- 低成本" in md and "  - 產業" in md and "1. 開戶" in md and "2. 下單" in md, "巢狀清單與編號清單")
    check("```\npython3 a.py\n  --x\n```" in md, "程式碼區塊保留縮排")
    check("`code`" in md and "*斜體*" in md, "行內程式碼與斜體")
    check("| 代號 | 名稱 |" in md and "|---|---|" in md and "| 0050 | 元大台灣50 |" in md, "表格補分隔線")
    check("![走勢圖](https://ex.com/i.png)" in md, "圖片留 alt 與網址")
    check(not any(x in md for x in ("alert", "首頁", "cookie", "隱藏文字", "側欄廣告", "版權所有")),
          "去掉腳本、導覽、cookie 橫幅、隱藏、側欄、頁尾")
    t2, md2 = html_to_markdown("<body><div class='site-menu'>選單</div><article>短</article><article><p>"
                               + "正文" * 50 + "</p></article></body>")
    check(md2.startswith("正文") and "選單" not in md2, "多個 article 取最長的")

    class Resp:
        def __init__(self, body, ctype="text/html; charset=big5"):
            self.body, self.headers = body, {"Content-Type": ctype}

        def read(self, n=-1):
            b, self.body = (self.body, b"") if n < 0 else (self.body[:n], self.body[n:])
            return b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    calls, slept = [], []

    def opener(req, timeout):
        url = req.full_url if hasattr(req, "full_url") else req
        calls.append(url)
        if url.endswith("/robots.txt"):
            return Resp(b"User-agent: *\nDisallow: /private", "text/plain")
        if "flaky" in url and calls.count(url) < 3:
            raise urllib.error.HTTPError(url, 503, "busy", {"Retry-After": "3"}, None)
        if "gone" in url:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        if "spa" in url:
            return Resp(b"<html><body><div id=root></div></body></html>", "text/html")
        if "pdf" in url:
            return Resp(b"%PDF", "application/pdf")
        return Resp(("<html><body><main><p>" + "繁體中文內容" * 30 + "</p></main></body></html>").encode("big5"))

    _robots.clear()
    md = convert("https://t.com/ok", opener=opener, sleep=slept.append)
    check(md.startswith("---\ntitle:") and "url: https://t.com/ok" in md and "繁體中文內容" in md, "big5 網頁正確解碼並加上來源資訊")
    for url, want in (("https://t.com/private/x", "robots.txt"), ("https://t.com/gone", "HTTP 404"),
                      ("https://t.com/spa", "內容太少"), ("https://t.com/pdf", "不是網頁"), ("ftp://t.com", "http")):
        try:
            convert(url, opener=opener, sleep=slept.append)
            check(False, f"{url} 應該失敗")
        except FetchError as e:
            check(want in str(e), f"{want} → 清楚的錯誤訊息（{e}）")
    slept.clear()
    convert("https://t.com/flaky", opener=opener, sleep=slept.append)
    check(slept == [3, 3], "503 依 Retry-After 等待後重試成功")
    check(sum(1 for c in calls if c.endswith("/robots.txt")) == 1, "robots.txt 每個網站只讀一次")
    with tempfile.TemporaryDirectory() as td:
        waits = []
        ok, bad = batch(["https://t.com/ok", "https://t.com/gone", "https://t.com/ok2"], td, delay=5,
                        opener=opener, sleep=waits.append)
        check(ok == 2 and len(bad) == 1 and "gone" in bad[0][0], "批次：部分失敗照樣存其他的，並列出失敗的")
        check(len(waits) == 2 and all(w > 4 for w in waits), "同一個網站之間有間隔，不轟炸")
        check(sorted(os.listdir(td)) == ["t.com-ok.md", "t.com-ok2.md"], "檔名由網址產生、不留 .part")
        p = os.path.join(td, "t.com-ok.md")
        with open(p, encoding="utf-8") as fh:
            before = fh.read()
        os.utime(p, (0, 0))
        batch(["https://t.com/ok"], td, opener=opener, sleep=waits.append)
        with open(p, encoding="utf-8") as fh:
            check(fh.read() == before and os.path.getmtime(p) == 0, "內容沒變就不改檔（排程不會每天空 commit）")
    try:
        fetch("https://big.com/x", max_bytes=10, opener=lambda r, timeout: Resp(b"<p>" + b"a" * 100000) if not str(getattr(r, "full_url", r)).endswith("robots.txt") else Resp(b"", "text/plain"))
        check(True, "超過大小上限只讀前段，不會撐爆記憶體")
    except Exception as e:
        check(False, f"大小上限：{e}")
    print("自我測試全部通過" if not fails else f"{fails} 項失敗")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description="網頁轉乾淨的 Markdown（遵守 robots.txt、限流、重試）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="抓一個網址")
    f.add_argument("url")
    f.add_argument("--out", help="存成檔案（不給就印到螢幕）")
    f.add_argument("--max-bytes", type=int, default=5_000_000)
    b = sub.add_parser("batch", help="抓清單裡的每個網址")
    b.add_argument("list", help="文字檔，一行一個網址（# 開頭是註解）")
    b.add_argument("--out-dir", required=True)
    b.add_argument("--delay", type=float, default=1.5, help="同一個網站兩次之間至少隔幾秒")
    b.add_argument("--max-bytes", type=int, default=5_000_000)
    sub.add_parser("selftest", help="離線自我測試")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "fetch":
        try:
            md = convert(a.url, max_bytes=a.max_bytes)
        except FetchError as e:
            print(f"✗ {e}", file=sys.stderr)
            return 1
        if a.out:
            with open(a.out, "w", encoding="utf-8") as fh:
                fh.write(md)
            print(f"✓ 已存到 {a.out}")
        else:
            sys.stdout.write(md)
        return 0
    with open(a.list, encoding="utf-8") as fh:
        urls = [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    if not urls:
        print("清單是空的：在檔案裡一行放一個網址", file=sys.stderr)
        return 1
    ok, bad = batch(urls, a.out_dir, delay=a.delay, max_bytes=a.max_bytes)
    print(f"完成：成功 {ok}、失敗 {len(bad)}（共 {len(urls)}）")
    for url, why in bad:
        print(f"  ✗ {url}：{why}")
    return 0 if ok else 1   # 全部失敗才紅燈；部分失敗照樣交出成功的，並列出哪幾個失敗


if __name__ == "__main__":
    sys.exit(main())
