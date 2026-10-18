// App 開發全流程（dev-guide.html）與 AI Agent 製作流程（agent.html）共用的程式：
// 分頁、路線圖與進度清單、一題一題的精靈、範例庫、我的 App 清單、出處清單。
// 頁面專屬的內容（題目、範例、產生指令的規則）放在各頁，這裡只放兩頁都一樣的機制，避免兩份程式各自壞掉。
"use strict";
const Guide = (() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, props = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) e.append(kid);
    return e;
  };
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 無痕模式或空間滿：只是記不住，不影響使用 */ } },
  };
  function toast(m) { const t = $("toast"); if (!t) return; t.textContent = m; t.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("show"), 1700); }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); }
    catch {
      // 舊版 Safari 或非 https 時 clipboard API 會失敗，退回選取文字後 execCommand
      const ta = document.createElement("textarea"); ta.value = text; ta.setAttribute("readonly", "");
      ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, text.length);
      let ok = false; try { ok = document.execCommand("copy"); } catch { ok = false; }
      ta.remove(); if (!ok) { toast("無法自動複製，請長按文字框手動複製"); return false; }
    }
    toast("已複製 ✓"); return true;
  }
  // 頁面裡 <script type="application/json"> 的資料（題目、範例、參考資源）：Python 檢查程式也讀同一份
  function data(id) { const s = $(id); return s ? JSON.parse(s.textContent) : null; }

  /* ── 技能庫取得方式與「裝完接著做」規則：技能庫網頁（index.html）有一份一模一樣的，E2E 會比對 ── */
  const REPO = "https://github.com/xin7355-collab/skl";
  const FETCH_CMD = `(git -C /tmp/skl pull -q 2>/dev/null || git clone -q --depth 1 ${REPO} /tmp/skl)`;
  const FOLLOW_UP = `裝好後不要停，接著做：\n` +
    `1. 先列出「執行方案」：每個新技能在這個 App 要拿來做什麼、會改哪些檔案、技能需要的套件或工具怎麼裝` +
    `（寫進 package.json／requirements.txt／工作流，下次開工作階段才還在）、怎麼驗證、有沒有費用或要我準備的東西（金鑰、帳號）。\n` +
    `2. 列完就直接照方案做，不用等我回覆；需要我提供的東西（金鑰、帳號、付款），或這個 repo 的 CLAUDE.md 沒授權的動作，才先停下來問我。\n` +
    `3. 每一項做完就驗證（有測試就跑；網頁用 Playwright 開手機尺寸看），照 CLAUDE.md 合併推送，最後回報做了什麼、驗過什麼、沒驗到什麼。`;

  /* ── 分頁 ── */
  let tabKey = "", tabs = [];
  function showTab(id, opts = {}) {
    if (!tabs.includes(id)) id = tabs[0];
    for (const b of document.querySelectorAll("nav.tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === id));
    for (const t of tabs) $("tab-" + t).hidden = t !== id;
    store.set(tabKey, id);
    if (!opts.keepScroll) window.scrollTo(0, 0);
  }
  function initTabs(key, ids) {
    tabKey = key; tabs = ids;
    for (const b of document.querySelectorAll("nav.tabs button")) b.addEventListener("click", () => showTab(b.dataset.tab));
    // 頁面內的「#tab-xxx」「#st-xxx」連結：切到那個分頁、打開那一步，而不是只改網址
    document.addEventListener("click", (e) => {
      const a = e.target.closest("a[href^='#'], [data-go]");
      if (!a) return;
      // 全部做完時「下一步」沒有目標（data-go 是空的），什麼都不做
      const target = a.hasAttribute("data-go") ? a.dataset.go : (a.getAttribute("href") || "").slice(1);
      if (!target) return;
      if (target.startsWith("tab-")) { e.preventDefault(); showTab(target.slice(4)); }
      // 其他分頁裡的目標（某一步、某個案例、常見卡關）：先切分頁再捲過去，否則藏起來的分頁跳不過去
      else if ($(target)) { e.preventDefault(); goTo(target); }
    });
    showTab(store.get(key, ids[0]), { keepScroll: true });
    // 分頁列是兩排、會隨字級與螢幕寬度變高，用實際高度當捲動的保留空間
    const nav = document.querySelector("nav.tabs");
    const setNavH = () => { if (nav) document.documentElement.style.setProperty("--navh", nav.offsetHeight + "px"); };
    setNavH(); window.addEventListener("resize", setNavH);
  }
  // 頁面可以在跳到某一步之前先做準備（例如 Agent 頁要先切到那一步所屬的做法，不然那一步是藏起來的）
  let beforeGo = null;
  function onBeforeGo(fn) { beforeGo = fn; }
  function goTo(id) {
    const node = $(id);
    if (!node) return;
    if (beforeGo) beforeGo(node);
    const sec = node.closest("section[id^='tab-']");
    if (sec) showTab(sec.id.slice(4), { keepScroll: true });
    if (node.tagName === "DETAILS") node.open = true;
    requestAnimationFrame(() => node.scrollIntoView({ block: "start" }));
  }

  /* ── 進度清單：教學的每一步可以打勾，路線圖跟著變色並指出下一步 ── */
  function initProgress({ key, visible = () => true, doneText = "全部完成 🎉" }) {
    const done = store.get(key, {});
    const steps = () => [...document.querySelectorAll("details.step[data-step]")].filter(visible);
    function paint() {
      // 可略過的步驟（例如雛形、上架）沒做不算落後；做了才算進度
      const list = steps().filter((s) => !s.hasAttribute("data-optional") || done[s.id]);
      const nDone = list.filter((s) => done[s.id]).length;
      const next = list.find((s) => !done[s.id]);
      for (const s of document.querySelectorAll("details.step[data-step]")) {
        s.classList.toggle("is-done", !!done[s.id]);
        const b = s.querySelector(".donebtn"); if (b) b.textContent = done[s.id] ? "✓ 已完成（再按一次取消）" : "這一步做完了 ✓";
      }
      for (const n of document.querySelectorAll("[data-node]")) {
        n.classList.toggle("done", !!done[n.dataset.node]);
        n.classList.toggle("next", !!next && next.id === n.dataset.node);
      }
      const pct = list.length ? Math.round(nDone / list.length * 100) : 0;
      for (const p of document.querySelectorAll("[data-progress]")) {
        p.querySelector("b").textContent = `進度 ${nDone}／${list.length}`;
        p.querySelector(".bar i").style.width = pct + "%";
        const nb = p.querySelector("[data-next]");
        if (nb) {
          nb.textContent = next ? "下一步：" + (next.dataset.title || "") + " →" : doneText;
          nb.dataset.go = next ? next.id : "";
        }
      }
      for (const c of document.querySelectorAll("[data-checklist]")) {
        c.textContent = "";
        for (const s of list) {
          const id = "chk-" + s.id;
          const box = el("input", { type: "checkbox", id, onchange: () => { toggle(s.id, box.checked); } });
          box.checked = !!done[s.id];
          c.append(el("label", { class: "opt", for: id }, box,
            el("div", {}, el("b", { text: s.dataset.title || s.id }), s.dataset.when ? el("small", { text: s.dataset.when }) : null)));
        }
      }
    }
    function toggle(id, on) {
      if (on) done[id] = true; else delete done[id];
      store.set(key, done); paint();
    }
    for (const s of document.querySelectorAll("details.step[data-step]")) {
      const b = s.querySelector(".donebtn");
      if (b) b.addEventListener("click", () => { toggle(s.id, !done[s.id]); if (done[s.id]) toast("已記下，進度 +1"); });
    }
    for (const n of document.querySelectorAll("[data-node]")) n.addEventListener("click", () => goTo(n.dataset.node));
    for (const r of document.querySelectorAll("[data-reset-progress]"))
      r.addEventListener("click", () => { for (const k of Object.keys(done)) delete done[k]; store.set(key, done); paint(); toast("進度已清空"); });
    paint();
    return { paint, toggle };
  }

  /* ── 精靈：一次一題，最後才產生指令 ── */
  // def.questions：[{id, title, hint, fields:[{id, type:text|textarea|single|multi, label, options:[{v,label,desc}], default, showIf}]}]
  function createWizard({ root, def, storeKey, build, migrate }) {
    const fields = def.questions.flatMap((q) => q.fields);
    const defaults = () => Object.fromEntries(fields.map((f) => [f.id, f.default !== undefined ? clone(f.default) : (f.type === "multi" ? [] : "")]));
    function clone(v) { return Array.isArray(v) ? v.slice() : v; }
    // 舊版存檔、範例都經過這裡：不認得的欄位丟掉、不在選項裡的值回到預設，畫面才不會出現空白選項
    function normalize(raw) {
      const a = defaults();
      if (!raw || typeof raw !== "object") return a;
      for (const f of fields) {
        if (!(f.id in raw)) continue;
        const v = raw[f.id];
        if (f.type === "multi") { const ok = new Set(f.options.map((o) => o.v)); a[f.id] = (Array.isArray(v) ? v : []).filter((x) => ok.has(x)); }
        else if (f.type === "single") { if (f.options.some((o) => o.v === v)) a[f.id] = v; }
        else a[f.id] = String(v ?? "");
      }
      return a;
    }
    let saved = store.get(storeKey, null);
    if (!saved && migrate) saved = migrate();
    const st = { screen: saved && saved.screen !== undefined ? saved.screen : "start", answers: normalize(saved && saved.answers), returnToSum: false };
    if (typeof st.screen === "number" && !(st.screen >= 0 && st.screen < def.questions.length)) st.screen = "start";
    const save = () => store.set(storeKey, { screen: st.screen, answers: st.answers });
    const shown = (f) => !f.showIf || [].concat(f.showIf.in).includes(st.answers[f.showIf.field]);
    const label = (f, v) => (f.options || []).find((o) => o.v === v)?.label || v;

    function renderStart() {
      const d0 = defaults();
      const started = Object.keys(st.answers).some((k) => JSON.stringify(st.answers[k]) !== JSON.stringify(d0[k]));
      return el("div", {},
        el("h2", { text: def.startTitle || "要從哪裡開始？" }),
        el("p", { class: "hint", text: def.startHint || "" }),
        el("div", { class: "opts" },
          el("button", { class: "btn", "data-wiz": "begin", onclick: () => go(0) }, `從頭開始（${def.questions.length} 題，約 3 分鐘）`),
          el("button", { class: "btn ghost", "data-wiz": "examples", onclick: () => showTab(def.examplesTab || "examples") }, "從範例開始（點一下就填好）"),
          started ? el("button", { class: "btn ghost", "data-wiz": "resume", onclick: () => go("summary") }, "看上次填的結果") : null));
    }
    function renderField(f) {
      const v = st.answers[f.id];
      const wrap = el("div", { class: "field", "data-field": f.id });
      wrap.append(el("div", { class: "flabel", text: f.label || "" }));
      if (f.help) wrap.append(el("p", { class: "hint", text: f.help }));
      if (f.type === "text" || f.type === "textarea") {
        const inp = el(f.type === "text" ? "input" : "textarea", { class: "in", id: "w-" + f.id, placeholder: f.placeholder || "" });
        inp.value = v || "";
        inp.addEventListener("input", () => { st.answers[f.id] = inp.value; save(); });
        wrap.append(inp);
      } else {
        const box = el("div", { class: "opts" });
        for (const o of f.options) {
          const id = `w-${f.id}-${o.v}`;
          const inp = el("input", { type: f.type === "single" ? "radio" : "checkbox", name: "w-" + f.id, id, value: o.v });
          inp.checked = f.type === "single" ? v === o.v : v.includes(o.v);
          inp.addEventListener("change", () => {
            if (f.type === "single") st.answers[f.id] = o.v;
            else st.answers[f.id] = f.options.map((x) => x.v).filter((x) => $(`w-${f.id}-${x}`).checked);
            save();
            // 條件題（例如選「改版」才問 repo 網址）要跟著出現或消失
            if (fields.some((g) => g.showIf && g.showIf.field === f.id)) render();
          });
          box.append(el("label", { class: "opt", for: id }, inp, el("div", {}, el("b", { text: o.label }), o.desc ? el("small", { text: o.desc }) : null)));
        }
        wrap.append(box);
      }
      return wrap;
    }
    function renderQuestion(i) {
      const q = def.questions[i];
      const n = def.questions.length;
      const box = el("div", {},
        el("div", { class: "wtop" }, el("span", { text: `第 ${i + 1}／${n} 題` }), el("button", { class: "linkbtn", onclick: () => go("summary") }, "直接看結果")),
        el("div", { class: "bar" }, el("i", { style: `width:${Math.round((i + 1) / n * 100)}%` })),
        el("h2", { text: q.title }),
        q.hint ? el("p", { class: "hint", text: q.hint }) : null);
      for (const f of q.fields) if (shown(f)) box.append(renderField(f));
      const back = st.returnToSum ? "回到結果" : (i === 0 ? "回開頭" : "上一步");
      const fwd = st.returnToSum ? "改好了，看結果" : (i === n - 1 ? "看結果 →" : "下一步 →");
      box.append(el("div", { class: "nav" },
        el("button", { class: "btn ghost", "data-wiz": "back", onclick: () => go(st.returnToSum ? "summary" : (i === 0 ? "start" : i - 1)) }, back),
        el("button", { class: "btn", "data-wiz": "next", onclick: () => go(st.returnToSum || i === n - 1 ? "summary" : i + 1) }, fwd)));
      return box;
    }
    function answerText(f) {
      const v = st.answers[f.id];
      if (f.type === "multi") return v.length ? v.map((x) => label(f, x)).join("、") : "（沒有選）";
      if (f.type === "single") return label(f, v) || "（沒有選）";
      return v ? v : "（沒有填）";
    }
    function renderSummary() {
      const out = build(st.answers);
      const box = el("div", {}, el("h2", { text: "結果：照下面做" }));
      if (out.rec) box.append(el("div", { class: "rec", "data-way": out.rec.way || "" }, el("b", { text: out.rec.title }), el("div", { class: "hint", style: "margin:4px 0 0", text: out.rec.why })));
      if (out.warns && out.warns.length) box.append(el("ul", { class: "warns" }, out.warns.map((w) => el("li", { text: "⚠️ " + w }))));
      if (out.packs && out.packs.length) box.append(el("p", { class: "hint" }, "技能組合：", el("span", { class: "chip", id: "wizPacks", text: out.packs.join(", ") })));
      for (const b of out.blocks) {
        box.append(el("h3", { text: b.title }));
        if (b.hint) box.append(el("p", { class: "hint", text: b.hint }));
        const ta = el("textarea", { class: "out", id: b.id, readonly: true });
        ta.value = b.text;
        box.append(ta, el("div", { class: "row" }, el("button", { class: "btn", "data-copy": b.id, onclick: () => copyText(ta.value) }, b.copyLabel || "複製"),
          b.link ? el("a", { class: "btn ghost", href: b.link.href, target: "_blank", rel: "noopener", "data-open": b.id }, b.link.text) : null));
        if (b.link && b.link.note) box.append(el("p", { class: "hint", text: b.link.note }));
      }
      if (out.next && out.next.length) {
        box.append(el("h3", { text: "接下來" }));
        box.append(el("ol", {}, out.next.map((n) => el("li", {}, n.step ? el("a", { href: "#" + n.step, text: n.text }) : n.text))));
      }
      box.append(el("h3", { text: "你的回答（可以改）" }));
      def.questions.forEach((q, i) => {
        const items = q.fields.filter(shown).map((f) => `${f.label}：${answerText(f)}`);
        box.append(el("div", { class: "sum" }, el("div", {}, el("b", { text: `${i + 1}. ${q.title}` }), el("span", { text: items.join("　") })),
          el("button", { class: "btn ghost small", "data-edit": q.id, onclick: () => { st.returnToSum = true; go(i, true); } }, "改")));
      });
      box.append(el("div", { class: "row" },
        el("button", { class: "btn ghost", "data-wiz": "restart", onclick: () => { st.answers = defaults(); go("start"); toast("已清空，重新開始"); } }, "全部重來")));
      return box;
    }
    function render() {
      root.textContent = "";
      root.append(st.screen === "start" ? renderStart() : st.screen === "summary" ? renderSummary() : renderQuestion(st.screen));
    }
    function go(screen, keepReturn) {
      if (!keepReturn && screen !== "summary") st.returnToSum = false;
      if (screen === "summary") st.returnToSum = false;
      st.screen = screen; save(); render();
      root.scrollIntoView({ block: "start" });
    }
    render();
    return {
      // 範例、我的 App 都從這裡把答案帶進來，直接跳到結果
      apply(raw, msg) { st.answers = normalize(Object.assign(defaults(), raw)); go("summary"); showTab(def.wizardTab || "start"); if (msg) toast(msg); },
      answers: () => st.answers,
      go,
    };
  }

  /* ── 範例庫 ── */
  // skillsCat：額外一個篩選鈕，只顯示用到 skl 技能的範例（範例的 skills 欄位）
  function renderGallery({ root, filtersRoot, items, cats, onApply, skillsCat = "", xray = "xray.html" }) {
    let cur = "全部";
    const match = (x) => cur === "全部" || (skillsCat && cur === skillsCat ? (x.skills || []).length > 0 : x.cat === cur);
    function paint() {
      root.textContent = "";
      for (const t of items.filter(match)) {
        const refs = (t.refs || []).map((r) => r.repo
          ? el("div", {}, "參考：", el("a", { href: `${xray}?repo=${encodeURIComponent(r.repo)}`, text: r.repo }), r.why ? "（" + r.why + "）" : "")
          : el("div", {}, "參考：", el("a", { href: r.url, target: "_blank", rel: "noopener", text: r.name }), r.why ? "（" + r.why + "）" : ""));
        root.append(el("div", { class: "tcard", "data-tpl": t.id },
          el("h3", { text: `${t.icon} ${t.title}` }),
          el("div", { class: "meta", text: `${t.cat}　難度 ${"★".repeat(t.level)}${"☆".repeat(3 - t.level)}${t.meta ? "　" + t.meta : ""}` }),
          el("p", { text: t.desc }),
          (t.skills || []).length ? el("div", { class: "tskills" }, "用到的技能：", ...t.skills.map((n) => el("span", { class: "chip", text: n }))) : null,
          refs.length ? el("div", { class: "refs" }, refs) : null,
          el("div", { class: "row" }, el("button", { class: "btn small", "data-apply": t.id, onclick: () => onApply(t) }, "套用到起手式"))));
      }
    }
    filtersRoot.textContent = "";
    for (const c of ["全部", ...(skillsCat ? [skillsCat] : []), ...cats]) {
      const b = el("button", { "aria-pressed": String(c === cur), "data-cat": c, onclick: () => { cur = c; for (const x of filtersRoot.children) x.setAttribute("aria-pressed", String(x.dataset.cat === c)); paint(); } }, c);
      filtersRoot.append(b);
    }
    paint();
  }

  /* ── 我的 App：部署時把 .github/apps.json 複製成網站的 apps.json ── */
  async function loadApps() {
    try {
      const r = await fetch("apps.json", { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      const d = await r.json();
      return (d.apps || []).filter((a) => /^[\w.-]+\/[\w.-]+$/.test(a.repo || ""));
    } catch { return null; }
  }
  async function renderMyApps({ root, actionLabel, onPick, failNote = "讀不到 App 清單（apps.json 部署後才有）。", xray = "xray.html" }) {
    root.textContent = "";
    root.append(el("p", { class: "hint", text: "讀取 App 清單中…" }));
    const apps = await loadApps();
    root.textContent = "";
    if (!apps) {
      root.append(el("div", { class: "note", text: failNote }));
      return;
    }
    for (const a of apps) {
      root.append(el("div", { class: "sum", "data-app": a.repo },
        el("div", {}, el("b", { text: a.repo.split("/")[1] + (a.private ? "（私人）" : "") }),
          el("span", {}, el("a", { href: `${xray}?repo=${encodeURIComponent(a.repo)}`, text: "用 X-Ray 看" }), a.url ? "　" : "", a.url ? el("a", { href: a.url, target: "_blank", rel: "noopener", text: "打開 App" }) : "")),
        el("button", { class: "btn ghost small", onclick: () => onPick(a) }, actionLabel)));
    }
  }

  /* ── 出處清單：從頁面上的出處連結自動整理，避免清單和內文對不起來 ── */
  function listSources() {
    const seen = new Map();
    for (const a of document.querySelectorAll("main a.src")) {
      const u = a.href; if (!seen.has(u)) seen.set(u, a.textContent.replace(/^↗/, ""));
    }
    const ul = $("srcs"); if (!ul) return;
    for (const [u, name] of seen) ul.append(el("li", {}, name.endsWith("※") ? "※ " : "", el("a", { href: u, target: "_blank", rel: "noopener", text: u })));
  }

  /* ── 參考資源 ── */
  function renderRefs({ root, filtersRoot, items, kinds, xray = "xray.html" }) {
    let cur = "全部";
    function paint() {
      root.textContent = "";
      for (const r of items.filter((x) => cur === "全部" || x.kind === cur)) {
        const links = [];
        if (r.url) links.push(el("a", { href: r.url, target: "_blank", rel: "noopener", text: r.url.replace(/^https:\/\//, "") }));
        if (r.repo) links.push(el("a", { href: `${xray}?repo=${encodeURIComponent(r.repo)}`, text: "用 X-Ray 看 " + r.repo }));
        root.append(el("div", { class: "refcard", "data-ref": r.name },
          el("h3", { text: (r.top ? "⭐ " : "") + r.name }),
          el("div", { class: "meta", text: [r.kind, r.lang, r.phone, r.cost].filter(Boolean).join("　") }),
          el("p", { text: r.why }),
          r.take ? el("p", { class: "hint", text: "可以學的：" + r.take }) : null,
          el("div", { class: "meta" }, ...links.flatMap((l, i) => (i ? ["　", l] : [l])))));
      }
    }
    filtersRoot.textContent = "";
    for (const k of ["全部", ...kinds])
      filtersRoot.append(el("button", { "aria-pressed": String(k === cur), "data-kind": k, onclick: () => { cur = k; for (const x of filtersRoot.children) x.setAttribute("aria-pressed", String(x.dataset.kind === k)); paint(); } }, k));
    paint();
  }

  /* ── 教學裡的「要貼的指令」：<pre data-copybox> 自動加上複製按鈕 ── */
  function wireCopyBoxes() {
    for (const pre of document.querySelectorAll("pre[data-copybox]")) {
      const wrap = el("div", { class: "copybox" });
      pre.replaceWith(wrap); wrap.append(pre, el("button", { class: "btn small", onclick: () => copyText(pre.textContent) }, "複製"));
    }
  }

  function checkVersion(ver) {
    if (!ver || ver.startsWith("__")) return;
    document.addEventListener("visibilitychange", async () => {
      if (document.hidden) return;
      try { const r = await fetch("version.json", { cache: "no-store" }); if (!r.ok) return;
        const v = await r.json(); if (v.ver && v.ver !== ver) location.reload(); } catch { /* 離線略過 */ }
    });
  }

  return { $, el, store, toast, copyText, data, REPO, FETCH_CMD, FOLLOW_UP, initTabs, showTab, goTo, onBeforeGo, initProgress, createWizard,
    renderGallery, renderMyApps, renderRefs, listSources, wireCopyBoxes, checkVersion };
})();
