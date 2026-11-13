/* model_router.js — 網頁／Cloudflare Worker／Node 用的 AI 模型自動挑選（與 scripts/model_router.py 同一套規則）。
 *
 * 為什麼：寫死模型名稱，供應商一下架整個功能就 404（使用者的 App 實際踩過 gemini-1.5-flash、llama-3.1-8b-instant）。
 * 做法：問官方現在有哪些模型 → 依等級偏好序挑最新 → 回應明確說「模型不存在」才換下一個（404 但沒說＝網址設定錯，停下說明）→
 *       429 分三種：每分鐘限流照對方給的時間等、每日／每月額度用完直接換下一個模型、餘額／帳單問題停止這家供應商 →
 *       冷卻記在模型上、同一個頁面／Worker 裡共用（每次 new 也記得）→ 5xx 指數退避 → 結果不合格自動升級（fast → smart → best）。
 *       金鑰只放 HTTP 標頭，不放網址、不印出來。
 *       付費供應商（PROVIDERS 的 paid）逾時／連線中斷不重送同一請求（對方可能已處理、已收費），直接換下一個候選；
 *       一次 chat() 最多送 maxCalls 次請求（預設 12），超過就丟中文錯誤，避免費用失控。
 *       規則與 Python 版一致：兩邊 selftest 跑同一份 router_cases.json，註解較完整的說明在 Python 版。
 *
 * 用法（瀏覽器：<script src="model_router.js"></script>；Worker／Node：import 或 require）：
 *   const r = new ModelRouter("gemini", { keys: [apiKey] });
 *   const text = await r.chat("auto", [{ role: "user", content: "把這段翻成中文：..." }]);
 *   node model_router.js selftest            // 加 --strict：找不到 router_cases.json 就算失敗（skl 的 CI 用）
 */
(function (root) {
  "use strict";
  const TIERS = ["fast", "smart", "best"];
  // paid：每次呼叫都收費（逾時不重送）。"free_suffix"＝看模型名稱，OpenRouter 以 :free 結尾的免費、其他收費。
  // Groq／Gemini 預設是免費層所以標 false；開了付費帳單的 App 建立時傳 { paid: true }。
  const PROVIDERS = {
    groq: { list: "https://api.groq.com/openai/v1/models", chat: "https://api.groq.com/openai/v1/chat/completions", style: "openai", paid: false },
    openrouter: { list: "https://openrouter.ai/api/v1/models", chat: "https://openrouter.ai/api/v1/chat/completions", style: "openai", paid: "free_suffix" },
    gemini: { list: "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
              chat: "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", style: "gemini", paid: false },
    anthropic: { list: "https://api.anthropic.com/v1/models?limit=100", chat: "https://api.anthropic.com/v1/messages", style: "anthropic", paid: true },
  };
  const DEFAULT_MAX_CALLS = 12;
  // 等待與冷卻（秒）：一次最多等 60 秒（再久就先換模型）；額度用完沒說何時恢復先避開 1 小時；
  // 額度用完卻說幾秒後再試不可信（Gemini 每日額度也附幾秒的 retryDelay，論壇實例 1s）至少 5 分鐘；怪異的 Retry-After 最多一天
  const MAX_WAIT = 60, QUOTA_COOLDOWN = 3600, QUOTA_MIN_COOLDOWN = 300, MAX_COOLDOWN = 86400;
  // 沒標 paid 的新供應商當付費：寧可少重試，不要重複收費
  function isPaid(cfg, model, override) {
    if (override != null) return !!override;
    const p = cfg.paid == null ? true : cfg.paid;
    if (p === "free_suffix") return !String(model).endsWith(":free");
    return !!p;
  }
  // Groq：2026-08-16 停用 llama-3.1-8b-instant、llama-3.3-70b-versatile 後只剩 gpt-oss 配得到。qwen 另有一份免費額度、
  // 中文也比較好，三個等級都放：smart 先用 qwen、best 先用 gpt-oss-120b，回覆不合格升級時才是真的換模型。不寫版號。
  const PREFS = {
    groq: { fast: [/llama-\d+(\.\d+)?-8b/i, /instant/i, /gpt-oss-20b/i, /qwen/i, /llama/i],
            smart: [/qwen/i, /llama-\d+(\.\d+)?-70b/i, /gpt-oss-120b/i, /70b/i, /llama/i],
            best: [/gpt-oss-120b/i, /qwen/i, /llama-4/i, /70b/i, /llama/i] },
    openrouter: { fast: [/llama-\d+(\.\d+)?-8b.*:free$/i, /:free$/i],
                  smart: [/70b.*:free$/i, /(qwen|deepseek).*:free$/i, /:free$/i],
                  best: [/(deepseek|qwen).*:free$/i, /70b.*:free$/i, /:free$/i] },
    gemini: { fast: [/^gemini-\d+(\.\d+)?-flash-lite$/i, /^gemini-\d+(\.\d+)?-flash$/i],
              smart: [/^gemini-\d+(\.\d+)?-flash$/i, /^gemini-\d+(\.\d+)?-pro$/i],
              best: [/^gemini-\d+(\.\d+)?-pro$/i, /^gemini-\d+(\.\d+)?-flash$/i] },
    anthropic: { fast: [/haiku/i], smart: [/sonnet/i], best: [/opus/i, /sonnet/i] },
  };
  const NON_CHAT = /whisper|tts|orpheus|speech|audio|embed|guard|moderation|rerank|image|vision-only|live|aqa|playai/i;
  const UNSTABLE = /preview|exp|experimental|beta/i;
  const TTL = 6 * 3600 * 1000;   // 模型下架是常態，不可永久快取
  // 以下規則跟 Python 版一字不差（為什麼這樣寫見 Python 版註解）。
  // 回應「明確說」模型不存在／下架才算：只看 404 會把網址打錯也當成模型下架
  const MODEL_MISSING = /model_not_found|model_decommissioned|decommissioned|\bmodels?\b[\s\S]{0,160}?(?:does not exist|not found|no longer (?:available|supported)|has been (?:deprecated|retired|removed))|not_found_error[\s\S]{0,80}?\bmodel\b|not a valid model|no endpoints found for/i;
  // 餘額／帳單。⛔ 不能只比對 billing、credits：Groq、Gemini 的限流訊息、OpenRouter 的每日上限訊息都有這些字
  const BILLING = /insufficient[_\s-]?(?:balance|credits?|funds|quota)|credit balance is too low|(?:out of|no remaining|not enough) credits?|requires more credits|can only afford|credits? (?:are |have been )?(?:depleted|exhausted)|prepayment|payment required|(?:billing|spend(?:ing)?) (?:hard )?limit|exceeded (?:your |the )?(?:monthly )?(?:budget|spend(?:ing)?)\b|reached your specified (?:api )?usage limits?|billing[_\s]not[_\s]active|billing account[\s\S]{0,60}?(?:disabled|closed|suspended)|account[_\s](?:is[_\s])?(?:deactivated|suspended|inactive|not[_\s]active)|arrears/i;
  // 每日／每週／每月額度（Groq TPD／RPD、OpenRouter free-models-per-day、Gemini quotaId 的 PerDay）；「today」不算
  const LONG_WINDOW = /per[\s_-]?(?:day|week|month)|\b(?:rpd|tpd)\b|\b(?:daily|weekly|monthly)\b/i;
  const TEXT_DELAY = /(?:try again|retry)\s+(?:in|after)\s+((?:\d+(?:\.\d+)?(?:ms|h|m|s))+|\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)?/i;
  const NUMBER = /^\d+(?:\.\d+)?$/;
  const DURATION = /^(?:\d+(?:\.\d+)?(?:ms|h|m|s))+$/i;
  const DUR_PART = /(\d+(?:\.\d+)?)(ms|h|m|s)/gi;
  const UNIT = { ms: 0.001, h: 3600, m: 60, s: 1 };
  // 先比對 HTTP 日期格式再交給 Date.parse：Date.parse 連「test 1」都當成 2001 年
  const HTTP_DATE = /^(?:[A-Za-z]{3},\s*)?\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}(?::\d{2})?\s*(?:GMT|UTC|[+-]\d{4})$/;
  // 參數量（8b、70b、a22b）不算版本：不然 qwen3-32b 的 32 會大過 qwen3.6-27b 的 6
  const SIZE = /(\d+(?:\.\d+)?)b(?![a-z0-9])/gi;
  const THINK = /^\s*<think>[\s\S]*?<\/think>\s*/i, THINK_OPEN = /^\s*<think>/i;
  const HEADER_KEYS = [["retry-after", "_retry_after"], ["x-ratelimit-reset", "_ratelimit_reset"]];
  const COOL_NOTE = { rate: "限流", quota: "額度用完" };
  // 冷卻紀錄放在模組層級：Worker 常在每個請求裡 new ModelRouter（金鑰在 env），記在物件上等於沒記
  const COOLDOWNS = new Map();
  const MISSING = Symbol("missing"), ESCALATE = Symbol("escalate");   // 不用字串當記號，模型回什麼字都不會搞混

  function resetCooldowns() { COOLDOWNS.clear(); }
  // 金鑰組的指紋（FNV-1a）：不同帳號的冷卻分開記；只拿來分組，不存、不印金鑰本身
  function fingerprint(keys) {
    let h = 0x811c9dc5;
    for (const b of new TextEncoder().encode(keys.join("\n"))) h = Math.imul(h ^ b, 0x01000193) >>> 0;
    return h.toString(16).padStart(8, "0");
  }
  function versionKey(id) {
    const sizes = Array.from(id.matchAll(SIZE), (m) => Number(m[1]));
    const nums = (id.replace(SIZE, " ").match(/\d+/g) || []).map(Number);
    return [nums, sizes, id.length];
  }
  function cmpDesc(a, b) {
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      const x = a[i] == null ? -1 : a[i], y = b[i] == null ? -1 : b[i];
      if (x !== y) return y - x;
    }
    return 0;
  }
  // 先比版本號（大的先），同版本比參數量（大的先），最後名稱短的先
  function versionCmp(a, b) {
    const ka = versionKey(a), kb = versionKey(b);
    return cmpDesc(ka[0], kb[0]) || cmpDesc(ka[1], kb[1]) || ka[2] - kb[2];
  }
  function rank(ids, provider, tier, avoid) {
    const av = new Set(avoid || []);
    const pool = ids.filter((m) => !av.has(m) && !NON_CHAT.test(m));
    const out = [];
    for (const stableOnly of [true, false])
      for (const re of PREFS[provider][tier])
        pool.filter((m) => re.test(m) && (!stableOnly || !UNSTABLE.test(m))).sort(versionCmp)
          .forEach((m) => { if (!out.includes(m)) out.push(m); });
    return out.slice(0, 5);
  }
  function pickTier(messages, hint) {
    if (TIERS.includes(hint)) return hint;
    const text = messages.map((m) => String(m.content || "")).join(" "), low = text.toLowerCase();
    if (/程式|code|debug|架構|重構|規格|長篇|完整報告|refactor|architecture/.test(low) || text.length > 60000) return "best";
    if (/分析|推理|為什麼|比較|策略|評估|判斷|預測|analy|reason|why|compare|evaluate|strategy/.test(low) || text.length > 8000) return "smart";
    return "fast";
  }

  // ── 錯誤回應分類（跟 Python 版共用 router_cases.json 的案例）──
  // 「要等多久」換成秒數；看不懂回 null。"5m"＝300 秒（不是 5 秒）、"520ms"、"1m30s"、"1h2m3s"、HTTP 日期
  function parseDelay(v, nowSec) {
    if (v == null || typeof v === "boolean") return null;
    if (typeof v === "number") return v >= 0 && isFinite(v) ? v : null;
    const s = String(v).trim();
    if (NUMBER.test(s)) return parseFloat(s);
    if (DURATION.test(s)) {
      let total = 0;
      for (const m of s.matchAll(DUR_PART)) total += parseFloat(m[1]) * UNIT[m[2].toLowerCase()];
      return total;
    }
    if (HTTP_DATE.test(s)) {
      const ms = Date.parse(s);
      if (isNaN(ms)) return null;
      return Math.max(0, ms / 1000 - (nowSec == null ? Date.now() / 1000 : nowSec));
    }
    return null;
  }
  // 訊息裡的等待時間（try again in 7m26.4s／Please retry in 17.3s／retry after 30 seconds）
  function textDelay(text) {
    // 用 match 不用 RegExp.exec：安全掃描會把 .exec( 當成執行指令，害技能裝不進其他 App
    const m = String(text || "").match(TEXT_DELAY);
    if (!m) return null;
    const num = m[1], unit = (m[2] || "").toLowerCase();
    if (!NUMBER.test(num)) return parseDelay(num);
    const mult = unit.startsWith("min") ? 60 : (unit.startsWith("hour") || unit.startsWith("hr")) ? 3600 : 1;
    return parseFloat(num) * mult;
  }
  // x-ratelimit-reset 是「恢復的時間點」（OpenRouter 用毫秒時間戳）；不像時間戳的不猜
  function resetDelay(v, nowSec) {
    let x = Number(v);
    if (x > 1e12) x /= 1000;
    return x > 1e9 ? Math.max(0, x - nowSec) : null;
  }
  // 錯誤回應裡所有字串值串成一行（用 | 隔開，避免把兩個欄位接成一句話誤判）
  function flat(body, limit) {
    const out = [];
    const walk = (v, depth) => {
      if (depth > 20) return;
      if (typeof v === "string") out.push(v);
      else if (Array.isArray(v)) v.forEach((x) => walk(x, depth + 1));
      else if (v && typeof v === "object") Object.values(v).forEach((x) => walk(x, depth + 1));
    };
    walk(body, 0);
    return out.join(" | ").slice(0, limit || 4000);
  }
  // Gemini 的 error.details：QuotaFailure 的 quotaId（PerMinute／PerDay）、quotaValue 是不是 0，以及 RetryInfo 的 retryDelay
  function geminiDetails(b) {
    const err = b && b.error && typeof b.error === "object" ? b.error : null;
    const ids = [];
    let retry = null, zero = false;
    for (const d of err && Array.isArray(err.details) ? err.details : []) {
      if (!d || typeof d !== "object") continue;
      const kind = String(d["@type"] || "");
      if (kind.endsWith("QuotaFailure")) {
        for (const v of Array.isArray(d.violations) ? d.violations : []) {
          if (v && typeof v === "object" && v.quotaId) { ids.push(String(v.quotaId)); zero = zero || String(v.quotaValue) === "0"; }
        }
      } else if (kind.endsWith("RetryInfo") && retry == null) retry = d.retryDelay;
    }
    return [ids, retry, zero];
  }
  // OpenRouter 把限流標頭放在 error.metadata.headers 裡（例如 X-RateLimit-Reset）
  function metaReset(b) {
    const h = b && b.error && b.error.metadata && b.error.metadata.headers;
    if (h && typeof h === "object" && !Array.isArray(h)) for (const k of Object.keys(h)) if (k.toLowerCase() === "x-ratelimit-reset") return h[k];
    return null;
  }
  const first = (...vals) => { for (const v of vals) if (v != null) return v; return null; };
  // 把判斷限流要用的回應標頭放進 body（_retry_after、_ratelimit_reset）；名稱不分大小寫，body 已有的不覆蓋
  function withHeaders(body, headers) {
    if (!body || typeof body !== "object" || Array.isArray(body) || !headers) return body;
    const get = typeof headers.get === "function" ? (n) => headers.get(n)
      : (n) => { const k = Object.keys(headers).find((x) => x.toLowerCase() === n); return k == null ? null : headers[k]; };
    for (const [name, field] of HEADER_KEYS) {
      const v = get(name);
      if (v != null && v !== "" && !(field in body)) body[field] = String(v);
    }
    return body;
  }
  // 回 { kind, delay }：billing／missing／config／auth／quota／rate／server／other（說明見 Python 版 classify）
  function classify(status, body, nowSec) {
    const now = nowSec == null ? Date.now() / 1000 : nowSec;
    const b = body && typeof body === "object" && !Array.isArray(body) ? body : {};
    const text = flat(body);
    if (status === 402 || ([400, 401, 403, 429].includes(status) && BILLING.test(text))) return { kind: "billing", delay: null };
    if ((status === 400 || status === 404) && MODEL_MISSING.test(text)) return { kind: "missing", delay: null };
    if (status === 404) return { kind: "config", delay: null };
    if (status === 401 || status === 403) return { kind: "auth", delay: null };
    if (status !== 429 && status !== 0 && status < 500) return { kind: "other", delay: null };
    const [ids, retry, zero] = geminiDetails(b);
    // quotaValue 是 0（limit: 0）：這個方案根本沒有這個模型的額度，等多久都不會好；retryDelay 沒意義，當成沒說
    if (status === 429 && zero) return { kind: "quota", delay: null };
    const delay = first(parseDelay(b._retry_after, now), parseDelay(retry, now), textDelay(text),
      resetDelay(b._ratelimit_reset, now), resetDelay(metaReset(b), now));
    if (status === 429) {
      // Gemini 有結構化的 quotaId 就只看它：每分鐘限流的訊息也寫「exceeded your current quota」
      const longWindow = ids.length ? ids.some((i) => LONG_WINDOW.test(i)) : LONG_WINDOW.test(text);
      return { kind: longWindow ? "quota" : "rate", delay };
    }
    return { kind: "server", delay };
  }
  // 推理模型（Groq 的 qwen 等）回覆開頭的 <think>…</think> 拿掉；只有開頭沒有結尾＝想到一半被截斷，沒有答案
  function stripThink(text) {
    const out = String(text || "").replace(THINK, "");
    return THINK_OPEN.test(out) ? "" : out;
  }
  function human(sec) {
    const s = Math.max(0, Number(sec));
    if (s < 90) return Math.floor(s + 0.5) + " 秒";
    if (s < 5400) return Math.floor(s / 60 + 0.5) + " 分鐘";
    return Math.floor(s / 3600 + 0.5) + " 小時";
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  class ModelRouter {
    constructor(provider, opts) {
      if (!PROVIDERS[provider]) throw new Error("不支援的供應商：" + provider + "（可用：" + Object.keys(PROVIDERS).join("、") + "）");
      opts = opts || {};
      this.p = provider; this.cfg = PROVIDERS[provider];
      this.keys = (opts.keys || []).filter(Boolean);
      this.fetch = opts.fetch || ((...a) => fetch(...a));
      this.sleep = opts.sleep || sleep;
      this.now = opts.now || (() => Date.now());   // 毫秒；測試用假時鐘
      this.override = opts.override || {};      // 例如 { fast: "gemini-2.5-flash" }，等同 Python 的環境變數覆寫  // guardrails: ignore（範例／測試資料）
      this.log = opts.log || ((m) => console.warn(m));
      // 跟 Python 版一樣等 60 秒：對方卡住不回時，不要一直等到 Worker／瀏覽器把整個請求砍掉
      this.timeoutMs = opts.timeoutMs || 60000;
      // 看不懂或小於 1 就用預設並提醒（跟 Python 版讀 MODEL_ROUTER_MAX_CALLS 的規則一樣）
      const mc = opts.maxCalls == null ? DEFAULT_MAX_CALLS : Math.floor(Number(opts.maxCalls));
      this.maxCalls = mc >= 1 ? mc : DEFAULT_MAX_CALLS;
      if (!(mc >= 1)) this.log("⚠️ maxCalls=" + opts.maxCalls + " 看不懂（要填 1 以上的整數），改用預設 " + DEFAULT_MAX_CALLS);
      this.paid = opts.paid == null ? null : !!opts.paid;
      this._ids = null; this._at = 0; this._good = {}; this._calls = 0;
      this._fp = fingerprint(this.keys);
    }
    _headers(key) {
      const st = this.cfg.style;
      if (st === "gemini") return { "x-goog-api-key": key };
      // 瀏覽器直接呼叫 Claude API 需要這個標頭；金鑰是使用者自己的，只存在他的裝置
      if (st === "anthropic") return { "x-api-key": key, "anthropic-version": "2023-06-01", "anthropic-dangerous-direct-browser-access": "true" };
      return { Authorization: "Bearer " + key };
    }
    async _http(method, url, headers, payload) {
      // 逾時當成狀態 0（跟連線失敗一樣）：免費供應商會退避重試，付費供應商由 _call 決定不重送；計時要包含讀完回應內容
      const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timer = ctl ? setTimeout(() => ctl.abort(), this.timeoutMs) : null;
      try {
        const r = await this.fetch(url, { method, headers: Object.assign({}, headers, payload ? { "Content-Type": "application/json" } : {}),
                                          body: payload ? JSON.stringify(payload) : undefined, signal: ctl ? ctl.signal : undefined });
        // 先讀文字再解析：HTML 錯誤頁（例如中間的 Proxy 回 404）放進 _raw，分類時看得到
        let body = {};
        if (typeof r.text === "function") {
          const raw = await r.text();
          try { body = raw ? JSON.parse(raw) : {}; } catch (e) { body = { _raw: String(raw).slice(0, 500) }; }
        } else { try { body = await r.json(); } catch (e) { body = {}; } }
        if (!body || typeof body !== "object" || Array.isArray(body)) body = { _raw: JSON.stringify(body).slice(0, 500) };
        return [r.status, withHeaders(body, r.headers)];
      } catch (e) { return [0, {}]; }
      finally { if (timer) clearTimeout(timer); }
    }
    async models(refresh) {
      if (!refresh && this._ids && Date.now() - this._at < TTL) return this._ids;
      let last = "";
      for (let i = 0; i < Math.max(1, this.keys.length); i++) {
        const [st, body] = await this._http("GET", this.cfg.list, this._headers(this.keys[i] || ""));
        if (st === 200) {
          const data = body.data || body.models || [];
          this._ids = data.filter((m) => !Array.isArray(m.supportedGenerationMethods) || m.supportedGenerationMethods.includes("generateContent"))
            .map((m) => String(m.id || m.name || "").replace(/^models\//, "")).filter(Boolean);
          this._at = Date.now(); return this._ids;
        }
        last = "第 " + (i + 1) + " 把金鑰 HTTP " + st;   // 只說第幾把，絕不印金鑰
      }
      this.log("⚠️ [" + this.p + "] 取不到模型清單（" + last + "），改用上次成功過的模型");
      this._ids = []; this._at = Date.now(); return [];
    }
    // ── 冷卻（記在模型上，不是整家供應商）──
    _ck(model) { return this.p + "\u0000" + this._fp + "\u0000" + model; }
    _cooling(model) { const c = COOLDOWNS.get(this._ck(model)); return c && c.until > this.now() ? c : null; }
    _cool(model, seconds, why) {
      seconds = Math.min(MAX_COOLDOWN, Math.max(0, Number(seconds)));
      const until = this.now() + seconds * 1000, k = this._ck(model), old = COOLDOWNS.get(k);
      if (!old || old.until < until) COOLDOWNS.set(k, { until, why });   // 已經有更久的冷卻就不縮短
      return seconds;
    }
    async _candidates(tier, avoid) {
      const av = new Set(avoid || []);   // 陣列或 Set 都收（跟 Python 版一樣）
      const c = this.override[tier] ? [this.override[tier]] : [];
      const ids = await this.models();
      rank(ids, this.p, tier, av).forEach((m) => { if (!c.includes(m)) c.push(m); });
      const g = this._good[tier];
      if (g && !av.has(g) && !c.includes(g)) c.push(g);
      if (!c.length && ids.length) this.log("⚠️ [" + this.p + "] 等級 " + tier + " 沒配到模型；目前有：" + ids.slice(0, 25).join(", "));
      const ready = [], cooling = [];
      for (const m of c) { const x = this._cooling(m); if (x) cooling.push([m, x]); else ready.push(m); }
      return { ready, cooling };
    }
    // 這個等級現在要依序嘗試的模型（冷卻中的先跳過）
    async pick(tier, avoid) { return (await this._candidates(tier, avoid)).ready; }
    _request(model, messages, maxTokens) {
      const sys = messages.filter((m) => m.role === "system").map((m) => m.content).join("\n");
      const chat = messages.filter((m) => m.role !== "system"), st = this.cfg.style;
      if (st === "gemini") {
        const body = { contents: chat.map((m) => ({ role: m.role === "assistant" ? "model" : "user", parts: [{ text: m.content }] })) };
        if (sys) body.systemInstruction = { parts: [{ text: sys }] };
        return [this.cfg.chat.replace("{model}", encodeURIComponent(model)), body];
      }
      if (st === "anthropic") {
        const body = { model, max_tokens: maxTokens, messages: chat };
        if (sys) body.system = sys;
        return [this.cfg.chat, body];
      }
      return [this.cfg.chat, { model, messages, max_tokens: maxTokens }];
    }
    _text(b) {
      const st = this.cfg.style;
      if (st === "gemini") return (((b.candidates || [{}])[0].content || {}).parts || []).map((p) => p.text || "").join("");
      if (st === "anthropic") return (b.content || []).filter((x) => x.type === "text").map((x) => x.text).join("");
      return stripThink((((b.choices || [{}])[0].message) || {}).content || "");
    }
    async _post(url, key, payload, tried) {
      // 上限算的是「送出去的請求」，包含重試與換金鑰；模型清單（GET）不收費所以不算
      if (this._calls >= this.maxCalls)
        throw new Error("[" + this.p + "] 這次已經送出 " + this._calls + " 次 AI 請求（上限 " + this.maxCalls + "）還沒成功，為了避免費用失控先停下。試過：" +
          (tried.join("；") || "（無）") + "。請確認金鑰有設定、額度還夠，稍後再試；確定要多試幾次就調高 maxCalls 選項。");
      this._calls++;
      return this._http("POST", url, this._headers(key), payload);
    }
    // 供應商回應的摘要：先把自己的金鑰遮掉再截斷，截斷不會留下半把金鑰
    _excerpt(body) {
      let s = flat(body);
      for (const k of this.keys) if (k) s = s.split(k).join("***");
      return s.slice(0, 200) || "（沒有內容）";
    }
    async _call(model, messages, maxTokens, tried) {
      const [url, payload] = this._request(model, messages, maxTokens);
      const paid = isPaid(this.cfg, model, this.paid);
      let noMoney = false;
      for (let ki = 0; ki < Math.max(1, this.keys.length); ki++) {
        for (let attempt = 0; attempt < 4; attempt++) {
          const [st, body] = await this._post(url, this.keys[ki] || "", payload, tried);
          if (st === 200) {
            const t = this._text(body);
            if (t.trim()) { COOLDOWNS.delete(this._ck(model)); return t; }
            tried.push(model + "（回了空白）"); return null;
          }
          const { kind, delay } = classify(st, body, this.now() / 1000);
          if (kind === "missing") { tried.push(model + "（已下架或不存在）"); this._ids = null; return MISSING; }
          if (kind === "config") {
            // 每個模型共用同一個網址：再換模型只會一直 404，也不能把模型標成下架
            tried.push(model + "（HTTP 404，回應沒說是模型不存在）");
            throw new Error("[" + this.p + "] 呼叫 " + model + " 回 HTTP 404，但回應沒說是模型不存在：多半是網址設定錯（API 路徑、版本，或中間的 Proxy／Worker），" +
              "不是模型下架，所以沒有把模型標成下架。請檢查 PROVIDERS 裡 " + this.p + " 的 chat 網址。供應商回應：" + this._excerpt(body));
          }
          // 帳單是整個帳號的事：下一把金鑰可能是別的帳號
          if (kind === "billing") { tried.push(model + "（第 " + (ki + 1) + " 把金鑰：帳戶餘額或額度不足 " + st + "）"); noMoney = true; break; }
          if (kind === "auth") { tried.push(model + "（第 " + (ki + 1) + " 把金鑰被拒 " + st + "）"); break; }
          if (kind === "quota") {
            // 每日／每月額度：等幾秒不會恢復，重試只會浪費次數上限
            const cd = this._cool(model, delay == null ? QUOTA_COOLDOWN : Math.max(delay, QUOTA_MIN_COOLDOWN), "quota");
            tried.push(model + "（每日／每月額度用完，約 " + human(cd) + " 後恢復；不重試，換下一個模型）");
            this.log("⚠️ [" + this.p + "] " + model + " 每日／每月額度用完，約 " + human(cd) + " 後恢復；這段時間改用同一家的其他模型");
            return null;
          }
          // 逾時／連線中斷時請求可能已被處理（也收費了），只是回應沒送回來：付費服務不重送、也不換金鑰重送
          if (st === 0 && paid) { tried.push(model + "（逾時或連線中斷；付費服務不重送，避免重複收費）"); return null; }
          let wait = Math.min(30, 2 ** (attempt + 1));
          if (delay != null) wait = Math.max(wait, delay);
          if (kind === "rate") {
            if (wait > MAX_WAIT) { this._cool(model, wait, "rate"); tried.push(model + "（限流，對方要求等 " + human(wait) + "；先換下一個模型）"); return null; }
            if (attempt < 3) { await this.sleep(wait * 1000); continue; }
            this._cool(model, wait, "rate"); tried.push(model + "（限流 429，等了 3 次還是滿；先換下一個模型）"); return null;
          }
          if (kind === "server") {
            if (attempt < 3) { await this.sleep(Math.min(MAX_WAIT, wait) * 1000); continue; }
            tried.push(model + "（服務忙碌 " + st + "）"); return null;
          }
          tried.push(model + "（HTTP " + st + "）"); return null;
        }
      }
      if (noMoney)
        throw new Error("[" + this.p + "] 帳戶餘額或額度不足：這不是限流，等多久都不會恢復，所以已停止嘗試 " + this.p + " 的其他模型。" +
          "請到供應商後台檢查帳單——儲值餘額、付款方式、每月花費上限。試過：" + tried.join("；") + "。");
      return null;
    }
    // 處理一次 _call 的結果：成功回文字；ESCALATE＝不合格要升級；null＝換下一個
    _settle(t, model, r, validate, tried, avoid, done) {
      if (r === MISSING) { avoid.add(model); return null; }
      if (r == null) { if (!this._cooling(model)) done.add(model); return null; }   // 不是因為冷卻失敗的，這次不再試
      if (validate && !validate(r)) { tried.push(model + "（回覆不合格，升級）"); done.add(model); return ESCALATE; }
      this._good[t] = model; return r;
    }
    async chat(tier, messages, opts) {
      opts = opts || {};
      tier = tier === "auto" ? pickTier(messages) : tier;
      if (!TIERS.includes(tier)) throw new Error("不認識的等級：" + tier + "（用 fast／smart／best／auto）");
      this._calls = 0;   // 上限是每次 chat() 各自計算
      const order = opts.escalate === false ? [tier] : TIERS.slice(TIERS.indexOf(tier));
      const maxTokens = opts.maxTokens || 1024, validate = opts.validate;
      const tried = [], avoid = new Set(), done = new Set(), seen = [];
      for (const t of order) {
        // 同一個模型可能在好幾個等級（例如 qwen）：這次已經失敗過的不再浪費次數
        const { ready, cooling } = await this._candidates(t, new Set([...avoid, ...done]));
        for (const [m, c] of cooling) {
          if (!seen.some(([, s]) => s === m))
            tried.push(m + "（" + (COOL_NOTE[c.why] || "冷卻") + "，冷卻中，約 " + human((c.until - this.now()) / 1000) + " 後恢復，這次跳過）");
          seen.push([t, m]);
        }
        for (const model of ready) {
          seen.push([t, model]);
          const r = this._settle(t, model, await this._call(model, messages, maxTokens, tried), validate, tried, avoid, done);
          if (r === ESCALATE) break;   // 換更高等級
          if (r != null) return r;
        }
      }
      const r2 = await this._secondPass(seen, avoid, done, messages, maxTokens, tried, validate);
      if (r2 != null) return r2;
      const now = this.now();
      const lefts = seen.filter(([, m]) => !avoid.has(m)).map(([, m]) => this._cooling(m)).filter(Boolean).map((c) => (c.until - now) / 1000);
      const soon = lefts.length ? "冷卻中的模型最快約 " + human(Math.min(...lefts)) + " 後恢復，到時再試；" : "";
      throw new Error("[" + this.p + "] 所有模型都失敗了，試過：" + (tried.join("；") || "沒有可用的模型") + "。" + soon +
        "請確認金鑰有設定、額度還夠；要指定模型可傳 { override: { 等級: 模型名稱 } }。");
    }
    // 第一輪全失敗、而且有模型只是在冷卻（多半是每分鐘限流）：挑 60 秒內會恢復的，從最快的開始等一下再試。
    // 不這樣做的話，批次工作遇到每個模型剛好都在限流，接下來每一筆都會直接失敗
    async _secondPass(seen, avoid, done, messages, maxTokens, tried, validate) {
      const now = this.now(), queue = [], picked = new Set();
      for (const [t, m] of seen) {
        if (picked.has(m) || avoid.has(m) || done.has(m)) continue;
        picked.add(m);
        const c = this._cooling(m), left = c ? (c.until - now) / 1000 : 0;
        if (left <= MAX_WAIT) queue.push([left, t, m]);
      }
      queue.sort((a, b) => a[0] - b[0]);   // 穩定排序：一樣快恢復的照原本偏好順序
      for (const [, t, model] of queue) {
        const c = this._cooling(model);
        if (c && c.until > this.now()) await this.sleep(c.until - this.now());
        const r = this._settle(t, model, await this._call(model, messages, maxTokens, tried), validate, tried, avoid, done);
        if (r != null && r !== ESCALATE) return r;
      }
      return null;
    }
  }

  async function selftest(opts) {
    opts = opts || {};
    let ok = true;
    const t = (c, n) => { console.log((c ? "PASS " : "FAIL ") + n); ok = ok && !!c; };
    resetCooldowns();
    const ids = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "whisper-large-v3", "llama-3.5-8b-instant"];
    t(rank(ids, "groq", "fast")[0] === "llama-3.5-8b-instant", "fast 挑最新的 8b");
    t(rank(["gemini-2.5-flash", "gemini-3.0-flash-preview", "gemini-2.5-flash-image"], "gemini", "smart")[0] === "gemini-2.5-flash", "正式版優先、圖片模型濾掉");  // guardrails: ignore（範例／測試資料）
    t(pickTier([{ content: "分析這檔股票" }]) === "smart" && pickTier([{ content: "翻成中文" }]) === "fast", "auto 判斷難度");
    const mk = (models, chatFn) => async (url, init) => {
      const json = async () => (init.method === "GET" ? { data: models.map((id) => ({ id })) } : chatFn(JSON.parse(init.body).model, init.headers)[1]);
      const status = init.method === "GET" ? 200 : chatFn(JSON.parse(init.body).model, init.headers)[0];
      return { status, json, headers: { get: () => null } };
    };
    let r = new ModelRouter("groq", { keys: ["k"], sleep: async () => {}, log: () => {},
      fetch: mk(ids, (m) => (m === "llama-3.5-8b-instant" ? [404, { error: "model_decommissioned" }] : [200, { choices: [{ message: { content: "ok:" + m } }] }])) });
    t((await r.chat("fast", [{ role: "user", content: "hi" }])) === "ok:llama-3.1-8b-instant", "下架 → 自動換下一個");
    r = new ModelRouter("groq", { keys: ["k"], sleep: async () => {}, log: () => {},
      fetch: mk(ids, (m) => [200, { choices: [{ message: { content: m.includes("8b") ? "壞" : "好的分析" } }] }]) });
    t((await r.chat("fast", [{ role: "user", content: "x" }], { validate: (s) => s.length > 2 })) === "好的分析", "不合格 → 自動升級");
    r = new ModelRouter("groq", { keys: ["secret-123"], sleep: async () => {}, log: () => {}, fetch: mk(ids, () => [500, {}]) });
    try { await r.chat("best", [{ role: "user", content: "x" }]); t(false, "全部失敗要丟錯"); }
    catch (e) { t(!String(e.message).includes("secret-123") && e.message.includes("試過"), "全部失敗：中文錯誤、不洩漏金鑰"); }
    // 對方一直不回：逾時後重試，最後丟出中文錯誤，而不是永遠卡住
    // 假伺服器只在被中止時才結束；沒有逾時機制的話會永遠卡住，所以外面再包 3 秒的保險
    const hang = async (url, init) => (init.method === "GET" ? { status: 200, json: async () => ({ data: [{ id: "llama-3.1-8b-instant" }] }), headers: { get: () => null } }
      : new Promise((_, rej) => { if (init.signal) init.signal.addEventListener("abort", () => rej(new Error("aborted"))); }));
    let calls = 0;
    r = new ModelRouter("groq", { keys: ["k"], sleep: async () => { calls++; }, log: () => {}, fetch: hang, timeoutMs: 20 });
    const out = await Promise.race([r.chat("fast", [{ role: "user", content: "x" }], { escalate: false }).catch((e) => e),
      new Promise((res) => setTimeout(() => res("卡住"), 3000))]);
    t(out instanceof Error && calls === 3 && out.message.includes("服務忙碌"), "對方不回應：逾時後退避重試 3 次，再丟出中文錯誤（不會卡住）");

    // ── 付費服務逾時不重送、總次數上限（跟 Python 版同一組案例） ──
    const antSeq = (script) => {
      const posts = [];
      const f = async (url, init) => {
        if (init.method === "GET") return { status: 200, json: async () => ({ data: [{ id: "claude-haiku-5-5" }, { id: "claude-haiku-4-5-20251001" }] }), headers: { get: () => null } };  // guardrails: ignore（範例／測試資料）
        const m = JSON.parse(init.body).model; posts.push(m);
        const [st, b] = script(m, posts.length);
        if (st === 0) throw new TypeError("network");
        return { status: st, json: async () => b, headers: { get: () => null } };
      };
      return [f, posts];
    };
    let waits = [];
    let [fa, posts] = antSeq((m, n) => (n === 1 ? [0, {}] : [200, { content: [{ type: "text", text: "ok:" + m }] }]));
    r = new ModelRouter("anthropic", { keys: ["k1", "k2"], sleep: async (ms) => { waits.push(ms); }, log: () => {}, fetch: fa });
    const o1 = await r.chat("fast", [{ role: "user", content: "x" }], { escalate: false });
    t(JSON.stringify(posts) === JSON.stringify(["claude-haiku-5-5", "claude-haiku-4-5-20251001"]) && o1 === "ok:claude-haiku-4-5-20251001" && !waits.length,  // guardrails: ignore（範例／測試資料）
      "付費（Claude）逾時：同一請求不重送、不換金鑰重送，直接換下一個候選");
    [fa, posts] = antSeq(() => [0, {}]);
    r = new ModelRouter("anthropic", { keys: ["k"], sleep: async () => {}, log: () => {}, fetch: fa });
    try { await r.chat("fast", [{ role: "user", content: "x" }], { escalate: false }); t(false, "付費全部逾時要丟錯"); }
    catch (e) { t(posts.length === 2 && e.message.includes("不重送"), "付費全部逾時：每個候選只送一次，錯誤說明為什麼沒重試"); }
    waits = [];
    [fa, posts] = antSeq((m, n) => (n === 1 ? [429, {}] : n === 2 ? [529, {}] : [200, { content: [{ type: "text", text: "ok" }] }]));
    r = new ModelRouter("anthropic", { keys: ["k"], sleep: async (ms) => { waits.push(ms); }, log: () => {}, fetch: fa });
    t((await r.chat("fast", [{ role: "user", content: "x" }])) === "ok" && waits.join() === "2000,4000" && posts.length === 3,
      "付費遇到 429／5xx（伺服器明確拒絕、沒處理）照舊退避重試");
    const seen = [];
    const orf = async (url, init) => {
      if (init.method === "GET") return { status: 200, json: async () => ({ data: [{ id: "test/free-model:free" }] }), headers: { get: () => null } };
      seen.push(1); if (seen.length < 3) throw new TypeError("network");
      return { status: 200, json: async () => ({ choices: [{ message: { content: "free-ok" } }] }), headers: { get: () => null } };
    };
    r = new ModelRouter("openrouter", { keys: ["k"], sleep: async () => {}, log: () => {}, fetch: orf });
    t((await r.chat("fast", [{ role: "user", content: "x" }])) === "free-ok" && seen.length === 3, "OpenRouter 的 :free 模型不收費 → 逾時照樣重試");
    t(isPaid(PROVIDERS.openrouter, "vendor/paid-model") && !isPaid(PROVIDERS.openrouter, "x:free") && isPaid(PROVIDERS.anthropic, "x")
      && !isPaid(PROVIDERS.groq, "x") && isPaid(PROVIDERS.groq, "x", true) && isPaid({}, "x"), "付費判斷：看 PROVIDERS、可覆寫、沒標的當付費");
    let cnt = 0;
    const f500 = async (url, init) => (init.method === "GET" ? { status: 200, json: async () => ({ data: ids.map((id) => ({ id })) }), headers: { get: () => null } }
      : (cnt++, { status: 500, json: async () => ({}), headers: { get: () => null } }));
    r = new ModelRouter("groq", { keys: ["a", "b"], sleep: async () => {}, log: () => {}, fetch: f500, maxCalls: 5 });
    try { await r.chat("fast", [{ role: "user", content: "x" }]); t(false, "超過上限要丟錯"); }
    catch (e) { t(cnt === 5 && e.message.includes("上限 5") && e.message.includes("maxCalls"), "總次數上限：送滿 5 次就停，中文說明怎麼調"); }
    cnt = 0;
    try { await r.chat("fast", [{ role: "user", content: "x" }]); } catch (e) { /* 預期會丟錯 */ }
    t(cnt === 5, "上限每次 chat() 各自計算（下一次重新算）");
    const warned = [];
    t(new ModelRouter("groq", { log: (m) => warned.push(m), maxCalls: "abc" }).maxCalls === DEFAULT_MAX_CALLS && warned.length === 1,
      "maxCalls 看不懂 → 用預設並提醒");
    t(new ModelRouter("groq", { log: () => {} }).maxCalls === 12, "預設上限 12");

    // ── _http：真的 fetch 回應有 headers.get 與 text()（HTML 錯誤頁、日期格式的 Retry-After）──
    const res = (status, raw, hdrs) => ({ status, text: async () => raw,
      headers: { get: (n) => { const k = Object.keys(hdrs).find((x) => x.toLowerCase() === n.toLowerCase()); return k == null ? null : hdrs[k]; } } });
    r = new ModelRouter("groq", { keys: ["k"], log: () => {},
      fetch: async () => res(429, '{"error":{"message":"slow down"}}', { "Retry-After": "Fri, 15 Jan 2027 08:00:30 GMT", "X-RateLimit-Reset": "1800000060" }) });
    let [st, body] = await r._http("POST", "https://example.invalid/x", {}, { a: 1 });
    t(st === 429 && body._retry_after === "Fri, 15 Jan 2027 08:00:30 GMT" && body._ratelimit_reset === "1800000060"
      && JSON.stringify(classify(st, body, 1800000000)) === JSON.stringify({ kind: "rate", delay: 30 }),
      "_http：Retry-After 是日期也保留原字串（舊版只收純數字），x-ratelimit-reset 也帶上");
    r = new ModelRouter("groq", { keys: ["k"], log: () => {}, fetch: async () => res(404, "<html><h1>404 Not Found</h1></html>", {}) });
    [st, body] = await r._http("POST", "https://example.invalid/x", {}, { a: 1 });
    t(st === 404 && String(body._raw).includes("404 Not Found") && classify(st, body).kind === "config", "_http：HTML 錯誤頁放進 _raw，404 判斷成網址設定錯");
    // 設定錯誤的訊息會附上供應商回應，但自己的金鑰要遮掉
    r = new ModelRouter("groq", { keys: ["sk-test-SECRET-123"], log: () => {}, sleep: async () => {},
      fetch: async (url, init) => (init.method === "GET" ? res(200, '{"data":[{"id":"openai/gpt-oss-20b"}]}', {})
        : res(404, '{"error":{"message":"no route for key sk-test-SECRET-123"}}', {})) });
    try { await r.chat("fast", [{ role: "user", content: "x" }]); t(false, "404 網址錯要丟錯"); }
    catch (e) { t(!e.message.includes("SECRET") && e.message.includes("***") && e.message.includes("網址"), "404 網址錯：訊息附回應摘要、金鑰遮成 ***"); }

    await runShared(t, opts.strict);
    console.log("\n自我測試" + (ok ? "全部通過" : "有失敗"));
    return ok ? 0 : 1;
  }

  // ── 兩個語言共用的案例（router_cases.json）──
  const same = (a, b) => (a == null || b == null ? a == null && b == null : Math.abs(a - b) < 1e-6);
  const show = (v) => (v == null ? "None" : String(v));
  // 假的 fetch 回應：只有 _raw 的內容照原樣回（模擬 HTML／純文字錯誤頁），其他回 JSON 文字
  const fakeRes = (status, body, hdrs) => ({
    status,
    text: async () => (body && typeof body._raw === "string" && Object.keys(body).length === 1 ? body._raw : JSON.stringify(body)),
    headers: { get: (n) => { const k = Object.keys(hdrs || {}).find((x) => x.toLowerCase() === n.toLowerCase()); return k == null ? null : hdrs[k]; } },
  });
  function okBody(provider, text) {
    const st = PROVIDERS[provider].style;
    if (st === "gemini") return { candidates: [{ content: { parts: [{ text }] } }] };
    if (st === "anthropic") return { content: [{ type: "text", text }] };
    return { choices: [{ message: { content: text } }] };
  }
  async function runShared(t, strict) {
    const fs = require("fs"), path = require("path");
    const p = [path.join(__dirname, "router_cases.json"), path.join(__dirname, "..", "assets", "router_cases.json")].find((x) => fs.existsSync(x));
    if (!p) {
      if (strict) t(false, "找不到 router_cases.json（兩個語言共用的測試案例；--strict 一定要有）");
      else console.log("⚠️ 找不到 router_cases.json，共用案例沒跑。只把模組複製進 App 時是正常的；要跑完整測試就把 assets/router_cases.json 放在模組旁邊，或在 skl 裡跑。");
      return;
    }
    const C = JSON.parse(fs.readFileSync(p, "utf8"));
    const t0 = C.now;
    const sample = (item) => JSON.parse(JSON.stringify(typeof item === "string" ? C.samples[item] : item));   // 複本：withHeaders 會改 body
    const idsOf = (v) => (typeof v === "string" ? C.model_responses[v].data.map((m) => m.id) : v);
    for (const c of C.delays) {
      const got = parseDelay(c.value, c.now);
      t(same(got, c.seconds), "等待時間：" + c.name + "（" + JSON.stringify(c.value) + " → 期望 " + show(c.seconds) + "，得到 " + show(got) + "）");
    }
    for (const c of C.text_delays) {
      const got = textDelay(c.text);
      t(same(got, c.seconds), "訊息裡的等待時間：" + JSON.stringify(c.text) + " → 期望 " + show(c.seconds) + "，得到 " + show(got));
    }
    for (const c of C.classify) {
      const s = sample(c.sample);
      const info = classify(s.status, withHeaders(s.body || {}, s.headers), c.now != null ? c.now : t0);
      t(info.kind === c.kind && same(info.delay, c.delay),
        "分類：" + c.name + "（期望 " + c.kind + " " + show(c.delay) + "，得到 " + info.kind + " " + show(info.delay) + "）");
    }
    for (const c of C.think) {
      const got = stripThink(c.text);
      t(got === c.out, "<think> 處理：" + JSON.stringify(c.text) + " → " + JSON.stringify(got));
    }
    for (const c of C.rank) {
      const got = rank(idsOf(c.ids), c.provider, c.tier);
      t(JSON.stringify(got) === JSON.stringify(c.expect), "排序：" + c.name + "（得到 " + JSON.stringify(got) + "）");
    }
    for (const c of C.pick) {
      const resp = C.model_responses[c.response];
      const r = new ModelRouter(c.provider, { keys: ["k"], log: () => {}, sleep: async () => {}, fetch: async () => fakeRes(200, resp, {}) });
      const got = {};
      for (const tier of Object.keys(c.expect)) got[tier] = await r.pick(tier);
      t(JSON.stringify(got) === JSON.stringify(c.expect), "挑模型：" + c.name + "（得到 " + JSON.stringify(got) + "）");
    }
    for (const sc of C.scenarios) await runScenario(t, C, sc, sample);
  }
  // 整段情境：假 fetch（照真實格式回應）＋假時鐘；每一步都 new 一個 ModelRouter，驗證冷卻是整個頁面共用的
  async function runScenario(t, C, sc, sample) {
    resetCooldowns();
    let clock = C.now;
    const queues = {};
    for (const [k, v] of Object.entries(sc.responses || {})) queues[k] = v.slice();
    for (const [i, step] of sc.steps.entries()) {
      if (step.advance != null) { clock += step.advance; continue; }
      const posts = [], waits = [];
      const fetchFn = async (url, init) => {
        if (init.method === "GET") {
          if (typeof sc.models === "string") return fakeRes(200, C.model_responses[sc.models], {});
          return fakeRes(200, sc.provider === "gemini"
            ? { models: sc.models.map((m) => ({ name: "models/" + m, supportedGenerationMethods: ["generateContent"] })) }
            : { data: sc.models.map((id) => ({ id })) }, {});
        }
        const sent = init.body ? JSON.parse(init.body) : {};
        const model = sent.model || decodeURIComponent((url.match(/\/models\/([^/:]+):generateContent/) || [])[1] || "");
        const h = init.headers || {};
        const key = (h.Authorization || "").replace(/^Bearer /, "") || h["x-goog-api-key"] || h["x-api-key"] || "";
        posts.push(model);
        const q = queues[model + "#" + key] || queues[model];
        const spec = sample(q ? (q.length > 1 ? q.shift() : q[0]) : "ok");
        if (spec.status === 200) return fakeRes(200, okBody(sc.provider, spec.text != null ? spec.text : "ok:" + model), {});
        if (spec.status === 0) throw new TypeError("network");
        return fakeRes(spec.status, spec.body || {}, spec.headers || {});
      };
      const r = new ModelRouter(sc.provider, { keys: step.keys || ["k"], fetch: fetchFn, log: () => {}, maxCalls: step.max_calls,
        sleep: async (ms) => { waits.push(ms / 1000); clock += ms / 1000; }, now: () => clock * 1000 });
      let out = null, err = null;
      try { out = await r.chat(step.tier || "fast", [{ role: "user", content: "x" }], { escalate: step.escalate !== false }); }
      catch (e) { err = String((e && e.message) || e); }
      const exp = step.expect;
      let good = JSON.stringify(posts) === JSON.stringify(exp.posts);
      if (exp.waits) good = good && waits.length === exp.waits.length && exp.waits.every((w, j) => same(waits[j], w));
      if ("result" in exp) good = good && out === exp.result;
      if (exp.error_has) good = good && err != null && exp.error_has.every((s) => err.includes(s));
      t(good, "情境：" + sc.name + "（第 " + (i + 1) + " 步）" + (good ? "" : " posts=" + JSON.stringify(posts) + " waits=" + JSON.stringify(waits) + " out=" + out + " err=" + err));
    }
    resetCooldowns();
  }

  const api = { ModelRouter, rank, pickTier, isPaid, classify, parseDelay, textDelay, stripThink, withHeaders, resetCooldowns,
    selftest, TIERS, PROVIDERS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else Object.assign(root, api);
  if (typeof require !== "undefined" && typeof module !== "undefined" && require.main === module && process.argv[2] === "selftest")
    selftest({ strict: process.argv.includes("--strict") }).then((c) => process.exit(c));
})(typeof globalThis !== "undefined" ? globalThis : this);
