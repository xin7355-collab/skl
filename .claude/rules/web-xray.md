---
paths:
  - "web/xray.html"
  - "web/build_trending.py"
  - "web/advisor_rules.json"
  - "web/check_advisor_rules.py"
  - "web/e2e/xray.spec.js"
  - ".github/apps.json"
---

# Repo X-Ray（web/xray.html）

（從 CLAUDE.md 搬來：只有讀或改到上面這些檔案時才載入，其他工作不用每次都帶著。）

- `web/xray.html`：Repo X-Ray（使用者提供的單檔工具，GitHub 專案透視）。合併搜尋器（網址直接掃、關鍵字搜尋）、
  上一步／瀏覽器返回不重查、搜尋結果右側大字星星數（`fmtStars`）＋小字檔案大小與排序、localStorage 快取 1 小時（超過 200KB 不存）省 API 額度。
  熱門推薦由 `web/build_trending.py` 在 `Deploy web page`（每天 06:00 排程＋push）收集成 `trending.json`（不進 git）；
  另有模糊搜尋（同義詞＋OR 放寬＋本機重排）、結果內模糊篩選、成長速度（★/天）、
  繁中翻譯（Google 免費端點，熱門清單部署時預翻）、專案類型判斷與「參考它做我的 App」提示。
  分三個分頁（探索 GitHub／我的 App／紀錄）；App 顧問只給自己的 App（apps.json 或 xin7355-collab 帳號），外部專案不跑。
  看過／下載過／收藏紀錄存 localStorage `xray-hist-v1`；「白話重點」取代技術指紋、API 端點、檔案類型分佈。
  批次勾選（localStorage `xray-pick-v1`，用途範本 `PURPOSES`，股票類自動補回測檢查）→ 複製網址＋要求給 Claude；
  每次複製記在 `xray-picklog-v1` 與紀錄的 cp 欄位。搜尋一次 100 筆；分類籤 `CATS`／`catsOf`（手機上分類）＋進階條件（GitHub qualifier）。
  翻譯快取 `xray-tr-v2`（v1 有混送時存下的未翻譯英文，已棄用）；翻譯請求 10 秒逾時、每批翻好就重畫。
  說明文件清單每份附一句中文說明（SKILL.md description／第一段，摘要快取 `xray-docsum-v1`）；授權、行為準則等制式文件用 `DOC_KNOWN`
  直接標中文、不送翻譯（省請求避免被 Google 限流）。翻譯被限流（429／連線失敗）時 `trFetch` 斷路 3 分鐘。
  專案地圖 `paintMap`（資料夾用途規則 `DIR_ROLES`，只用 `res.skel`）；「複製整包重點給 AI」`buildPack`（上限 `PACK_CAP`，只用已抓內容）；
  外部深入看 `EXT_TOOLS`（私人 repo 不顯示）。README 取路徑最淺的那份（`.claude/skills/x/README.md` 不能搶走根目錄的）。
  Python 判斷只看主要依賴（`pyMainDeps`，dev 群組不算）；有 `[project]` 的 pyproject 是套件，docs 裡的 index.html 不算網站。
  翻譯：簡體也送去轉繁體、簡體與英文分批送（混送時英文會原樣傳回）；簡體字表在 xray.html 的 SIMP_RE 與
  build_trending.py 的 SIMP，兩邊要一致。
  「我的 App」＋「App 顧問」：規則在 `web/advisor_rules.json`（推薦技能路徑必須存在，`web/check_advisor_rules.py` 檢查），
  部署時複製 `.github/apps.json` 成 `apps.json`。新增 App 就加進 apps.json（可填 `url`：線上網址，X-Ray 優先顯示）。
  `.claude/`、`.agents/` 等隱藏資料夾裡的 SKILL.md 是裝進來的技能，不算技能包。
  Token 可勾「記在這支手機」存 localStorage（使用者要求；只建議唯讀 Token，畫面只顯示末 4 碼，只送 api.github.com）。
- 2026-10 新增（函式名稱）：複製一律用 `copyText`，失敗會開手動複製面板 `#copyfb`，並回傳有沒有成功（要記「複製過」的地方先判斷）。
  「只問這段」是 `askBtn`／`askPrompt`（資料來自 `S.mapData`、`S.adv.hits`）。`pickWant` 同一 weight 內規則輪流取、高 weight 組先輪完。
  敏感檔名 `sensitiveWhy`（只看路徑）；自動產生資料夾 `GEN_DIRS`／`isGenDir`（venv 證據是 skel 的 `venv` 旗標）。
  首次 commit `firstCommit`（只在詳細頁查、最多 2 次 API、快取 1 小時）；限流 `retryDelayFromHeaders`（20 秒內照等，
  x-ratelimit-reset 只在剩餘額度為 0 時採用）。熱門清單照 trending.json 通用顯示：每筆有 `first_seen`／`new`（`new === true` 才顯示 🆕），
  `breakout` 清單另有 `stars_per_day`／`gain`；fixtures 用 `trendingJson`、`apiOverride` 模擬。
- 「🌐 有線上成品」：`quickLive`（只看清單本身的 `homepage`／`has_pages`，指回 github.com 或本機位址的不算；不打 API），
  開關存 `xray-live`；清單列的「開成品」是 `span[data-live]`（列是 `<button>`，捕捉階段攔下來 `window.open`）。
  trending.json 要有這兩個欄位（`build_trending.py` 的 `KEEP`，空的不寫）。CSS 已有 `.live`（詳細頁的線上網址），別再用這個 class 名。
