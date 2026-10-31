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
- 2026-10-31 搜尋（依 `web/xray_search_probe.py` 在 Actions 實查 GitHub，改組字規則前先重跑 `X-Ray search probe` 工作流）：
  空格 = 全部都要、OR = 有一個就算（跨概念用 OR 會放寬到上百萬筆）、括號不分組、**「-字」不會排除，要寫 NOT**（`-language:x` 這種條件才認 `-`）、
  6 個 OR 就 422（5 個 OR + 1 個 NOT 可以）。所以：`parseQ` 拆出詞／NOT／條件；一般模式 `plainQuery` 只把 -字 換 NOT；
  模糊模式 `fzPlan`：好幾個詞先查「全部都要」（中文換英文代表字），總數 < `FZ_MIN` 才放寬成 OR 再查一次（2 次額度、合併去重）；
  純數字（年份）不放進 OR；排除的字不加同義詞；回來後 `hasNot` 在手機上再擋一次。字典 `SYN` 沒有的中文詞用 `toEnglishTerms`
  （Google 免費翻譯 tl=en，快取 `xray-q2en-v1`，被限流就照原詞搜）。
- 描述語言篩選 `#rlang`（`descLang`／`DLANGS`／`langOk`，存 `xray-dlang`）：只看搜尋結果的描述，假名 → 日文、諺文 → 韓文、
  中文用 `SIMP_RE` 判簡繁（只有共用字分不出來 → 歸繁中）。
- 搜尋列：輸入框 16px（iPhone 小於 16px 會自動放大整頁）、「開始」至少 46px 高；`.bar` 有 `scroll-margin-top`，
  加上 `#url` focus 後檢查一次，避免被置頂分頁列 `.tabs` 蓋住（實測以前上緣 4 成被蓋）。
- 🤖 每天自動健檢：`web/build_fleet.mjs` 在 `Deploy web page`（ubuntu-24.04，因為 Playwright 1.56.1 不支援 26.04）用無頭瀏覽器開
  `_site/xray.html`、直接呼叫 `analyzeLite`（⛔ 不另寫一份），寫 `fleet.json`。⛔ 只放數量（分數、待補強幾項、建議裝幾個、已裝幾個），
  公開網站不放問題內容；上一份從線上網站拿來算「比上次多／少幾項」。私人 App 要 `SKL_READ_TOKEN`，沒設定 → skipped（不算失敗）；
  空 repo（409）→ empty。失敗不擋部署，`fleet-check` 亮紅燈。X-Ray `#autofleet`／`paintAutoFleet`，超過 3 天沒更新要說。
- 🔔 追蹤搜尋：`xray-watch-v1`（存手機，⛔ 不放公開 repo）。`checkWatches` 打開時查、間隔 `WATCH_EVERY`，查詢加 `created:>=`（上次前一天），
  新的記 `fresh`；`openWatch` 只列新的並標 🆕、看過歸零。搜尋與追蹤共用 `planFor`。
