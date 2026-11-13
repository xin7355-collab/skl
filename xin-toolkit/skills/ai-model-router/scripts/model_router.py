#!/usr/bin/env python3
"""model_router.py — 讓 App 自動挑 AI 模型、模型下架自動換、難的工作自動升級。

為什麼需要（使用者的 App 實際踩過）：
- 寫死模型名稱，供應商一下架就整批 404：StockAI-DB 的 llama-3.1-8b-instant 被 Groq 下架後，
  新聞標題翻譯、情緒判讀、垃圾過濾三件事一起壞掉，而且只顯示「API 錯誤 404」。
- 不同工作難度差很多：翻標題用最便宜的就好，股票分析才值得用大模型。

做法（從 StockAI-DB 的 groq_common.py 一般化而來）：
1. 讓官方自己說現在有哪些模型（/models），依「等級」的偏好序（正規表示式，不寫死版號）挑最新的。
2. 回應「明確說」模型不存在／已下架 → 清快取、避開它、換下一個再打（自我修復）。
   404 但回應沒這樣說 → 多半是網址設定錯：直接停下說明，不把每個模型都當成下架。
3. 429 分三種（classify()，兩個語言共用的案例在 assets/router_cases.json）：
   - 每分鐘／每秒限流 → 照對方給的時間等（Retry-After 秒數或日期、Gemini 的 retryDelay、Groq 的「1m30s」），
     再試同一個模型；要等超過 60 秒就先換下一個模型，不讓一個請求卡住整個工作。
   - 每日／每月額度用完 → 等幾秒不會恢復：不重試、直接換下一個模型，記住恢復時間。
   - 餘額／帳單問題 → 等多久都不會恢復：停止這家供應商，用中文提醒去後台看帳單。
   Gemini 的每分鐘限流訊息也寫「You exceeded your current quota」，所以看結構化的 quotaId（PerDay／PerMinute），
   不看那句話。
4. 冷卻記在「模型」上、整個程式共用（新建的 Router 也看得到）：之後的呼叫跳過冷卻中的模型，同一家的其他模型照用。
   全部都在冷卻、而且最快的 60 秒內恢復 → 等它一下再試，批次工作才不會一路失敗。
5. 5xx／網路斷線 → 指數退避重試（2/4/8 秒，尊重 Retry-After）；401／403 → 換下一把金鑰。
   逾時／連線中斷（狀態 0）：免費供應商照樣退避重試；付費供應商不重送同一請求，直接換下一個候選
   ——對方可能已經處理完、只是回應沒送到，重送等於付兩次錢。429／5xx 是伺服器明確拒絕、沒有處理，照舊可重試。
6. 結果不合格（validate 回 False）→ 自動升一級（fast → smart → best）再試一次。
7. 一次 chat() 最多送出 MODEL_ROUTER_MAX_CALLS 次請求（預設 12），超過就停：
   3 個等級 × 5 個候選 × 多把金鑰 × 4 次重試，最壞會打出上百次，付費服務會燒錢。
   額度用完的模型只花 1 次、冷卻中的不花，上限才留給真的有機會成功的模型。
8. 推理模型（例如 Groq 的 qwen）回覆開頭的 <think>…</think> 會拿掉，App 拿到的只有答案。
9. 全部失敗時丟出中文、可行動的錯誤，列出試過哪些模型（絕不印出金鑰）。

等級：fast（分類、翻譯、摘要、大量小工作）／smart（分析、推理）／best（最難、最長）／auto（依內容自動判斷）。
指定模型（覆寫）：環境變數 MODEL_ROUTER_<供應商>_<等級>，例如 MODEL_ROUTER_GROQ_FAST=openai/gpt-oss-20b。
呼叫次數上限：環境變數 MODEL_ROUTER_MAX_CALLS（或 Router(max_calls=...)）。
付費與否：照 PROVIDERS 的 paid；Groq／Gemini 開了付費帳單的話傳 Router(..., paid=True)，逾時就不重送。
冷卻紀錄只存在記憶體（程式結束就沒了）；確定額度已恢復、想馬上全部重試就呼叫 reset_cooldowns()。

用法：
  from model_router import Router
  r = Router("groq", keys=[os.environ["GROQ_API_KEY"]])
  text = r.chat("auto", [{"role": "user", "content": "把這則新聞標題翻成中文：..."}])
  python3 model_router.py list groq        # 列出現在可用的模型與各等級會挑誰（金鑰讀環境變數）
  python3 model_router.py selftest         # 加 --strict：找不到共用案例檔就算失敗（skl 的 CI 用）
"""

import argparse
import datetime
import email.utils
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request

TIERS = ("fast", "smart", "best")
# 供應商設定：list＝模型清單網址、chat＝呼叫網址、key_env＝預設讀哪個環境變數、
# paid＝每次呼叫都收費（逾時不重送）。"free_suffix" 表示看模型名稱：OpenRouter 名稱以 :free 結尾的免費、其他收費。
# Groq／Gemini 預設是免費層所以標 False；開了付費帳單的 App 建立 Router 時傳 paid=True。
PROVIDERS = {
    "groq": {"list": "https://api.groq.com/openai/v1/models", "chat": "https://api.groq.com/openai/v1/chat/completions",
             "key_env": "GROQ_API_KEY", "style": "openai", "paid": False},
    "openrouter": {"list": "https://openrouter.ai/api/v1/models", "chat": "https://openrouter.ai/api/v1/chat/completions",
                   "key_env": "OPENROUTER_API_KEY", "style": "openai", "paid": "free_suffix"},
    "gemini": {"list": "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
               "chat": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
               "key_env": "GEMINI_API_KEY", "style": "gemini", "paid": False},
    "anthropic": {"list": "https://api.anthropic.com/v1/models?limit=100", "chat": "https://api.anthropic.com/v1/messages",
                  "key_env": "ANTHROPIC_API_KEY", "style": "anthropic", "paid": True},
}
DEFAULT_MAX_CALLS = 12
# 偏好序：每個等級由上而下比對，同一條規則命中多個時挑版本最新的。用規則而不是固定名稱，版號一直變也不用改。
# Groq：2026-08-16 停用 llama-3.1-8b-instant、llama-3.3-70b-versatile 後，原本的規則只剩 gpt-oss 配得到。
# qwen 另有一份免費額度、中文也比較好，三個等級都放：smart 先用 qwen、best 先用 gpt-oss-120b，
# 兩級的第一個模型不同，回覆不合格升級時才是真的換一個模型。「qwen」不寫版號，新版上架會自動配到。
PREFS = {
    "groq": {"fast": [r"llama-\d+(\.\d+)?-8b", r"instant", r"gpt-oss-20b", r"qwen", r"llama"],
             "smart": [r"qwen", r"llama-\d+(\.\d+)?-70b", r"gpt-oss-120b", r"70b", r"llama"],
             "best": [r"gpt-oss-120b", r"qwen", r"llama-4", r"70b", r"llama"]},
    "openrouter": {"fast": [r"llama-\d+(\.\d+)?-8b.*:free$", r":free$"],
                   "smart": [r"70b.*:free$", r"(qwen|deepseek).*:free$", r":free$"],
                   "best": [r"(deepseek|qwen).*:free$", r"70b.*:free$", r":free$"]},
    "gemini": {"fast": [r"^gemini-\d+(\.\d+)?-flash-lite$", r"^gemini-\d+(\.\d+)?-flash$"],
               "smart": [r"^gemini-\d+(\.\d+)?-flash$", r"^gemini-\d+(\.\d+)?-pro$"],
               "best": [r"^gemini-\d+(\.\d+)?-pro$", r"^gemini-\d+(\.\d+)?-flash$"]},
    "anthropic": {"fast": [r"haiku"], "smart": [r"sonnet"], "best": [r"opus", r"sonnet"]},
}
# 非聊天模型先濾掉（StockAI-DB 實測踩過：挑到語音辨識模型回 400）；guard 也濾掉 gpt-oss-safeguard、prompt-guard
NON_CHAT = re.compile(r"whisper|tts|orpheus|speech|audio|embed|guard|moderation|rerank|image|vision-only|live|aqa|playai", re.I)
# 預覽版／實驗版只在沒有正式版時才用
UNSTABLE = re.compile(r"preview|exp|experimental|beta", re.I)
TTL = 6 * 3600  # 模型下架是常態，不可永久快取

# 等待與冷卻（秒）
MAX_WAIT = 60            # 一次最多等 60 秒；對方要更久就先換下一個模型，不讓一個請求卡住整個工作
QUOTA_COOLDOWN = 3600    # 額度用完但回應沒說何時恢復：先避開 1 小時（之後再試只花 1 次請求）
QUOTA_MIN_COOLDOWN = 300  # 額度用完卻說「幾秒後再試」不可信：Gemini 每日額度的錯誤也附幾秒的 retryDelay（Google 論壇實例是 1s，照等還是 429）
MAX_COOLDOWN = 86400     # 怪異的 Retry-After（幾個月後）也只避開一天，長時間跑的程式不會永遠用不了那個模型

# 以下規則 Python 與 JS 兩邊要一模一樣（re.A：\b、\d 只認英數，跟 JS 一致）。
# 回應「明確說」模型不存在／下架才算：只看 404 會把網址打錯也當成模型下架，一路把每個模型都標掉。
MODEL_MISSING = re.compile(
    r"model_not_found|model_decommissioned|decommissioned"
    r"|\bmodels?\b[\s\S]{0,160}?(?:does not exist|not found|no longer (?:available|supported)"
    r"|has been (?:deprecated|retired|removed))"
    r"|not_found_error[\s\S]{0,80}?\bmodel\b|not a valid model|no endpoints found for", re.I | re.A)
# 餘額／帳單：等多久都不會好。⛔ 不能只比對 billing、credits 這種字：Groq 每分鐘限流的訊息有 settings/billing 網址、
# Gemini 每分鐘限流寫「check your plan and billing details」、OpenRouter 每日上限寫「Add 10 credits」，都不是沒錢。
BILLING = re.compile(
    r"insufficient[_\s-]?(?:balance|credits?|funds|quota)|credit balance is too low"
    r"|(?:out of|no remaining|not enough) credits?|requires more credits|can only afford"
    r"|credits? (?:are |have been )?(?:depleted|exhausted)|prepayment|payment required"
    r"|(?:billing|spend(?:ing)?) (?:hard )?limit|exceeded (?:your |the )?(?:monthly )?(?:budget|spend(?:ing)?)\b"
    r"|reached your specified (?:api )?usage limits?|billing[_\s]not[_\s]active"
    r"|billing account[\s\S]{0,60}?(?:disabled|closed|suspended)"
    r"|account[_\s](?:is[_\s])?(?:deactivated|suspended|inactive|not[_\s]active)|arrears", re.I | re.A)
# 每日／每週／每月額度：Groq 寫 tokens per day (TPD)／requests per day (RPD)、OpenRouter 寫 free-models-per-day、
# Gemini 的 quotaId 寫 ...PerDay...。「today」不算（Groq 每分鐘限流也寫 Upgrade to Dev Tier today）。
LONG_WINDOW = re.compile(r"per[\s_-]?(?:day|week|month)|\b(?:rpd|tpd)\b|\b(?:daily|weekly|monthly)\b", re.I | re.A)
# 訊息裡的等待時間：Groq「try again in 7m26.4s」、Gemini「Please retry in 17.3s」、「retry after 30 seconds」
TEXT_DELAY = re.compile(r"(?:try again|retry)\s+(?:in|after)\s+((?:\d+(?:\.\d+)?(?:ms|h|m|s))+|\d+(?:\.\d+)?)"
                        r"\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)?", re.I | re.A)
NUMBER = re.compile(r"\d+(?:\.\d+)?", re.A)
DURATION = re.compile(r"(?:\d+(?:\.\d+)?(?:ms|h|m|s))+", re.I | re.A)
DUR_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)", re.I | re.A)
UNIT = {"ms": 0.001, "h": 3600, "m": 60, "s": 1}
# Retry-After 可以是 HTTP 日期。先比對格式再解析：JS 的 Date.parse 連「test 1」都當成日期，兩邊才會一致
HTTP_DATE = re.compile(r"(?:[A-Za-z]{3},\s*)?\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}(?::\d{2})?\s*(?:GMT|UTC|[+-]\d{4})", re.A)
# 參數量（8b、70b、a22b）不算版本：不然 qwen3-32b 的 32 會大過 qwen3.6-27b 的 6，舊版反而排前面
SIZE = re.compile(r"(\d+(?:\.\d+)?)b(?![a-z0-9])", re.I | re.A)
THINK = re.compile(r"^\s*<think>[\s\S]*?</think>\s*", re.I)
THINK_OPEN = re.compile(r"^\s*<think>", re.I)
# _http 把這些回應標頭放進 body，classify() 只看 body 一個地方（測試用的假 http 也照這樣做）
HEADER_KEYS = (("retry-after", "_retry_after"), ("x-ratelimit-reset", "_ratelimit_reset"))
COOL_NOTE = {"rate": "限流", "quota": "額度用完"}

# 冷卻紀錄：（供應商, 金鑰指紋, 模型）→（到期時間, 原因）。放在模組層級、整個程式共用：
# 有的 App 每次呼叫都新建 Router（例如在函式裡），記在物件上的話等於沒記。
_COOLDOWNS = {}
_MISSING = object()   # 模型不存在（不用字串當記號：模型真的回「MISSING」兩個字也不會搞混）
_ESCALATE = object()  # 回覆不合格、要升級（同上，不用字串）


class RouterError(Exception):
    pass


def reset_cooldowns():
    """清掉所有冷卻紀錄：測試用，或確定額度已經恢復、想馬上全部重試時用。"""
    _COOLDOWNS.clear()


def _fingerprint(keys):
    """金鑰組的指紋（FNV-1a）：不同帳號的冷卻分開記——同一個程式服務兩個帳號時，A 的額度用完不該連累 B。
    只是拿來分組，不存、不印金鑰本身。"""
    h = 0x811C9DC5
    for byte in "\n".join(keys).encode("utf-8"):
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def max_calls_from_env(log=None):
    """讀 MODEL_ROUTER_MAX_CALLS；看不懂或小於 1 就用預設，並提醒（不默默吞掉設定錯誤）。"""
    raw = os.environ.get("MODEL_ROUTER_MAX_CALLS", "").strip()
    if not raw:
        return DEFAULT_MAX_CALLS
    try:
        n = int(raw)
        if n >= 1:
            return n
    except ValueError:
        pass
    if log:
        log(f"⚠️ MODEL_ROUTER_MAX_CALLS={raw!r} 看不懂（要填 1 以上的整數），改用預設 {DEFAULT_MAX_CALLS}")
    return DEFAULT_MAX_CALLS


def is_paid(cfg, model, override=None):
    """這次呼叫會不會收費：Router(paid=...) 覆寫 > PROVIDERS 設定。"""
    if override is not None:
        return bool(override)
    p = cfg.get("paid", True)   # 沒標的新供應商當付費：寧可少重試，不要重複收費
    if p == "free_suffix":
        return not str(model).endswith(":free")
    return bool(p)


def version_key(mid):
    """先比版本號（gemini-2.5 > gemini-2.0；qwen3.6 > qwen3；claude-opus-5-5 > claude-opus-5），
    同版本再比參數量（大的先），最後名稱短的先。"""
    sizes = [float(x) for x in SIZE.findall(mid)]
    nums = [int(n) for n in re.findall(r"\d+", SIZE.sub(" ", mid), re.A)]
    return (nums, sizes, -len(mid))


def rank(ids, provider, tier, avoid=()):
    """依偏好序排出候選（最多 5 個）；正式版優先於預覽版。"""
    pool = [m for m in ids if m not in avoid and not NON_CHAT.search(m)]
    out = []
    for stable_only in (True, False):
        for pat in PREFS[provider][tier]:
            hit = [m for m in pool if re.search(pat, m, re.I) and (not stable_only or not UNSTABLE.search(m))]
            for m in sorted(hit, key=version_key, reverse=True):
                if m not in out:
                    out.append(m)
    return out[:5]


def pick_tier(messages, hint=None):
    """auto：依內容判斷難度。寧可先用便宜的，不合格再由 chat() 自動升級。"""
    if hint in TIERS:
        return hint
    text = " ".join(str(m.get("content", "")) for m in messages)
    low = text.lower()
    if re.search(r"程式|code|debug|架構|重構|規格|長篇|完整報告|refactor|architecture", low) or len(text) > 60000:
        return "best"
    if re.search(r"分析|推理|為什麼|比較|策略|評估|判斷|預測|analy|reason|why|compare|evaluate|strategy", low) or len(text) > 8000:
        return "smart"
    return "fast"


# ── 錯誤回應分類（兩個語言共用 assets/router_cases.json 的案例）──
def parse_delay(value, now=None):
    """「要等多久」換成秒數；看不懂回 None（不猜）。
    支援 20、"20"、"2.5s"、"520ms"、"5m"（＝300 秒，不是 5 秒）、"1m30s"、"1h2m3s"，
    以及 HTTP 日期（Retry-After 規格允許，例如 "Fri, 15 Jan 2027 08:00:30 GMT"，算離 now 還有幾秒）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 and math.isfinite(value) else None
    s = str(value).strip()
    if NUMBER.fullmatch(s):
        return float(s)
    if DURATION.fullmatch(s):
        total = 0.0
        for num, unit in DUR_PART.findall(s):
            total += float(num) * UNIT[unit.lower()]
        return total
    if HTTP_DATE.fullmatch(s):
        try:
            when = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        return max(0.0, when.timestamp() - (time.time() if now is None else now))
    return None


def text_delay(text):
    """從錯誤訊息找等待時間（try again in 7m26.4s／Please retry in 17.3s／retry after 30 seconds）。"""
    m = TEXT_DELAY.search(text or "")
    if not m:
        return None
    num, unit = m.group(1), (m.group(2) or "").lower()
    if not NUMBER.fullmatch(num):
        return parse_delay(num)
    mult = 60 if unit.startswith("min") else 3600 if unit.startswith(("hour", "hr")) else 1
    return float(num) * mult


def _reset_delay(value, now):
    """x-ratelimit-reset 是「恢復的時間點」（OpenRouter 用毫秒時間戳）；看起來不像時間戳的不猜。"""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if x > 1e12:
        x /= 1000.0
    return max(0.0, x - now) if x > 1e9 else None


def _flat(body, limit=4000):
    """錯誤回應裡所有字串值串成一行（用 | 隔開，避免把兩個欄位接成一句話誤判）。"""
    out = []

    def walk(v, depth):
        if depth > 20:
            return
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v:
                walk(x, depth + 1)
    walk(body, 0)
    return " | ".join(out)[:limit]


def _gemini_details(body):
    """Gemini 的 error.details：QuotaFailure 的 quotaId（PerMinute／PerDay）、quotaValue 是不是 0，
    以及 RetryInfo 的 retryDelay。"""
    err = body.get("error") if isinstance(body, dict) else None
    details = err.get("details") if isinstance(err, dict) else None
    ids, retry, zero = [], None, False
    for d in details if isinstance(details, list) else []:
        if not isinstance(d, dict):
            continue
        kind = str(d.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            violations = d.get("violations")
            for v in violations if isinstance(violations, list) else []:
                if isinstance(v, dict) and v.get("quotaId"):
                    ids.append(str(v["quotaId"]))
                    zero = zero or str(v.get("quotaValue")) == "0"
        elif kind.endswith("RetryInfo") and retry is None:
            retry = d.get("retryDelay")
    return ids, retry, zero


def _meta_reset(body):
    """OpenRouter 把限流標頭放在 error.metadata.headers 裡（例如 X-RateLimit-Reset）。"""
    err = body.get("error") if isinstance(body, dict) else None
    meta = err.get("metadata") if isinstance(err, dict) else None
    hdrs = meta.get("headers") if isinstance(meta, dict) else None
    if isinstance(hdrs, dict):
        for k, v in hdrs.items():
            if str(k).lower() == "x-ratelimit-reset":
                return v
    return None


def _first(*values):
    for v in values:
        if v is not None:
            return v
    return None


def with_headers(body, headers):
    """把判斷限流要用的回應標頭放進 body（_retry_after、_ratelimit_reset）；名稱不分大小寫，body 已有的不覆蓋。"""
    if not isinstance(body, dict) or not headers:
        return body
    if isinstance(headers, dict):
        low = {str(k).lower(): v for k, v in headers.items()}
        get = low.get
    else:
        get = headers.get        # urllib 的標頭本來就不分大小寫
    for name, field in HEADER_KEYS:
        v = get(name)
        if v not in (None, "") and field not in body:
            body[field] = str(v)
    return body


def classify(status, body, now=None):
    """把失敗的回應分類，回 {"kind": ..., "delay": 秒數或 None}。
    kind：billing（餘額／帳單）／missing（模型不存在）／config（404 但不是模型問題，多半網址錯）／auth（金鑰被拒）／
          quota（每日／每月額度用完）／rate（每分鐘限流）／server（5xx、網路斷線）／other。
    delay 依序看：Retry-After 標頭 → Gemini retryDelay → 訊息裡的 try again in … → x-ratelimit-reset 時間戳。"""
    now = time.time() if now is None else now
    b = body if isinstance(body, dict) else {}
    text = _flat(body)
    if status == 402 or (status in (400, 401, 403, 429) and BILLING.search(text)):
        return {"kind": "billing", "delay": None}
    if status in (400, 404) and MODEL_MISSING.search(text):
        return {"kind": "missing", "delay": None}
    if status == 404:
        return {"kind": "config", "delay": None}
    if status in (401, 403):
        return {"kind": "auth", "delay": None}
    if status != 429 and status != 0 and status < 500:
        return {"kind": "other", "delay": None}
    ids, retry, zero = _gemini_details(b)
    if status == 429 and zero:
        # quotaValue 是 0（訊息寫 limit: 0）：這個方案根本沒有這個模型的額度，就算 quotaId 是 PerMinute，
        # 等多久都不會好；附的 retryDelay 沒有意義，當成沒說（先避開 1 小時）
        return {"kind": "quota", "delay": None}
    delay = _first(parse_delay(b.get("_retry_after"), now), parse_delay(retry, now), text_delay(text),
                   _reset_delay(b.get("_ratelimit_reset"), now), _reset_delay(_meta_reset(b), now))
    if status == 429:
        # Gemini 有結構化的 quotaId 就只看它：每分鐘限流的訊息也寫「exceeded your current quota」，看那句話會誤判
        long_window = any(LONG_WINDOW.search(i) for i in ids) if ids else bool(LONG_WINDOW.search(text))
        return {"kind": "quota" if long_window else "rate", "delay": delay}
    return {"kind": "server", "delay": delay}


def strip_think(text):
    """推理模型（Groq 的 qwen 等）會把思考過程用 <think>…</think> 放在回覆開頭：拿掉，只留答案。
    只有 <think> 沒有結尾＝想到一半被 max_tokens 截斷、沒有答案 → 回空字串（chat() 會當成空白回覆換下一個）。"""
    out = THINK.sub("", text or "", count=1)
    return "" if THINK_OPEN.match(out) else out


def human(seconds):
    """秒數換成好讀的中文（四捨五入用 +0.5 取整，跟 JS 版一致）。"""
    s = max(0.0, float(seconds))
    if s < 90:
        return f"{int(s + 0.5)} 秒"
    if s < 5400:
        return f"{int(s / 60 + 0.5)} 分鐘"
    return f"{int(s / 3600 + 0.5)} 小時"


class Router:
    def __init__(self, provider, keys=None, http=None, sleep=time.sleep, log=None, max_calls=None, paid=None, clock=None):
        if provider not in PROVIDERS:
            raise RouterError(f"不支援的供應商：{provider}（可用：{', '.join(PROVIDERS)}）")
        self.p = provider
        self.cfg = PROVIDERS[provider]
        env_keys = [k for k in os.environ.get(self.cfg["key_env"], "").split(",") if k.strip()]
        self.keys = [k.strip() for k in (keys or env_keys) if k and k.strip()]
        self.http = http or _http
        self.sleep = sleep
        self.clock = clock or time.time
        self.log = log or (lambda m: print(m, file=sys.stderr))
        self.max_calls = max_calls if max_calls else max_calls_from_env(self.log)
        self.paid = paid
        self._ids, self._at, self._good = None, 0.0, {}
        self._calls = 0
        self._fp = _fingerprint(self.keys)

    # ── 模型清單 ──
    def _headers(self, key):
        if self.cfg["style"] == "gemini":
            return {"x-goog-api-key": key}
        if self.cfg["style"] == "anthropic":
            return {"x-api-key": key, "anthropic-version": "2023-06-01"}
        return {"Authorization": "Bearer " + key}

    def models(self, refresh=False):
        if not refresh and self._ids is not None and time.time() - self._at < TTL:
            return self._ids
        last = ""
        for i, k in enumerate(self.keys or [""]):
            status, body = self.http("GET", self.cfg["list"], self._headers(k), None)
            if status == 200:
                data = body.get("data") or body.get("models") or []
                ids = []
                for m in data:
                    mid = str(m.get("id") or m.get("name") or "").replace("models/", "")
                    methods = m.get("supportedGenerationMethods")
                    if mid and (methods is None or "generateContent" in methods):
                        ids.append(mid)
                self._ids, self._at = ids, time.time()
                return ids
            last = f"第 {i + 1} 把金鑰 HTTP {status}"   # 只說第幾把，絕不印金鑰
        self.log(f"⚠️ [{self.p}] 取不到模型清單（{last}），改用上次成功過的模型")
        self._ids, self._at = [], time.time()
        return []

    # ── 冷卻（記在模型上，不是整家供應商）──
    def _cooling(self, model):
        """模型還在冷卻就回（到期時間, 原因），否則 None。"""
        v = _COOLDOWNS.get((self.p, self._fp, model))
        return v if v and v[0] > self.clock() else None

    def _cool(self, model, seconds, why):
        """記下這個模型要避開多久，回實際採用的秒數。已經有更久的冷卻（例如額度用完）就不縮短。"""
        seconds = min(MAX_COOLDOWN, max(0.0, float(seconds)))
        until = self.clock() + seconds
        k = (self.p, self._fp, model)
        old = _COOLDOWNS.get(k)
        if not old or old[0] < until:
            _COOLDOWNS[k] = (until, why)
        return seconds

    def _candidates(self, tier, avoid=()):
        """（現在可以試的, 冷卻中的[(模型, (到期, 原因))]）。覆寫 > 官方清單排序 > 上次成功過的。"""
        override = os.environ.get(f"MODEL_ROUTER_{self.p.upper()}_{tier.upper()}")
        cands = [override] if override else []
        ids = self.models()
        cands += [m for m in rank(ids, self.p, tier, avoid) if m not in cands]
        good = self._good.get(tier)
        if good and good not in avoid and good not in cands:
            cands.append(good)
        if not cands and ids:
            self.log(f"⚠️ [{self.p}] 等級 {tier} 的偏好序一個都沒配到；目前有的模型：{', '.join(ids[:25])}")
        ready, cooling = [], []
        for m in cands:
            c = self._cooling(m)
            if c:
                cooling.append((m, c))
            else:
                ready.append(m)
        return ready, cooling

    def pick(self, tier, avoid=()):
        """回傳這個等級現在要依序嘗試的模型清單（冷卻中的先跳過）。"""
        return self._candidates(tier, avoid)[0]

    # ── 呼叫 ──
    def _request(self, model, messages, max_tokens):
        sys_txt = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        chat = [m for m in messages if m.get("role") != "system"]
        st = self.cfg["style"]
        if st == "gemini":
            url = self.cfg["chat"].format(model=model)
            body = {"contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in chat]}
            if sys_txt:
                body["systemInstruction"] = {"parts": [{"text": sys_txt}]}
        elif st == "anthropic":
            url = self.cfg["chat"]
            body = {"model": model, "max_tokens": max_tokens, "messages": chat}
            if sys_txt:
                body["system"] = sys_txt
        else:
            url = self.cfg["chat"]
            body = {"model": model, "messages": messages, "max_tokens": max_tokens}
        return url, body

    def _text(self, body):
        st = self.cfg["style"]
        if st == "gemini":
            parts = ((body.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            return "".join(p.get("text", "") for p in parts)
        if st == "anthropic":
            return "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
        return strip_think(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")

    def _post(self, url, key, payload, tried):
        # 上限算的是「送出去的請求」，包含重試與換金鑰；模型清單（GET）不收費所以不算
        if self._calls >= self.max_calls:
            raise RouterError(f"[{self.p}] 這次已經送出 {self._calls} 次 AI 請求（上限 {self.max_calls}）還沒成功，"
                              f"為了避免費用失控先停下。試過：{'；'.join(tried) or '（無）'}。"
                              f"請確認 {self.cfg['key_env']} 有設定、額度還夠，稍後再試；"
                              "確定要多試幾次就調高環境變數 MODEL_ROUTER_MAX_CALLS。")
        self._calls += 1
        return self.http("POST", url, self._headers(key), payload)

    def _excerpt(self, body):
        """供應商回應的摘要（給設定錯誤的訊息用）：先把自己的金鑰遮掉再截斷，截斷不會留下半把金鑰。"""
        s = _flat(body)
        for k in self.keys:
            if k:
                s = s.replace(k, "***")
        return s[:200] or "（沒有內容）"

    def _call(self, model, messages, max_tokens, tried):
        url, payload = self._request(model, messages, max_tokens)
        paid = is_paid(self.cfg, model, self.paid)
        no_money = False
        for ki, key in enumerate(self.keys or [""]):
            for attempt in range(4):
                status, body = self._post(url, key, payload, tried)
                if status == 200:
                    text = self._text(body)
                    if text.strip():
                        _COOLDOWNS.pop((self.p, self._fp, model), None)
                        return text
                    tried.append(f"{model}（回了空白）")
                    return None
                info = classify(status, body, self.clock())
                kind, delay = info["kind"], info["delay"]
                if kind == "missing":
                    tried.append(f"{model}（已下架或不存在）")
                    self._ids = None            # 自我修復：下次重新問官方
                    return _MISSING
                if kind == "config":
                    # 每個模型共用同一個網址：再換模型只會一直 404，也不能把模型標成下架
                    tried.append(f"{model}（HTTP 404，回應沒說是模型不存在）")
                    raise RouterError(f"[{self.p}] 呼叫 {model} 回 HTTP 404，但回應沒說是模型不存在：多半是網址設定錯"
                                      "（API 路徑、版本，或中間的 Proxy／Worker），不是模型下架，所以沒有把模型標成下架。"
                                      f"請檢查 PROVIDERS 裡 {self.p} 的 chat 網址。供應商回應：{self._excerpt(body)}")
                if kind == "billing":
                    tried.append(f"{model}（第 {ki + 1} 把金鑰：帳戶餘額或額度不足 {status}）")
                    no_money = True
                    break                       # 帳單是整個帳號的事：下一把金鑰可能是別的帳號
                if kind == "auth":
                    tried.append(f"{model}（第 {ki + 1} 把金鑰被拒 {status}）")
                    break                       # 換下一把金鑰
                if kind == "quota":
                    # 每日／每月額度：等幾秒不會恢復，重試只會浪費次數上限
                    cd = self._cool(model, QUOTA_COOLDOWN if delay is None else max(delay, QUOTA_MIN_COOLDOWN), "quota")
                    tried.append(f"{model}（每日／每月額度用完，約 {human(cd)} 後恢復；不重試，換下一個模型）")
                    self.log(f"⚠️ [{self.p}] {model} 每日／每月額度用完，約 {human(cd)} 後恢復；這段時間改用同一家的其他模型")
                    return None
                if status == 0 and paid:
                    # 逾時／連線中斷時，請求可能已經被處理（也收費了），只是回應沒送回來：不重送、也不換金鑰重送
                    tried.append(f"{model}（逾時或連線中斷；付費服務不重送，避免重複收費）")
                    return None
                wait = min(30, 2 ** (attempt + 1))
                if delay is not None:
                    wait = max(wait, delay)
                if kind == "rate":
                    if wait > MAX_WAIT:
                        self._cool(model, wait, "rate")
                        tried.append(f"{model}（限流，對方要求等 {human(wait)}；先換下一個模型）")
                        return None
                    if attempt < 3:
                        self.sleep(wait)
                        continue
                    self._cool(model, wait, "rate")
                    tried.append(f"{model}（限流 429，等了 3 次還是滿；先換下一個模型）")
                    return None
                if kind == "server":
                    if attempt < 3:
                        self.sleep(min(MAX_WAIT, wait))
                        continue
                    tried.append(f"{model}（服務忙碌 {status}）")
                    return None
                tried.append(f"{model}（HTTP {status}）")
                return None
        if no_money:
            raise RouterError(f"[{self.p}] 帳戶餘額或額度不足：這不是限流，等多久都不會恢復，所以已停止嘗試 {self.p} 的其他模型。"
                              f"請到供應商後台檢查帳單——儲值餘額、付款方式、每月花費上限（金鑰讀 {self.cfg['key_env']}）。"
                              f"試過：{'；'.join(tried)}。")
        return None

    def _settle(self, t, model, r, validate, tried, avoid, done):
        """處理一次 _call 的結果：成功回文字；_ESCALATE＝不合格要升級；None＝換下一個。"""
        if r is _MISSING:
            avoid.add(model)
            return None
        if r is None:
            if not self._cooling(model):
                done.add(model)         # 不是因為冷卻失敗的（5xx、空白、HTTP 400…）這次不再試
            return None
        if validate and not validate(r):
            tried.append(f"{model}（回覆不合格，升級）")
            done.add(model)
            return _ESCALATE
        self._good[t] = model
        return r

    def chat(self, tier, messages, validate=None, max_tokens=1024, escalate=True):
        """回傳模型的文字回覆。tier 可用 fast／smart／best／auto。"""
        tier = pick_tier(messages) if tier == "auto" else tier
        if tier not in TIERS:
            raise RouterError(f"不認識的等級：{tier}（用 fast／smart／best／auto）")
        self._calls = 0                         # 上限是每次 chat() 各自計算
        order = list(TIERS[TIERS.index(tier):]) if escalate else [tier]
        tried, avoid, done, seen = [], set(), set(), []
        for t in order:
            # 同一個模型可能在好幾個等級（例如 qwen）：這次已經失敗過的不再浪費次數
            ready, cooling = self._candidates(t, avoid | done)
            for m, (until, why) in cooling:
                if m not in [s for _, s in seen]:
                    tried.append(f"{m}（{COOL_NOTE.get(why, '冷卻')}，冷卻中，約 {human(until - self.clock())} 後恢復，這次跳過）")
                seen.append((t, m))
            for model in ready:
                seen.append((t, model))
                r = self._settle(t, model, self._call(model, messages, max_tokens, tried), validate, tried, avoid, done)
                if r is _ESCALATE:
                    break                       # 換更高等級
                if r is not None:
                    return r
        r = self._second_pass(seen, avoid, done, messages, max_tokens, tried, validate)
        if r is not None:
            return r
        now = self.clock()
        lefts = [c[0] - now for c in (self._cooling(m) for _, m in seen if m not in avoid) if c]
        soon = f"冷卻中的模型最快約 {human(min(lefts))} 後恢復，到時再試；" if lefts else ""
        raise RouterError(f"[{self.p}] 所有模型都失敗了，試過：{'；'.join(tried) or '沒有可用的模型'}。{soon}"
                          f"請確認 {self.cfg['key_env']} 有設定、額度還夠；要指定模型可設 MODEL_ROUTER_{self.p.upper()}_<等級>。")

    def _second_pass(self, seen, avoid, done, messages, max_tokens, tried, validate):
        """第一輪全失敗、而且有模型只是在冷卻（多半是每分鐘限流）：挑 60 秒內會恢復的，從最快的開始等一下再試。
        不這樣做的話，批次工作遇到每個模型剛好都在限流，接下來每一筆都會直接失敗。"""
        now, queue, picked = self.clock(), [], set()
        for t, m in seen:
            if m in picked or m in avoid or m in done:
                continue
            picked.add(m)
            c = self._cooling(m)
            left = c[0] - now if c else 0.0
            if left <= MAX_WAIT:
                queue.append((left, t, m))
        queue.sort(key=lambda x: x[0])          # 穩定排序：一樣快恢復的照原本偏好順序
        for _, t, model in queue:
            c = self._cooling(model)
            if c and c[0] > self.clock():
                self.sleep(c[0] - self.clock())
            r = self._settle(t, model, self._call(model, messages, max_tokens, tried), validate, tried, avoid, done)
            if r is not None and r is not _ESCALATE:
                return r
        return None


def _http(method, url, headers, payload):
    """回 (狀態碼, JSON)。網路錯誤回 (0, {})，交給上層重試。
    錯誤回應：Retry-After、x-ratelimit-reset 標頭放進 body；不是 JSON 的內容（HTML 錯誤頁）放 _raw，分類時看得到。"""
    data = json.dumps(payload).encode() if payload is not None else None
    h = dict(headers, **({"Content-Type": "application/json"} if data else {}))
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read() or b"{}")
            # 中間的 Proxy 可能回 JSON 陣列：包成物件，後面 .get() 才不會整個當掉（跟 JS 版一樣）
            return r.status, body if isinstance(body, dict) else {"_raw": json.dumps(body, ensure_ascii=False)[:500]}
    except urllib.error.HTTPError as e:
        raw = e.read() or b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {"_raw": raw[:500].decode("utf-8", "replace")}
        if not isinstance(body, dict):
            body = {"_raw": json.dumps(body, ensure_ascii=False)[:500]}
        return e.code, with_headers(body, e.headers)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return 0, {}


def _same(a, b):
    """兩個秒數一樣嗎（小數誤差 1e-6 內）；兩個都是 None 也算一樣。"""
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) < 1e-6


def _ok_body(provider, text):
    st = PROVIDERS[provider]["style"]
    if st == "gemini":
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    if st == "anthropic":
        return {"content": [{"type": "text", "text": text}]}
    return {"choices": [{"message": {"content": text}}]}


def _shared_cases(t, strict):
    """跑 assets/router_cases.json（跟 JS 版同一份）：分類、等待時間、排序、<think>、整段情境。"""
    here = os.path.dirname(os.path.abspath(__file__))
    path = next((p for p in (os.path.join(here, "router_cases.json"), os.path.join(here, "..", "assets", "router_cases.json"))
                 if os.path.exists(p)), None)
    if not path:
        if strict:
            t(False, "找不到 router_cases.json（兩個語言共用的測試案例；--strict 一定要有）")
        else:
            print("⚠️ 找不到 router_cases.json，共用案例沒跑。只把模組複製進 App 時是正常的；"
                  "要跑完整測試就把 assets/router_cases.json 放在模組旁邊，或在 skl 裡跑。")
        return
    with open(path, encoding="utf-8") as f:
        C = json.load(f)
    t0 = C["now"]

    def sample(item):
        s = C["samples"][item] if isinstance(item, str) else item
        return json.loads(json.dumps(s))          # 複本：with_headers 會改 body，不能動到共用的範例

    def ids_of(v):
        return [m["id"] for m in C["model_responses"][v]["data"]] if isinstance(v, str) else v

    for c in C["delays"]:
        got = parse_delay(c["value"], c.get("now"))
        t(_same(got, c["seconds"]), f"等待時間：{c['name']}（{c['value']!r} → 期望 {c['seconds']}，得到 {got}）")
    for c in C["text_delays"]:
        got = text_delay(c["text"])
        t(_same(got, c["seconds"]), f"訊息裡的等待時間：{c['text']!r} → 期望 {c['seconds']}，得到 {got}")
    for c in C["classify"]:
        s = sample(c["sample"])
        info = classify(s["status"], with_headers(s.get("body", {}), s.get("headers")), c.get("now", t0))
        t(info["kind"] == c["kind"] and _same(info["delay"], c.get("delay")),
          f"分類：{c['name']}（期望 {c['kind']} {c.get('delay')}，得到 {info['kind']} {info['delay']}）")
    for c in C["think"]:
        got = strip_think(c["text"])
        t(got == c["out"], f"<think> 處理：{c['text']!r} → {got!r}")
    for c in C["rank"]:
        got = rank(ids_of(c["ids"]), c["provider"], c["tier"])
        t(got == c["expect"], f"排序：{c['name']}（得到 {got}）")
    for c in C["pick"]:
        resp = C["model_responses"][c["response"]]
        r = Router(c["provider"], keys=["k"], http=lambda *a: (200, json.loads(json.dumps(resp))),
                   sleep=lambda s: None, log=lambda m: None)
        got = {tier: r.pick(tier) for tier in c["expect"]}
        t(got == c["expect"], f"挑模型：{c['name']}（得到 {got}）")
    for sc in C["scenarios"]:
        _run_scenario(t, C, sc, sample)


def _run_scenario(t, C, sc, sample):
    """整段情境：假 http（照真實格式回應）＋假時鐘；每一步都新建 Router，驗證冷卻是整個程式共用的。"""
    reset_cooldowns()
    clock = [float(C["now"])]
    queues = {k: list(v) for k, v in sc.get("responses", {}).items()}
    provider, models = sc["provider"], sc["models"]
    for i, step in enumerate(sc["steps"]):
        if "advance" in step:
            clock[0] += step["advance"]
            continue
        posts, waits = [], []

        def http(method, url, headers, payload):
            if method == "GET":
                if isinstance(models, str):
                    return 200, json.loads(json.dumps(C["model_responses"][models]))
                if provider == "gemini":
                    return 200, {"models": [{"name": "models/" + m, "supportedGenerationMethods": ["generateContent"]} for m in models]}
                return 200, {"data": [{"id": m} for m in models]}
            model = (payload or {}).get("model") or re.search(r"/models/([^/:]+):generateContent", url).group(1)
            key = (headers.get("Authorization", "").replace("Bearer ", "", 1)
                   or headers.get("x-goog-api-key") or headers.get("x-api-key") or "")
            posts.append(model)
            q = queues.get(f"{model}#{key}") or queues.get(model)
            spec = sample((q.pop(0) if len(q) > 1 else q[0]) if q else "ok")
            if spec["status"] == 200:
                return 200, _ok_body(provider, spec.get("text", "ok:" + model))
            if spec["status"] == 0:
                return 0, {}
            return spec["status"], with_headers(spec.get("body", {}), spec.get("headers"))

        def sleep(s):
            waits.append(s)
            clock[0] += s
        r = Router(provider, keys=step.get("keys", ["k"]), http=http, sleep=sleep, log=lambda m: None,
                   clock=lambda: clock[0], max_calls=step.get("max_calls"))
        out = err = None
        try:
            out = r.chat(step.get("tier", "fast"), [{"role": "user", "content": "x"}], escalate=step.get("escalate", True))
        except RouterError as e:
            err = str(e)
        exp = step["expect"]
        ok = posts == exp["posts"]
        if "waits" in exp:
            ok = ok and len(waits) == len(exp["waits"]) and all(_same(a, b) for a, b in zip(waits, exp["waits"]))
        if "result" in exp:
            ok = ok and out == exp["result"]
        if "error_has" in exp:
            ok = ok and err is not None and all(s in err for s in exp["error_has"])
        t(ok, f"情境：{sc['name']}（第 {i + 1} 步）" + ("" if ok else f" posts={posts} waits={waits} out={out!r} err={err!r}"))
    reset_cooldowns()


def selftest(strict=False):
    ok = True

    def t(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    reset_cooldowns()
    ids = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "whisper-large-v3", "gpt-oss-120b", "llama-3.5-8b-instant"]
    t(rank(ids, "groq", "fast")[0] == "llama-3.5-8b-instant", "fast 挑最新的 8b（版本號比大小）")
    t("whisper-large-v3" not in rank(ids, "groq", "fast"), "語音模型被濾掉")
    t(rank(ids, "groq", "smart")[0] == "llama-3.3-70b-versatile", "smart 挑 70b")
    g = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-3.0-flash-preview", "gemini-2.5-flash-image", "gemini-2.5-pro"]  # guardrails: ignore（測試用假模型清單）
    t(rank(g, "gemini", "smart")[0] == "gemini-2.5-flash", "正式版優先於預覽版、圖片模型濾掉")  # guardrails: ignore（測試用假模型清單）
    t(rank(g, "gemini", "best")[0] == "gemini-2.5-pro", "best 挑 pro")  # guardrails: ignore（測試用假模型清單）
    a = ["claude-haiku-4-5-20251001", "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5"]  # guardrails: ignore（測試用假模型清單）
    t(rank(a, "anthropic", "fast")[0] == "claude-haiku-5-5" and rank(a, "anthropic", "best")[0] == "claude-opus-5-5",  # guardrails: ignore（測試用假模型清單）
      "Claude：fast→最新 Haiku、best→最新 Opus")
    t(pick_tier([{"content": "把標題翻成中文"}]) == "fast", "auto：翻譯 → fast")
    t(pick_tier([{"content": "分析這檔股票為什麼下跌"}]) == "smart", "auto：分析 → smart")
    t(pick_tier([{"content": "幫我 debug 這段程式"}]) == "best", "auto：程式 → best")

    class Fake:
        def __init__(self, models, script):
            self.models, self.script, self.calls = models, script, []

        def __call__(self, method, url, headers, payload):
            self.calls.append((method, url, (payload or {}).get("model"), dict(headers)))
            if method == "GET":
                return 200, {"data": [{"id": m} for m in self.models]}
            return self.script(payload.get("model"), headers)

    f = Fake(ids, lambda m, h: (404, {"error": {"code": "model_decommissioned"}}) if m == "llama-3.5-8b-instant"
             else (200, {"choices": [{"message": {"content": "你好 " + m}}]}))
    r = Router("groq", keys=["k1"], http=f, sleep=lambda s: None, log=lambda m: None)
    out = r.chat("fast", [{"role": "user", "content": "hi"}])
    t(out == "你好 llama-3.1-8b-instant", "模型被下架（404）→ 自動換下一個")
    t(sum(1 for c in f.calls if c[0] == "GET") >= 1 and r._good["fast"] == "llama-3.1-8b-instant", "記住上次成功的模型")

    seen = []

    def busy(m, h):
        seen.append(m)
        return (429, {"_retry_after": "1"}) if len(seen) < 3 else (200, {"choices": [{"message": {"content": "ok"}}]})
    waits = []
    r = Router("groq", keys=["k1"], http=Fake(ids, busy), sleep=waits.append, log=lambda m: None)
    t(r.chat("fast", [{"role": "user", "content": "hi"}]) == "ok" and waits == [2, 4], "429 → 指數退避 2、4 秒後成功")

    def keyed(m, h):
        return (401, {}) if h.get("Authorization") == "Bearer bad" else (200, {"choices": [{"message": {"content": "k2"}}]})
    r = Router("groq", keys=["bad", "good"], http=Fake(ids, keyed), sleep=lambda s: None, log=lambda m: None)
    t(r.chat("fast", [{"role": "user", "content": "hi"}]) == "k2", "第一把金鑰被拒 → 換第二把")

    def tiered(m, h):
        return 200, {"choices": [{"message": {"content": "壞" if "8b" in m else "好的分析"}}]}
    r = Router("groq", keys=["k"], http=Fake(ids, tiered), sleep=lambda s: None, log=lambda m: None)
    t(r.chat("fast", [{"role": "user", "content": "x"}], validate=lambda s: len(s) > 2) == "好的分析",
      "回覆不合格 → 自動升級到 smart")

    r = Router("groq", keys=["secret-key-123"], http=Fake(ids, lambda m, h: (500, {})), sleep=lambda s: None, log=lambda m: None)
    try:
        r.chat("best", [{"role": "user", "content": "x"}])
        t(False, "全部失敗要丟錯")
    except RouterError as e:
        t("secret-key-123" not in str(e) and "試過" in str(e) and "GROQ_API_KEY" in str(e), "全部失敗：中文可行動錯誤、不洩漏金鑰")

    os.environ["MODEL_ROUTER_GROQ_FAST"] = "my-custom-model"
    f = Fake(ids, lambda m, h: (200, {"choices": [{"message": {"content": m}}]}))
    r = Router("groq", keys=["k"], http=f, sleep=lambda s: None, log=lambda m: None)
    t(r.chat("fast", [{"role": "user", "content": "x"}]) == "my-custom-model", "環境變數可指定模型")
    del os.environ["MODEL_ROUTER_GROQ_FAST"]

    gf = Fake([], None)
    gf.script = lambda m, h: (200, {"candidates": [{"content": {"parts": [{"text": "G:" + m}]}}]})

    def gem_http(method, url, headers, payload):
        if method == "GET":
            return 200, {"models": [{"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                                    {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]}]}
        gf.calls.append(url)
        return 200, {"candidates": [{"content": {"parts": [{"text": "G"}]}}]}
    r = Router("gemini", keys=["gk"], http=gem_http, sleep=lambda s: None, log=lambda m: None)
    t(r.chat("smart", [{"role": "system", "content": "s"}, {"role": "user", "content": "x"}]) == "G"
      and "gemini-2.5-flash:generateContent" in gf.calls[0], "Gemini：只挑能聊天的模型、網址正確")

    def ant_http(method, url, headers, payload):
        if method == "GET":
            return 200, {"data": [{"id": "claude-haiku-5-5"}, {"id": "claude-opus-5-5"}]}  # guardrails: ignore（測試用假模型清單）
        return 200, {"content": [{"type": "text", "text": "A:" + payload["model"] + ":" + payload.get("system", "")}]}
    r = Router("anthropic", keys=["ak"], http=ant_http, sleep=lambda s: None, log=lambda m: None)
    t(r.chat("fast", [{"role": "system", "content": "S"}, {"role": "user", "content": "x"}]) == "A:claude-haiku-5-5:S",
      "Claude：system 放對位置、fast 用 Haiku")
    # ── 付費服務逾時不重送、總次數上限 ──
    def ant_seq(script):
        posts = []

        def h(method, url, headers, payload):
            if method == "GET":
                return 200, {"data": [{"id": "claude-haiku-5-5"}, {"id": "claude-haiku-4-5-20251001"}]}  # guardrails: ignore（測試用假模型清單）
            posts.append(payload["model"])
            return script(payload["model"], len(posts))
        return h, posts
    h, posts = ant_seq(lambda m, n: (0, {}) if n == 1 else (200, {"content": [{"type": "text", "text": "ok:" + m}]}))
    waits = []
    r = Router("anthropic", keys=["k1", "k2"], http=h, sleep=waits.append, log=lambda m: None)
    out = r.chat("fast", [{"role": "user", "content": "x"}], escalate=False)
    t(posts == ["claude-haiku-5-5", "claude-haiku-4-5-20251001"] and out == "ok:claude-haiku-4-5-20251001" and not waits,  # guardrails: ignore（測試用假模型清單）
      "付費（Claude）逾時：同一請求不重送、不換金鑰重送，直接換下一個候選")
    h, posts = ant_seq(lambda m, n: (0, {}))
    r = Router("anthropic", keys=["k"], http=h, sleep=lambda s: None, log=lambda m: None)
    try:
        r.chat("fast", [{"role": "user", "content": "x"}], escalate=False)
        t(False, "付費全部逾時要丟錯")
    except RouterError as e:
        t(len(posts) == 2 and "不重送" in str(e), "付費全部逾時：每個候選只送一次，錯誤說明為什麼沒重試")
    h, posts = ant_seq(lambda m, n: (429, {}) if n == 1 else ((529, {}) if n == 2 else
                                                              (200, {"content": [{"type": "text", "text": "ok"}]})))
    waits = []
    r = Router("anthropic", keys=["k"], http=h, sleep=waits.append, log=lambda m: None)
    t(r.chat("fast", [{"role": "user", "content": "x"}]) == "ok" and waits == [2, 4] and len(posts) == 3,
      "付費遇到 429／5xx（伺服器明確拒絕、沒處理）照舊退避重試")
    seen = []

    def or_http(method, url, headers, payload):
        if method == "GET":
            return 200, {"data": [{"id": "test/free-model:free"}]}
        seen.append(payload["model"])
        return (0, {}) if len(seen) < 3 else (200, {"choices": [{"message": {"content": "free-ok"}}]})
    r = Router("openrouter", keys=["k"], http=or_http, sleep=lambda s: None, log=lambda m: None)
    t(r.chat("fast", [{"role": "user", "content": "x"}]) == "free-ok" and len(seen) == 3,
      "OpenRouter 的 :free 模型不收費 → 逾時照樣重試")
    t(is_paid(PROVIDERS["openrouter"], "vendor/paid-model") and not is_paid(PROVIDERS["openrouter"], "x:free")
      and is_paid(PROVIDERS["anthropic"], "x") and not is_paid(PROVIDERS["groq"], "x")
      and is_paid(PROVIDERS["groq"], "x", True) and is_paid({}, "x"), "付費判斷：看 PROVIDERS、可覆寫、沒標的當付費")
    cnt = []

    def always500(method, url, headers, payload):
        if method == "GET":
            return 200, {"data": [{"id": m} for m in ids]}
        cnt.append(1)
        return 500, {}
    r = Router("groq", keys=["a", "b"], http=always500, sleep=lambda s: None, log=lambda m: None, max_calls=5)
    try:
        r.chat("fast", [{"role": "user", "content": "x"}])
        t(False, "超過上限要丟錯")
    except RouterError as e:
        t(len(cnt) == 5 and "上限 5" in str(e) and "MODEL_ROUTER_MAX_CALLS" in str(e), "總次數上限：送滿 5 次就停，中文說明怎麼調")
    cnt.clear()
    try:
        r.chat("fast", [{"role": "user", "content": "x"}])
    except RouterError:
        pass
    t(len(cnt) == 5, "上限每次 chat() 各自計算（下一次重新算）")
    os.environ["MODEL_ROUTER_MAX_CALLS"] = "3"
    t(Router("groq", keys=["k"], log=lambda m: None).max_calls == 3, "MODEL_ROUTER_MAX_CALLS 可調")
    warned = []
    os.environ["MODEL_ROUTER_MAX_CALLS"] = "abc"
    t(Router("groq", keys=["k"], log=warned.append).max_calls == DEFAULT_MAX_CALLS and warned,
      "MODEL_ROUTER_MAX_CALLS 看不懂 → 用預設並提醒")
    del os.environ["MODEL_ROUTER_MAX_CALLS"]
    t(Router("groq", keys=["k"], log=lambda m: None).max_calls == 12, "預設上限 12")
    try:
        Router("nope")
        t(False, "不支援的供應商要擋")
    except RouterError:
        t(True, "不支援的供應商會擋下")

    # ── _http：錯誤回應的標頭與非 JSON 內容（不連網：換掉 urlopen）──
    import io
    import email.message

    def fake_urlopen(status, body, headers):
        def opener(req, timeout=None):
            msg = email.message.Message()
            for k, v in headers.items():
                msg[k] = v
            raise urllib.error.HTTPError(req.full_url, status, "err", msg, io.BytesIO(body))
        return opener
    real = urllib.request.urlopen
    try:
        urllib.request.urlopen = fake_urlopen(429, b'{"error":{"message":"slow down"}}',
                                              {"Retry-After": "Fri, 15 Jan 2027 08:00:30 GMT", "X-RateLimit-Reset": "1800000060"})
        st, body = _http("POST", "https://example.invalid/x", {}, {"a": 1})
        t(st == 429 and body.get("_retry_after") == "Fri, 15 Jan 2027 08:00:30 GMT" and body.get("_ratelimit_reset") == "1800000060"
          and classify(st, body, 1800000000) == {"kind": "rate", "delay": 30.0},
          "_http：Retry-After 是日期也保留原字串（舊版只收純數字），x-ratelimit-reset 也帶上")
        urllib.request.urlopen = fake_urlopen(404, b"<html><h1>404 Not Found</h1></html>", {})
        st, body = _http("POST", "https://example.invalid/x", {}, {"a": 1})
        t(st == 404 and "404 Not Found" in body.get("_raw", "") and classify(st, body)["kind"] == "config",
          "_http：HTML 錯誤頁放進 _raw，404 判斷成網址設定錯")
    finally:
        urllib.request.urlopen = real

    # 設定錯誤的訊息會附上供應商回應，但自己的金鑰要遮掉
    def echo_key(method, url, headers, payload):
        if method == "GET":
            return 200, {"data": [{"id": "openai/gpt-oss-20b"}]}
        return 404, {"error": {"message": "no route for key sk-test-SECRET-123"}}
    r = Router("groq", keys=["sk-test-SECRET-123"], http=echo_key, sleep=lambda s: None, log=lambda m: None)
    try:
        r.chat("fast", [{"role": "user", "content": "x"}])
        t(False, "404 網址錯要丟錯")
    except RouterError as e:
        t("SECRET" not in str(e) and "***" in str(e) and "網址" in str(e), "404 網址錯：訊息附回應摘要、金鑰遮成 ***")

    _shared_cases(t, strict)
    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv):
    ap = argparse.ArgumentParser(description="AI 模型自動挑選與下架自動換（Groq／OpenRouter／Gemini／Claude）")
    sub = ap.add_subparsers(dest="cmd")
    sp = sub.add_parser("selftest", help="離線自我測試")
    sp.add_argument("--strict", action="store_true", help="找不到共用案例檔 router_cases.json 就算失敗（skl 的 CI 用）")
    lp = sub.add_parser("list", help="列出現在可用的模型與各等級會挑誰（金鑰讀環境變數）")
    lp.add_argument("provider", choices=list(PROVIDERS))
    a = ap.parse_args(argv[1:])
    if a.cmd == "selftest":
        return selftest(a.strict)
    if a.cmd == "list":
        r = Router(a.provider)
        if not r.keys:
            print(f"還沒設定 {PROVIDERS[a.provider]['key_env']}（這是還沒設定，不是壞掉）。")
            return 0
        ids = r.models(refresh=True)
        print(f"{a.provider} 目前有 {len(ids)} 個模型")
        for tier in TIERS:
            print(f"  {tier}: {', '.join(r.pick(tier)) or '（沒有配到）'}")
        return 0 if ids else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
