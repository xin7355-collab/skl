#!/usr/bin/env python3
"""model_router.py — 讓 App 自動挑 AI 模型、模型下架自動換、難的工作自動升級。

為什麼需要（使用者的 App 實際踩過）：
- 寫死模型名稱，供應商一下架就整批 404：StockAI-DB 的 llama-3.1-8b-instant 被 Groq 下架後，
  新聞標題翻譯、情緒判讀、垃圾過濾三件事一起壞掉，而且只顯示「API 錯誤 404」。
- 不同工作難度差很多：翻標題用最便宜的就好，股票分析才值得用大模型。

做法（從 StockAI-DB 的 groq_common.py 一般化而來）：
1. 讓官方自己說現在有哪些模型（/models），依「等級」的偏好序（正規表示式，不寫死版號）挑最新的。
2. 呼叫回 404／模型不存在 → 清快取、避開它、換下一個再打（自我修復）。
3. 429／5xx → 指數退避重試（2/4/8 秒，尊重 Retry-After）；401／403 → 換下一把金鑰。
   逾時／連線中斷（狀態 0）：免費供應商照樣退避重試；付費供應商不重送同一請求，直接換下一個候選
   ——對方可能已經處理完、只是回應沒送到，重送等於付兩次錢。429／5xx 是伺服器明確拒絕、沒有處理，照舊可重試。
4. 結果不合格（validate 回 False）→ 自動升一級（fast → smart → best）再試一次。
5. 一次 chat() 最多送出 MODEL_ROUTER_MAX_CALLS 次請求（預設 12），超過就停：
   3 個等級 × 5 個候選 × 多把金鑰 × 4 次重試，最壞會打出上百次，付費服務會燒錢。
6. 全部失敗時丟出中文、可行動的錯誤，列出試過哪些模型（絕不印出金鑰）。

等級：fast（分類、翻譯、摘要、大量小工作）／smart（分析、推理）／best（最難、最長）／auto（依內容自動判斷）。
指定模型（覆寫）：環境變數 MODEL_ROUTER_<供應商>_<等級>，例如 MODEL_ROUTER_GROQ_FAST=llama-3.3-70b-versatile。
呼叫次數上限：環境變數 MODEL_ROUTER_MAX_CALLS（或 Router(max_calls=...)）。
付費與否：照 PROVIDERS 的 paid；Groq／Gemini 開了付費帳單的話傳 Router(..., paid=True)，逾時就不重送。

用法：
  from model_router import Router
  r = Router("groq", keys=[os.environ["GROQ_API_KEY"]])
  text = r.chat("auto", [{"role": "user", "content": "把這則新聞標題翻成中文：..."}])
  python3 model_router.py list groq        # 列出現在可用的模型與各等級會挑誰（金鑰讀環境變數）
  python3 model_router.py selftest
"""

import argparse
import json
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
PREFS = {
    "groq": {"fast": [r"llama-\d+(\.\d+)?-8b", r"instant", r"gpt-oss-20b", r"llama"],
             "smart": [r"llama-\d+(\.\d+)?-70b", r"gpt-oss-120b", r"70b", r"llama"],
             "best": [r"gpt-oss-120b", r"llama-4", r"70b", r"llama"]},
    "openrouter": {"fast": [r"llama-\d+(\.\d+)?-8b.*:free$", r":free$"],
                   "smart": [r"70b.*:free$", r"(qwen|deepseek).*:free$", r":free$"],
                   "best": [r"(deepseek|qwen).*:free$", r"70b.*:free$", r":free$"]},
    "gemini": {"fast": [r"^gemini-\d+(\.\d+)?-flash-lite$", r"^gemini-\d+(\.\d+)?-flash$"],
               "smart": [r"^gemini-\d+(\.\d+)?-flash$", r"^gemini-\d+(\.\d+)?-pro$"],
               "best": [r"^gemini-\d+(\.\d+)?-pro$", r"^gemini-\d+(\.\d+)?-flash$"]},
    "anthropic": {"fast": [r"haiku"], "smart": [r"sonnet"], "best": [r"opus", r"sonnet"]},
}
# 非聊天模型先濾掉（StockAI-DB 實測踩過：挑到語音辨識模型回 400）
NON_CHAT = re.compile(r"whisper|tts|orpheus|speech|audio|embed|guard|moderation|rerank|image|vision-only|live|aqa|playai", re.I)
# 預覽版／實驗版只在沒有正式版時才用
UNSTABLE = re.compile(r"preview|exp|experimental|beta", re.I)
MISSING = re.compile(r"model_not_found|model_decommissioned|does not exist|not found|decommissioned|no longer|deprecated|not supported", re.I)
TTL = 6 * 3600  # 模型下架是常態，不可永久快取


class RouterError(Exception):
    pass


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
    """模型名稱裡的數字當版本（gemini-2.5 > gemini-2.0；claude-opus-5-5 > claude-opus-5）。"""
    nums = [int(n) for n in re.findall(r"\d+", mid)]
    return (nums, -len(mid))


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


class Router:
    def __init__(self, provider, keys=None, http=None, sleep=time.sleep, log=None, max_calls=None, paid=None):
        if provider not in PROVIDERS:
            raise RouterError(f"不支援的供應商：{provider}（可用：{', '.join(PROVIDERS)}）")
        self.p = provider
        self.cfg = PROVIDERS[provider]
        env_keys = [k for k in os.environ.get(self.cfg["key_env"], "").split(",") if k.strip()]
        self.keys = [k.strip() for k in (keys or env_keys) if k and k.strip()]
        self.http = http or _http
        self.sleep = sleep
        self.log = log or (lambda m: print(m, file=sys.stderr))
        self.max_calls = max_calls if max_calls else max_calls_from_env(self.log)
        self.paid = paid
        self._ids, self._at, self._good = None, 0.0, {}
        self._calls = 0

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

    def pick(self, tier, avoid=()):
        """回傳這個等級要依序嘗試的模型清單。覆寫 > 官方清單排序 > 上次成功過的。"""
        override = os.environ.get(f"MODEL_ROUTER_{self.p.upper()}_{tier.upper()}")
        cands = [override] if override else []
        ids = self.models()
        cands += [m for m in rank(ids, self.p, tier, avoid) if m not in cands]
        good = self._good.get(tier)
        if good and good not in avoid and good not in cands:
            cands.append(good)
        if not cands and ids:
            self.log(f"⚠️ [{self.p}] 等級 {tier} 的偏好序一個都沒配到；目前有的模型：{', '.join(ids[:25])}")
        return cands

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
        return ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""

    def _post(self, url, key, payload, tried):
        # 上限算的是「送出去的請求」，包含重試與換金鑰；模型清單（GET）不收費所以不算
        if self._calls >= self.max_calls:
            raise RouterError(f"[{self.p}] 這次已經送出 {self._calls} 次 AI 請求（上限 {self.max_calls}）還沒成功，"
                              f"為了避免費用失控先停下。試過：{'；'.join(tried) or '（無）'}。"
                              f"請確認 {self.cfg['key_env']} 有設定、額度還夠，稍後再試；"
                              "確定要多試幾次就調高環境變數 MODEL_ROUTER_MAX_CALLS。")
        self._calls += 1
        return self.http("POST", url, self._headers(key), payload)

    def _call(self, model, messages, max_tokens, tried):
        url, payload = self._request(model, messages, max_tokens)
        paid = is_paid(self.cfg, model, self.paid)
        for ki, key in enumerate(self.keys or [""]):
            for attempt in range(4):
                status, body = self._post(url, key, payload, tried)
                if status == 200:
                    text = self._text(body)
                    if text.strip():
                        return text
                    tried.append(f"{model}（回了空白）")
                    return None
                err = json.dumps(body, ensure_ascii=False)[:300] if isinstance(body, dict) else str(body)[:300]
                if status in (404,) or (status == 400 and MISSING.search(err)):
                    tried.append(f"{model}（已下架或不存在）")
                    self._ids = None            # 自我修復：下次重新問官方
                    return "MISSING"
                if status in (401, 403):
                    tried.append(f"{model}（第 {ki + 1} 把金鑰被拒 {status}）")
                    break                       # 換下一把金鑰
                if status == 0 and paid:
                    # 逾時／連線中斷時，請求可能已經被處理（也收費了），只是回應沒送回來：不重送、也不換金鑰重送
                    tried.append(f"{model}（逾時或連線中斷；付費服務不重送，避免重複收費）")
                    return None
                if status == 429 or status >= 500 or status == 0:
                    wait = min(30, 2 ** (attempt + 1))
                    ra = body.get("_retry_after") if isinstance(body, dict) else None
                    if ra:
                        wait = min(60, max(wait, int(ra)))
                    if attempt < 3:
                        self.sleep(wait)
                        continue
                    tried.append(f"{model}（{'額度用完' if status == 429 else '服務忙碌'} {status}）")
                    return None
                tried.append(f"{model}（HTTP {status}）")
                return None
        return None

    def chat(self, tier, messages, validate=None, max_tokens=1024, escalate=True):
        """回傳模型的文字回覆。tier 可用 fast／smart／best／auto。"""
        tier = pick_tier(messages) if tier == "auto" else tier
        if tier not in TIERS:
            raise RouterError(f"不認識的等級：{tier}（用 fast／smart／best／auto）")
        self._calls = 0                         # 上限是每次 chat() 各自計算
        order = list(TIERS[TIERS.index(tier):]) if escalate else [tier]
        tried, avoid = [], set()
        for t in order:
            for model in self.pick(t, avoid):
                r = self._call(model, messages, max_tokens, tried)
                if r == "MISSING":
                    avoid.add(model)
                    continue
                if r is None:
                    continue
                if validate and not validate(r):
                    tried.append(f"{model}（回覆不合格，升級）")
                    break                       # 換更高等級
                self._good[t] = model
                return r
        raise RouterError(f"[{self.p}] 所有模型都失敗了，試過：{'；'.join(tried) or '沒有可用的模型'}。"
                          f"請確認 {self.cfg['key_env']} 有設定、額度還夠；要指定模型可設 MODEL_ROUTER_{self.p.upper()}_<等級>。")


def _http(method, url, headers, payload):
    """回 (狀態碼, JSON)。網路錯誤回 (0, {})，交給上層重試。"""
    data = json.dumps(payload).encode() if payload is not None else None
    h = dict(headers, **({"Content-Type": "application/json"} if data else {}))
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except ValueError:
            body = {}
        if isinstance(body, dict) and e.headers.get("Retry-After", "").isdigit():
            body["_retry_after"] = e.headers["Retry-After"]
        return e.code, body
    except (urllib.error.URLError, TimeoutError, ValueError):
        return 0, {}


def selftest():
    ok = True

    def t(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

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
    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv):
    ap = argparse.ArgumentParser(description="AI 模型自動挑選與下架自動換（Groq／OpenRouter／Gemini／Claude）")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("selftest", help="離線自我測試")
    lp = sub.add_parser("list", help="列出現在可用的模型與各等級會挑誰（金鑰讀環境變數）")
    lp.add_argument("provider", choices=list(PROVIDERS))
    a = ap.parse_args(argv[1:])
    if a.cmd == "selftest":
        return selftest()
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
