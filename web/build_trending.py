#!/usr/bin/env python3
"""build_trending.py — 每天替 Repo X-Ray 主動收集 GitHub 熱門專案，產生 trending.json。

為什麼在部署時收集，而不是讓手機即時查：
- 手機沒 Token 時 GitHub 搜尋每小時只有 10 次，打開「熱門推薦」就用掉一半；
  由工作流（有 GITHUB_TOKEN）每天查一次、放成靜態檔，手機讀它完全不耗額度。
- 有前一次的資料就能算「這段時間多了幾顆星」，做出「竄升最快」「爆紅新星」清單——即時查詢做不到。
- 也記得每個專案第一次上榜的日期（first_seen），X-Ray 才能標「新上榜」（new）。

輸出欄位（web/xray.html 會讀，改名要兩邊一起改）：每個項目有 first_seen（YYYY-MM-DD）、new（布林）；
清單 id "breakout" 的項目另有 stars_per_day、gain（比上一份多幾星，上一份沒有就是 null）。

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
import email.utils
import json
import os
import re
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
    # 爆紅新星：一份清單最多查 BREAKOUT_PAGES（2）頁＝最多 2 次搜尋，第 1 頁不滿 100 筆就不查第 2 頁。
    # 搜尋次數：其他 6 份各 1 次＋這份最多 2 次＝每次部署最多 8 次（重試另計）。
    # 沒 Token 時搜尋上限是每分鐘 10 次（有 Token 30 次）；每次間隔 SEARCH_GAP（2.5 秒），
    # 8 次約 17.5 秒、都落在同一分鐘的視窗內，沒 Token 也還剩 2 次給重試。
    {"id": "breakout", "title": "爆紅新星", "desc": "30 天內建立，平均每天 200 星以上或比上次收集多 400 星以上",
     "q": "created:>={d30} stars:>={min_stars}", "per": 20, "kind": "breakout"},
    {"id": "active", "title": "熱門且正在更新", "desc": "1 萬星以上、3 天內有更新", "q": "stars:>10000 pushed:>{d3}", "per": 20},
    {"id": "ai", "title": "AI／大模型", "desc": "標記 llm 主題的熱門專案", "q": "topic:llm", "per": 20},
    {"id": "skills", "title": "Claude／Agent 技能", "desc": "名稱或描述含 claude skills", "q": "claude skills in:name,description,topics", "per": 20},
]
KEEP = ("full_name", "description", "stargazers_count", "language", "pushed_at", "created_at", "size", "html_url")
TR_URL = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-TW&dt=t&q="
SEARCH_GAP = 2.5  # 有 Token 時搜尋上限每分鐘 30 次；保守間隔，避免連續請求被擋
# 爆紅門檻（數值照 new-term-radar 的預設）：門檻太低會被一般新專案灌滿，太高抓不到剛起飛的
BREAKOUT_MIN_STARS = 300
BREAKOUT_VELOCITY = 200  # 建立以來平均每天幾星
BREAKOUT_DELTA = 400     # 比上一份快照多幾星
BREAKOUT_PAGES = 2
# 同一擁有者在同一份清單最多幾筆：不然一次開源一整套的組織會洗版整份清單
PER_OWNER = 3
DUP_THRESHOLD = 0.7
# GitHub 要求等超過這麼久就放棄這份清單：工作流有逾時，乾等幾分鐘不如沿用上一份
MAX_WAIT = 60
EPOCH_THRESHOLD = 1e8  # 大於這個數字的 reset 值是 Unix 時間戳（GitHub 的寫法），不是「幾秒後」


def fill_dates(q, today):
    for n in (3, 7, 30):
        q = q.replace("{d%d}" % n, (today - dt.timedelta(days=n)).isoformat())
    return q.replace("{min_stars}", str(BREAKOUT_MIN_STARS))


def slim(item):
    out = {k: item.get(k) for k in KEEP}
    out["description"] = (out["description"] or "")[:200]
    out["topics"] = (item.get("topics") or [])[:5]
    # 授權只留代號（MIT、AGPL-3.0…）：X-Ray 批次勾選時要告訴 Claude 能不能複製程式碼
    lic = (item.get("license") or {}).get("spdx_id")
    if lic:
        out["license"] = lic
    return out


def retry_delay_from_headers(headers, fallback, now=None):
    """被限流時該等幾秒：照主機給的標頭，沒有才用 fallback（指數退避）。

    改寫自 last30days skill 的 lib/http.py retry_delay_from_headers()
    （MIT License, Copyright (c) 2026 Matt Van Horn）。跟原版的差別：
    - 不在這裡截到上限，回傳實際秒數，讓呼叫端決定「等太久就放棄」
      （原版截短後照樣去打，時間還沒到又被擋一次，白白多用一次額度）。
    - x-ratelimit-reset 只在額度歸零（remaining 是 0）或根本沒給 remaining 時才看：GitHub 每個回應都帶 reset，
      額度還有剩的 403 是次要限流或真的沒權限，照 reset 等只會白等。
    - Retry-After 也接受 HTTP 日期格式。
    """
    if not headers:
        return fallback
    now = time.time() if now is None else now
    remaining = headers.get("x-ratelimit-remaining")
    names = ["Retry-After"]
    if remaining is None or str(remaining).strip() in ("0", "0.0"):
        names.append("x-ratelimit-reset")
    for name in names:
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            try:
                value = email.utils.parsedate_to_datetime(str(raw)).timestamp()
            except (TypeError, ValueError, IndexError, OverflowError):
                continue
        if value >= EPOCH_THRESHOLD:
            value -= now
        if value > 0:
            return value
    return fallback


def http_json(url, token, tries=4, opener=urllib.request.urlopen, sleep=time.sleep, now=time.time):
    """指數退避（exponential backoff retry）：網路錯誤、5xx 重試 2/4/8 秒；
    403/429（限流）照 GitHub 給的 Retry-After／x-ratelimit-reset 等，超過 MAX_WAIT 秒就放棄
    （丟出例外，build() 會沿用上一份的這張清單）；其他錯誤直接拋出。"""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "skl-trending"}
    if token:
        headers["Authorization"] = "Bearer " + token
    for i in range(tries):
        try:
            with opener(urllib.request.Request(url, headers=headers), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (403, 429) and e.code < 500 or i == tries - 1:
                raise
            wait = 2 ** (i + 1)
            if e.code in (403, 429):
                wait = retry_delay_from_headers(e.headers, wait, now())
                if wait > MAX_WAIT:
                    raise RuntimeError(f"GitHub 限流（HTTP {e.code}），要求等 {int(wait)} 秒，超過 {MAX_WAIT} 秒上限；"
                                       "這次先放棄這份清單、沿用上一份，下次排程會自動再試") from e
            sleep(wait)
        except (urllib.error.URLError, TimeoutError):
            if i == tries - 1:
                raise
            sleep(2 ** (i + 1))


# 簡體字表（只放繁體不會用到的字）：要跟 web/xray.html 的 SIMP_RE 一致。簡體描述也要送翻譯轉成繁體。
SIMP = set("这们说时来为对动画艺术与视频复语组织过还没发现问题进实开关传输网络数据库图标样设计让请记录区页书产业经济个国会学员长东车间门应该选择从电话总务边头乐历亲观觉讨论认识词译读写课谁谅调谈讲诉证评试详误变乱买卖宝审宠军农决况凤刘则刚创删别剧劳势华单卫厂厅压厉县参双叹吗听启响围园团坏块坚场坛坟壮声处备够夺奋妇妈婴宁宽寿导层岁岛币师带帮广庆庙废异张弹强归彻径忆态怀恋恶悬惊惧惨惯戏战户扑执扩扫扬扰抚抢护报担拟拥挂挡挣挤挥损换摄摆摇撑断无旧显暂机杀杂权条杨极构枪柜栏树桥档梦检楼欢气汉汤沟泪泽洁浅测浑浓涛润涨渐渔温湾湿满灭灯灵灾炉点炼烟烦烧热爱爷牵犹独狮猎猪献环玛琐畅疗盘监盖矿码础确碍礼种积称稳窃竞笔笼筑签简类粮紧纠红纤约级纪纬纯纱纲纳纵纷纸纹纺线练细终绍绑结绕绘给绝统继绩绪续维绵综绿缓编缘缝缩罗罚罢职联聪肃肠肤肿胀胆胜脉脑脚脸腾舰艰节芦苏苹荐荡荣药莲获营萧萨蓝虑虚虽蚀蛮补衬袭见规览触誉订训议讯许访诗诚诞询谊谋谓谢谣谦谨谱贝负贡财责贤败货质贩贪贫购贯贱贴贵贷贸费贺贼资赋赌赏赔赖赚赛赞赠赢赵赶趋跃践踪轨转轮软轰轻载较辅辆辈辉辞辩辽达迁迈运远违连迟适逊递逻遗邓邮邻郑酱释针钓钟钢钥钩钱钻铁铃铜银铺链销锁锅锋错锦键锻镇镜闪闭闯闲闷闹闻阀阁阅阔队阳阴阵阶际陆陈险随隐难雾静须顶项顺顾顿颁预领颖颜额风飞饥饭饮饰饱饼馆驱驶驻驾验骂骑骗鱼鲁鲜鸟鸡鸣鸭鹅麦黄齐齿龄龙")


def tr_fix(src, out):
    """Google 把技術縮寫翻成別的意思（LLM→法學碩士）時換回來；規則要跟 web/xray.html 的 TR_FIX 一致。"""
    if "LLM" in src:
        out = re.sub(r"法學碩士(學位)?", "LLM", out)
    if re.search(r"\bPR\b", src):
        out = out.replace("拉取請求", "PR")
    return out


def is_zh(t):
    """已經是繁體中文才不送翻譯；短句 1 個、長句 2 個以上簡體字就當簡體。"""
    n = sum(1 for c in t if "\u3400" <= c <= "\u9fff")
    if n == 0 or n / max(1, len(t.replace(" ", ""))) <= 0.3:
        return False
    sc = sum(1 for c in t if c in SIMP)
    return not (sc >= 2 or (sc >= 1 and n < 12))


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
                # 原樣傳回的英文句不記（Google 誤判語言時會這樣），留給手機端單句重翻
                known.update({a: tr_fix(a, b.strip()) for a, b in zip(batch, out)
                              if not (b.strip() == a and re.search(r"[A-Za-z]{3,}\s+[A-Za-z]{3,}", a))})
                streak = 0
            else:
                failed += len(batch)
        except Exception as e:
            failed += len(batch)
            streak += 1
            print(f"✗ 翻譯一批 {len(batch)} 筆失敗：{e}", file=sys.stderr)
        batch, size = [], 0
        sleep(1)

    # 簡體中文和英文分開送：混在同一批時 Google 會整批當中文，英文句原樣傳回
    def cjk(t):
        return any("\u3400" <= c <= "\u9fff" for c in t)
    todo = [d for d in todo if cjk(d)] + [d for d in todo if not cjk(d)]
    for d in todo:
        if size + len(d) > 1800 or (batch and cjk(batch[0]) != cjk(d)):
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


def _norm_text(t):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (t or "").lower())).strip()


def _grams(norm, n=3):
    if len(norm) < n:
        return {norm} if norm else set()
    return {norm[i:i + n] for i in range(len(norm) - n + 1)}


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def similarity(a, b):
    """字元三連片段 Jaccard 與單字 Jaccard 取大者。
    做法參考 last30days skill 的 lib/dedupe.py hybrid_similarity()（MIT License, Copyright (c) 2026 Matt Van Horn），
    簡化成不做中文斷詞、不用停用字表（這裡比的是專案名稱＋一句描述，不是長文）。"""
    na, nb = _norm_text(a), _norm_text(b)
    ta = {w for w in na.split() if len(w) > 1}
    tb = {w for w in nb.split() if len(w) > 1}
    return max(_jaccard(_grams(na), _grams(nb)), _jaccard(ta, tb))


def _dup_text(it):
    # 名稱和描述合起來比：只比名稱時 react／preact 這種短名三連片段重疊 75%，會被誤判成重複；
    # 名稱的 - _ 換成空白，換皮 fork（foo-skills／foo_skills）才比得出來
    name = (it.get("full_name") or "").split("/")[-1].replace("-", " ").replace("_", " ")
    return name + " " + (it.get("description") or "")


def dedupe_items(items, threshold=DUP_THRESHOLD):
    """名稱或描述近似重複（鏡像、換皮 fork、同一份 awesome 清單的翻譯版）只留排名前面那筆。
    參考 last30days skill 的 lib/dedupe.py dedupe_items()（MIT, Copyright (c) 2026 Matt Van Horn）。"""
    kept, texts = [], []
    for it in items:
        t = _dup_text(it)
        if any(similarity(t, k) >= threshold for k in texts):
            continue
        kept.append(it)
        texts.append(t)
    return kept


def cap_per_owner(items, limit=PER_OWNER):
    """同一個擁有者最多留 limit 筆（items 已照排名排好，先遇到的就是最好的）。
    參考 last30days skill 的 lib/fusion.py _apply_per_author_cap()（MIT, Copyright (c) 2026 Matt Van Horn）。"""
    count, out = {}, []
    for it in items:
        owner = (it.get("full_name") or "").split("/")[0].lower()
        if not owner:
            out.append(it)
            continue
        if count.get(owner, 0) < limit:
            out.append(it)
            count[owner] = count.get(owner, 0) + 1
    return out


def tidy(items, per):
    """先收合重複、再套作者上限、最後截到 per 筆；所以搜尋時要多抓（見 build 的 per_page）。"""
    return cap_per_owner(dedupe_items(items))[:per]


def breakout_score(repos, before, now, velocity_min=BREAKOUT_VELOCITY, delta_min=BREAKOUT_DELTA):
    """爆紅判斷：建立以來平均每天 ≥ velocity_min 星，或比上一份快照多 ≥ delta_min 星；快的排前面。
    改寫自 new-term-radar 的 radar/breakout.py score()（MIT License, Copyright (c) 2026 Yansen Zhu），
    輸出改成 X-Ray 清單的項目格式（slim 過的欄位＋stars_per_day、gain；gain 是 None 代表上一份沒有它）。"""
    out = []
    for r in repos:
        try:
            created = dt.datetime.fromisoformat((r.get("created_at") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if created.tzinfo is None:  # 只有日期沒時區時當 UTC，不然跟 now 相減會丟例外、整份清單失敗
            created = created.replace(tzinfo=dt.timezone.utc)
        # 不到一天的專案當一天算：剛建立幾小時、50 星就會算出每天幾百星，是假爆紅
        age = max((now - created).total_seconds() / 86400, 1.0)
        stars = r.get("stargazers_count") or 0
        velocity = stars / age
        name = r.get("full_name")
        delta = stars - before[name] if before and name in before else None
        if velocity < velocity_min and (delta is None or delta < delta_min):
            continue
        out.append(dict(slim(r), stars_per_day=round(velocity), gain=delta))
    out.sort(key=lambda x: -max(x["stars_per_day"], x["gain"] or 0))
    return out


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
    return tidy(out, 20)


def mark_first_seen(lists, prev, today):
    """每個項目加 first_seen（第一次出現在任一清單的日期）與 new（first_seen 是今天）。
    記憶放在頂層 first_seen_index：專案掉出清單幾天後回來，不會又被當成新的。
    上一份是舊格式（項目沒有 first_seen）時，當作上一份那天就看過，避免改版那天全部變成「新」。
    回傳新的 index：只留還在清單裡的、或 60 天內看過的，檔案才不會越長越大。"""
    idx = dict((prev or {}).get("first_seen_index") or {})
    try:
        prev_day = dt.datetime.fromisoformat(prev["generated_at"]).date().isoformat()
    except Exception:
        prev_day = None
    for l in (prev or {}).get("lists", []):
        for it in l.get("items", []):
            n = it.get("full_name")
            d = it.get("first_seen") or prev_day
            if n and d and (n not in idx or d < idx[n]):
                idx[n] = d
    t = today.isoformat()
    current = set()
    for l in lists:
        for it in l["items"]:
            n = it["full_name"]
            current.add(n)
            idx.setdefault(n, t)
            it["first_seen"] = idx[n]
            it["new"] = idx[n] == t
    cutoff = (today - dt.timedelta(days=60)).isoformat()
    return {n: d for n, d in idx.items() if n in current or d >= cutoff}


def search_url(q, per, page=1):
    p = {"q": q, "sort": "stars", "order": "desc", "per_page": per}
    if page > 1:
        p["page"] = page
    return API + "?" + urllib.parse.urlencode(p)


def build(fetch, prev, now, sleep=time.sleep):
    """fetch(url) -> GitHub 搜尋回應。回傳 (data 或 None, 失敗清單 id 列表)。"""
    today = now.date()
    prev_lists = {l["id"]: l for l in (prev or {}).get("lists", [])}
    prev_snap = (prev or {}).get("snapshot") or {}
    lists, failed, extra_snap = [], [], {}
    n_calls = 0

    def search(url):
        nonlocal n_calls
        if n_calls:
            sleep(SEARCH_GAP)
        n_calls += 1
        return fetch(url).get("items", [])

    for spec in LISTS:
        q = fill_dates(spec["q"], today)
        meta = {k: spec[k] for k in ("id", "title", "desc")}
        meta["q"] = q
        try:
            if spec.get("kind") == "breakout":
                repos = []
                for page in range(1, BREAKOUT_PAGES + 1):
                    got = search(search_url(q, 100, page))
                    repos += got
                    if len(got) < 100:
                        break
                items = tidy(breakout_score(repos, prev_snap, now), spec["per"])
                # 候選全部記進快照：下次才算得出「比上次多幾星」（只記入榜的，會漏掉剛要起飛的）
                extra_snap.update({r["full_name"]: r["stargazers_count"] for r in repos if r.get("full_name")})
            else:
                # 多抓一倍：收合重複與作者上限會刷掉一些，截完還要有 per 筆（同一次搜尋，不多耗額度）
                items = tidy([slim(x) for x in search(search_url(q, min(100, spec["per"] * 2)))], spec["per"])
            lists.append(dict(meta, items=items, stale=False))
        except Exception as e:
            failed.append(spec["id"])
            print(f"✗ 清單「{spec['title']}」查詢失敗：{e}", file=sys.stderr)
            if spec["id"] in prev_lists:
                lists.append(dict(prev_lists[spec["id"]], stale=True))
    if len(failed) == len(LISTS):
        if not prev:
            return None, failed
        old = dict(prev, stale=True)
        old["lists"] = [dict(l, items=[dict(it) for it in l.get("items", [])]) for l in prev.get("lists", [])]
        # 整份沿用時 new 要重算：昨天的「新」今天就不新了
        old["first_seen_index"] = mark_first_seen(old["lists"], prev, today)
        return old, failed
    rise = rising([l for l in lists if not l.get("stale")], prev, now)
    if rise:
        lists.insert(0, {"id": "rising", "title": "竄升最快", "desc": "跟上一次收集比，平均每天多最多星",
                         "q": "", "items": rise, "stale": False})
    index = mark_first_seen(lists, prev, today)
    snapshot = dict(extra_snap)
    snapshot.update({it["full_name"]: it["stargazers_count"] for l in lists for it in l["items"]})
    return {"generated_at": now.isoformat(timespec="seconds"), "stale": False, "failed": failed,
            "lists": lists, "snapshot": snapshot, "first_seen_index": index}, failed


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
        d = {"full_name": name, "stargazers_count": stars, "description": (name + " tool ") * 40, "language": "Python",
             "pushed_at": "2026-10-05", "created_at": "2026-01-01", "size": 10, "html_url": "u",
             "topics": list("abcdefg"), "owner": {"login": "secret-extra-field"},
             "license": {"spdx_id": "MIT", "name": "MIT License", "url": "x"}}
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
    check(it.get("license") == "MIT", "授權只留代號")
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
    sent.clear()
    L2 = [{"items": [{"description": "Hello there friend"}, {"description": "视频下载工具"}, {"description": "Fast LLM gateway"}]}]
    translate_lists(L2, None, lambda t: (sent.append(t), "\n".join("法學碩士" if "LLM" in x else ("譯" + x if "视" in x else x) for x in t.split("\n")))[1], nosleep)
    i2 = L2[0]["items"]
    check(len(sent) == 2 and "视频" in sent[0] and "Hello" in sent[1], "簡體與英文分開送")
    check("description_zh" not in i2[0] and i2[2]["description_zh"] == "LLM" and i2[1]["description_zh"].startswith("譯"),
          "英文原樣傳回不記、LLM 不被翻成法學碩士")
    check(is_zh("已經是繁體中文的描述") and not is_zh("艺术与视频动画：拆解复刻动画") and not is_zh("视频下载")
          and not is_zh("Hello world") and is_zh("拆解與刻畫的工具，支援多種風格"), "簡體要送翻譯、繁體不送、繁簡共用字不誤判")
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

    # ── 限流等待：照 Retry-After／x-ratelimit-reset，太久就放棄 ──
    T = 1_800_000_000.0
    check(retry_delay_from_headers({"Retry-After": "7"}, 2, T) == 7, "Retry-After 秒數照用")
    check(retry_delay_from_headers({"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(T) + 30)}, 2, T) == 30,
          "x-ratelimit-reset 是時間戳 → 換算成 30 秒")
    check(retry_delay_from_headers({"x-ratelimit-reset": "42"}, 2, T) == 42, "x-ratelimit-reset 是秒數（沒給 remaining）→ 42 秒")
    check(retry_delay_from_headers({"x-ratelimit-remaining": "9", "x-ratelimit-reset": str(int(T) + 900)}, 4, T) == 4,
          "額度還有剩的 403 不照 reset 等（用指數退避）")
    check(retry_delay_from_headers({"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(T) - 5)}, 8, T) == 8
          and retry_delay_from_headers({"Retry-After": "abc"}, 8, T) == 8 and retry_delay_from_headers(None, 8, T) == 8,
          "reset 已過、看不懂、沒標頭 → 用指數退避")
    hd = email.utils.formatdate(T + 20, usegmt=True)
    check(abs(retry_delay_from_headers({"Retry-After": hd}, 2, T) - 20) < 1, "Retry-After 是 HTTP 日期也看得懂")

    class FakeResp:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return self.body

    def opener_seq(seq):
        hits = []

        def op(req, timeout=None):
            hits.append(req.full_url)
            x = seq[len(hits) - 1]
            if isinstance(x, Exception):
                raise x
            return FakeResp(x)
        return op, hits

    def herr(code, hdrs):
        import email.message
        m = email.message.Message()
        for k, v in hdrs.items():
            m[k] = v
        return urllib.error.HTTPError("u", code, "x", m, None)
    slept = []
    op, hits = opener_seq([herr(429, {"Retry-After": "5"}), b'{"items": []}'])
    r = http_json("https://api.github.invalid/x", "", opener=op, sleep=slept.append, now=lambda: T)
    check(r == {"items": []} and slept == [5] and len(hits) == 2, "429＋Retry-After 5 → 等 5 秒再送一次就成功")
    slept.clear()
    op, hits = opener_seq([herr(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(T) + 600)})])
    try:
        http_json("https://api.github.invalid/x", "", opener=op, sleep=slept.append, now=lambda: T)
        gave_up = False
    except RuntimeError as e:
        gave_up = "60 秒" in str(e) and "沿用上一份" in str(e)
    check(gave_up and not slept and len(hits) == 1, "要求等 600 秒（超過 60 秒上限）→ 不乾等，直接放棄並說明")
    op, hits = opener_seq([herr(404, {})])
    try:
        http_json("https://api.github.invalid/x", "", opener=op, sleep=slept.append, now=lambda: T)
        e404 = False
    except urllib.error.HTTPError as e:
        e404 = e.code == 404
    check(e404 and len(hits) == 1, "404 不重試")

    def gave_up_fetch(url):
        if "stars%3A%3E50000" in url:
            raise RuntimeError("GitHub 限流，要求等 600 秒")
        return {"items": [repo("a/one", 1)]}
    d8, f8 = build(gave_up_fetch, data, now, nosleep)
    top = [l for l in d8["lists"] if l["id"] == "top"][0]
    check(f8 == ["top"] and top["stale"], "放棄的那份照「部分失敗」沿用上一份")

    # ── 爆紅判斷 ──
    def ago(days):
        return (now - dt.timedelta(days=days)).isoformat().replace("+00:00", "Z")
    cand = [repo("v/fast", 1000, created_at=ago(2)),       # 每天 500 星 → 收
            repo("s/slow", 1000, created_at=ago(20)),      # 每天 50 星，上一份 400 → 多 600 → 收
            repo("m/meh", 1000, created_at=ago(20)),       # 每天 50 星，上一份 800 → 只多 200 → 不收
            repo("h/hour", 150, created_at=ago(0.05)),     # 剛建 1 小時、150 星：年齡當 1 天 → 150/天 → 不收
            repo("n/nosnap", 1000, created_at=ago(25))]    # 每天 40 星、上一份沒有 → 不收
    bs = breakout_score(cand, {"s/slow": 400, "m/meh": 800}, now)
    check(sorted(x["full_name"] for x in bs) == ["s/slow", "v/fast"], "爆紅：每天 ≥200 星或比上次多 ≥400 星才收")
    fast = [x for x in bs if x["full_name"] == "v/fast"][0]
    slow = [x for x in bs if x["full_name"] == "s/slow"][0]
    check(fast["stars_per_day"] == 500 and fast["gain"] is None and slow["gain"] == 600
          and bs[0]["full_name"] == "s/slow" and "owner" not in fast, "爆紅：記每天星數與多幾星、用較大者排序、欄位有瘦身")

    bcalls = []

    def bfetch(url):
        bcalls.append(url)
        if "stars%3A%3E%3D300" in url:
            if "page=2" in url:
                return {"items": [repo("p/two", 9000, created_at=ago(3))]}
            return {"items": [repo("p/x%d" % i, 400, created_at=ago(10)) for i in range(99)]
                    + [repo("p/one", 5000, created_at=ago(5))]}
        return {"items": [repo("a/one", 500)]}
    d9, f9 = build(bfetch, None, now, nosleep)
    bl = [l for l in d9["lists"] if l["id"] == "breakout"][0]
    check(f9 == [] and bl["title"] == "爆紅新星" and [x["full_name"] for x in bl["items"]] == ["p/two", "p/one"],
          "爆紅清單：第 1 頁滿 100 筆才查第 2 頁、只收符合門檻的")
    check(len(bcalls) == len(LISTS) + 1 <= 10, f"一次部署共 {len(bcalls)} 次搜尋（沒 Token 每分鐘 10 次也夠）")
    check("created%3A%3E%3D2026-09-06" in [u for u in bcalls if "page=2" in u][0], "爆紅查 30 天內建立（2026-09-06 起）")
    check(d9["snapshot"].get("p/x5") == 400, "爆紅候選（沒入榜的）也記進快照，下次才算得出多幾星")

    # ── first_seen／new ──
    P1 = {"generated_at": "2026-10-01T00:00:00+00:00", "snapshot": {},
          "first_seen_index": {"gone/back": "2026-09-20"},
          "lists": [{"id": "top", "items": [{"full_name": "a/one", "first_seen": "2026-09-01"},
                                            {"full_name": "old/format"}]}]}
    fs = lambda u: {"items": [repo("a/one", 500), repo("old/format", 300), repo("gone/back", 200), repo("z/new", 100)]}
    d10, _ = build(fs, P1, now, nosleep)
    its = {x["full_name"]: x for x in [l for l in d10["lists"] if l["id"] == "top"][0]["items"]}
    check(its["a/one"]["first_seen"] == "2026-09-01" and its["a/one"]["new"] is False, "first_seen 從上一份沿用、不是新的")
    check(its["z/new"]["first_seen"] == "2026-10-06" and its["z/new"]["new"] is True, "第一次出現 → first_seen 是今天、new=true")
    check(its["old/format"]["first_seen"] == "2026-10-01" and not its["old/format"]["new"],
          "上一份是舊格式（沒有 first_seen）→ 當作上一份那天看過，改版那天不會全部變新")
    check(its["gone/back"]["first_seen"] == "2026-09-20", "掉出清單又回來 → 用 first_seen_index 記得的日期")
    check(all("first_seen" in x and "new" in x for l in d10["lists"] for x in l["items"]), "每份清單每一筆都有 first_seen 與 new")
    P2 = dict(d10, generated_at=now.isoformat(), first_seen_index={"ancient/x": "2026-01-01", "recent/x": "2026-09-30"})
    idx = mark_first_seen([{"items": []}], P2, now.date())
    check("recent/x" in idx and "ancient/x" not in idx, "first_seen_index 超過 60 天又不在清單的會清掉")
    d11, _ = build(down, dict(d10, generated_at="2026-10-05T00:00:00+00:00"), now + dt.timedelta(days=1), nosleep)
    check(not any(x["new"] for l in d11["lists"] for x in l["items"]), "整份沿用時 new 重算：昨天的新今天就不新")

    # ── 同一作者上限、近似重複收合 ──
    many = [{"full_name": "big/r%d" % i, "description": "unrelated project number %d about topic %s" % (i, "abcdefg"[i])}
            for i in range(6)]
    many.insert(2, {"full_name": "other/z", "description": "something else entirely"})
    capped = cap_per_owner(many)
    check([x["full_name"] for x in capped] == ["big/r0", "big/r1", "other/z", "big/r2"], "同一擁有者最多 3 筆，留排名前面的")
    check(len(cap_per_owner([{"full_name": "Big/a"}, {"full_name": "big/b"}, {"full_name": "BIG/c"}, {"full_name": "bIg/d"}])) == 3,
          "擁有者不分大小寫")
    dup = [{"full_name": "foo/awesome-llm", "description": "A curated list of awesome LLM resources and tools"},
           {"full_name": "bar/awesome_llm", "description": "A curated list of awesome LLM resources and tools."},
           {"full_name": "facebook/react", "description": "The library for web and native user interfaces"},
           {"full_name": "preactjs/preact", "description": "Fast 3kB React alternative with the same modern API"}]
    dd = dedupe_items(dup)
    check([x["full_name"] for x in dd] == ["foo/awesome-llm", "facebook/react", "preactjs/preact"],
          "近似重複只留排名前面那筆；react／preact 名稱像但描述不同不算重複")
    check(similarity("abc def", "abc def") == 1.0 and similarity("", "x") == 0.0 and similarity("ab", "ab") == 1.0,
          "相似度邊界：完全相同 1、空字串 0、短於 3 字也比得出來")
    tl = []

    def tfetch(url):
        tl.append(url)
        return {"items": [repo("big/r%d" % i, 1000 - i) for i in range(8)] + [repo("c/k", 10)]}
    d12, _ = build(tfetch, None, now, nosleep)
    top12 = [l for l in d12["lists"] if l["id"] == "top"][0]["items"]
    check([x["full_name"] for x in top12] == ["big/r0", "big/r1", "big/r2", "c/k"], "清單套用作者上限")
    check("per_page=60" in tl[0], "多抓一倍（30 → 60）再截，截完才夠筆數")
    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
