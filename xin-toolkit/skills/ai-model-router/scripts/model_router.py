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
4. 結果不合格（validate 回 False）→ 自動升一級（fast → smart → best）再試一次。
5. 全部失敗時丟出中文、可行動的錯誤，列出試過哪些模型（絕不印出金鑰）。

等級：fast（分類、翻譯、摘要、大量小工作）／smart（分析、推理）／best（最難、最長）／auto（依內容自動判斷）。
指定模型（覆寫）：環境變數 MODEL_ROUTER_<供應商>_<等級>，例如 MODEL_ROUTER_GROQ_FAST=llama-3.3-70b-versatile。

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
# 供應商設定：list＝模型清單網址、chat＝呼叫網址、key_env＝預設讀哪個環境變數
PROVIDERS = {
    "groq": {"list": "https://api.groq.com/openai/v1/models", "chat": "https://api.groq.com/openai/v1/chat/completions",
             "key_env": "GROQ_API_KEY", "style": "openai"},
    "openrouter": {"list": "https://openrouter.ai/api/v1/models", "chat": "https://openrouter.ai/api/v1/chat/completions",
                   "key_env": "OPENROUTER_API_KEY", "style": "openai"},
    "gemini": {"list": "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
               "chat": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
               "key_env": "GEMINI_API_KEY", "style": "gemini"},
    "anthropic": {"list": "https://api.anthropic.com/v1/models?limit=100", "chat": "https://api.anthropic.com/v1/messages",
                  "key_env": "ANTHROPIC_API_KEY", "style": "anthropic"},
}
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
    def __init__(self, provider, keys=None, http=None, sleep=time.sleep, log=None):
        if provider not in PROVIDERS:
            raise RouterError(f"不支援的供應商：{provider}（可用：{', '.join(PROVIDERS)}）")
        self.p = provider
        self.cfg = PROVIDERS[provider]
        env_keys = [k for k in os.environ.get(self.cfg["key_env"], "").split(",") if k.strip()]
        self.keys = [k.strip() for k in (keys or env_keys) if k and k.strip()]
        self.http = http or _http
        self.sleep = sleep
        self.log = log or (lambda m: print(m, file=sys.stderr))
        self._ids, self._at, self._good = None, 0.0, {}

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

    def _call(self, model, messages, max_tokens, tried):
        url, payload = self._request(model, messages, max_tokens)
        for ki, key in enumerate(self.keys or [""]):
            for attempt in range(4):
                status, body = self.http("POST", url, self._headers(key), payload)
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
