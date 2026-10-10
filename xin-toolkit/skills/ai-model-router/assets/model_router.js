/* model_router.js — 網頁／Cloudflare Worker／Node 用的 AI 模型自動挑選（與 scripts/model_router.py 同一套規則）。
 *
 * 為什麼：寫死模型名稱，供應商一下架整個功能就 404（使用者的 App 實際踩過 gemini-1.5-flash、llama-3.1-8b-instant）。
 * 做法：問官方現在有哪些模型 → 依等級偏好序挑最新 → 404／下架自動換下一個 → 429／5xx 指數退避 →
 *       結果不合格自動升級（fast → smart → best）。金鑰只放 HTTP 標頭，不放網址、不印出來。
 *
 * 用法（瀏覽器：<script src="model_router.js"></script>；Worker／Node：import 或 require）：
 *   const r = new ModelRouter("gemini", { keys: [apiKey] });
 *   const text = await r.chat("auto", [{ role: "user", content: "把這段翻成中文：..." }]);
 *   node model_router.js selftest
 */
(function (root) {
  "use strict";
  const TIERS = ["fast", "smart", "best"];
  const PROVIDERS = {
    groq: { list: "https://api.groq.com/openai/v1/models", chat: "https://api.groq.com/openai/v1/chat/completions", style: "openai" },
    openrouter: { list: "https://openrouter.ai/api/v1/models", chat: "https://openrouter.ai/api/v1/chat/completions", style: "openai" },
    gemini: { list: "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
              chat: "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", style: "gemini" },
    anthropic: { list: "https://api.anthropic.com/v1/models?limit=100", chat: "https://api.anthropic.com/v1/messages", style: "anthropic" },
  };
  const PREFS = {
    groq: { fast: [/llama-\d+(\.\d+)?-8b/i, /instant/i, /gpt-oss-20b/i, /llama/i],
            smart: [/llama-\d+(\.\d+)?-70b/i, /gpt-oss-120b/i, /70b/i, /llama/i],
            best: [/gpt-oss-120b/i, /llama-4/i, /70b/i, /llama/i] },
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
  const MISSING = /model_not_found|model_decommissioned|does not exist|not found|decommissioned|no longer|deprecated|not supported/i;
  const TTL = 6 * 3600 * 1000;   // 模型下架是常態，不可永久快取

  function versionCmp(a, b) {
    const na = (a.match(/\d+/g) || []).map(Number), nb = (b.match(/\d+/g) || []).map(Number);
    for (let i = 0; i < Math.max(na.length, nb.length); i++) {
      const x = na[i] == null ? -1 : na[i], y = nb[i] == null ? -1 : nb[i];
      if (x !== y) return y - x;
    }
    return a.length - b.length;
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
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  class ModelRouter {
    constructor(provider, opts) {
      if (!PROVIDERS[provider]) throw new Error("不支援的供應商：" + provider + "（可用：" + Object.keys(PROVIDERS).join("、") + "）");
      opts = opts || {};
      this.p = provider; this.cfg = PROVIDERS[provider];
      this.keys = (opts.keys || []).filter(Boolean);
      this.fetch = opts.fetch || ((...a) => fetch(...a));
      this.sleep = opts.sleep || sleep;
      this.override = opts.override || {};      // 例如 { fast: "gemini-2.5-flash" }，等同 Python 的環境變數覆寫  // guardrails: ignore（範例／測試資料）
      this.log = opts.log || ((m) => console.warn(m));
      this._ids = null; this._at = 0; this._good = {};
    }
    _headers(key) {
      const st = this.cfg.style;
      if (st === "gemini") return { "x-goog-api-key": key };
      // 瀏覽器直接呼叫 Claude API 需要這個標頭；金鑰是使用者自己的，只存在他的裝置
      if (st === "anthropic") return { "x-api-key": key, "anthropic-version": "2023-06-01", "anthropic-dangerous-direct-browser-access": "true" };
      return { Authorization: "Bearer " + key };
    }
    async _http(method, url, headers, payload) {
      try {
        const r = await this.fetch(url, { method, headers: Object.assign({}, headers, payload ? { "Content-Type": "application/json" } : {}),
                                          body: payload ? JSON.stringify(payload) : undefined });
        let body = {}; try { body = await r.json(); } catch (e) { body = {}; }
        const ra = r.headers && r.headers.get && r.headers.get("retry-after");
        if (ra && /^\d+$/.test(ra)) body._retry_after = ra;
        return [r.status, body];
      } catch (e) { return [0, {}]; }
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
    async pick(tier, avoid) {
      const c = this.override[tier] ? [this.override[tier]] : [];
      const ids = await this.models();
      rank(ids, this.p, tier, avoid).forEach((m) => { if (!c.includes(m)) c.push(m); });
      const g = this._good[tier];
      if (g && !(avoid || new Set()).has(g) && !c.includes(g)) c.push(g);
      if (!c.length && ids.length) this.log("⚠️ [" + this.p + "] 等級 " + tier + " 沒配到模型；目前有：" + ids.slice(0, 25).join(", "));
      return c;
    }
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
      return (((b.choices || [{}])[0].message) || {}).content || "";
    }
    async _call(model, messages, maxTokens, tried) {
      const [url, payload] = this._request(model, messages, maxTokens);
      for (let ki = 0; ki < Math.max(1, this.keys.length); ki++) {
        for (let attempt = 0; attempt < 4; attempt++) {
          const [st, body] = await this._http("POST", url, this._headers(this.keys[ki] || ""), payload);
          if (st === 200) { const t = this._text(body); if (t.trim()) return t; tried.push(model + "（回了空白）"); return null; }
          const err = JSON.stringify(body).slice(0, 300);
          if (st === 404 || (st === 400 && MISSING.test(err))) { tried.push(model + "（已下架或不存在）"); this._ids = null; return "MISSING"; }
          if (st === 401 || st === 403) { tried.push(model + "（第 " + (ki + 1) + " 把金鑰被拒 " + st + "）"); break; }
          if (st === 429 || st >= 500 || st === 0) {
            let wait = Math.min(30, 2 ** (attempt + 1));
            if (body._retry_after) wait = Math.min(60, Math.max(wait, +body._retry_after));
            if (attempt < 3) { await this.sleep(wait * 1000); continue; }
            tried.push(model + "（" + (st === 429 ? "額度用完" : "服務忙碌") + " " + st + "）"); return null;
          }
          tried.push(model + "（HTTP " + st + "）"); return null;
        }
      }
      return null;
    }
    async chat(tier, messages, opts) {
      opts = opts || {};
      tier = tier === "auto" ? pickTier(messages) : tier;
      if (!TIERS.includes(tier)) throw new Error("不認識的等級：" + tier + "（用 fast／smart／best／auto）");
      const order = opts.escalate === false ? [tier] : TIERS.slice(TIERS.indexOf(tier));
      const tried = [], avoid = new Set();
      for (const t of order) {
        for (const model of await this.pick(t, avoid)) {
          const r = await this._call(model, messages, opts.maxTokens || 1024, tried);
          if (r === "MISSING") { avoid.add(model); continue; }
          if (r == null) continue;
          if (opts.validate && !opts.validate(r)) { tried.push(model + "（回覆不合格，升級）"); break; }
          this._good[t] = model; return r;
        }
      }
      throw new Error("[" + this.p + "] 所有模型都失敗了，試過：" + (tried.join("；") || "沒有可用的模型") + "。請確認金鑰有設定、額度還夠。");
    }
  }

  async function selftest() {
    let ok = true;
    const t = (c, n) => { console.log((c ? "PASS " : "FAIL ") + n); ok = ok && !!c; };
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
    console.log("\n自我測試" + (ok ? "全部通過" : "有失敗"));
    return ok ? 0 : 1;
  }

  const api = { ModelRouter, rank, pickTier, selftest, TIERS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else Object.assign(root, api);
  if (typeof require !== "undefined" && typeof module !== "undefined" && require.main === module && process.argv[2] === "selftest")
    selftest().then((c) => process.exit(c));
})(typeof globalThis !== "undefined" ? globalThis : this);
