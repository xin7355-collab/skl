# 專案工作守則（給 Claude Code）

## 1. 溝通
- 一律用繁體中文（台灣用語）回覆、寫註解、寫 commit 訊息。
- 我主要用 iPhone 操作，沒有電腦。步驟以手機能完成為準；要我去設定頁時直接給網址。
- 回報要誠實：哪些驗過、哪些沒驗到，分開講清楚。不確定的事實先查證再講。
- 我說「只是先問問題」時，只回答，不動手改東西。

## 2. 長期授權：以下不用再問我
- 做完直接合併到 main 並推上去，不需要我同意：
  在指定分支開發 → rebase 到最新 main → fast-forward 合併 → push main → 分支也同步推。
  網路失敗重試最多 4 次（間隔 2/4/8/16 秒）；遠端有新 commit 就 fetch + rebase 再推。
- 部署用的分支（例如 gh-pages）也可以直接推。
- 改工作流、加套件、新增檔案、重構——直接做。
- 不要開 Pull Request，除非我明確要求。

## 3. 連這些也直接做，不用先問
- 刪除：Issue、Release、分支、檔案；force push；改寫 git 歷史。
- 花錢：開付費方案、呼叫付費 API。
- 改公開程度：repo 公開／私人切換、網站權限。
- 做了不可逆的事（刪除、force push、會產生費用的動作），完成後在回覆裡明確列出來。

## 4. 隱私與安全
- 我說過「不要上傳 GitHub」的東西永遠不 commit，做好打包給我下載。
- 絕對不要把金鑰、Token 印出來（log、commit、回覆都不行）。金鑰放 repo Secrets；
  程式用 HTTP 標頭傳，不放網址（網址會出現在錯誤訊息和記錄裡）。
- repo 保持公開（公開 repo 的 Actions 分鐘數無上限；私人免費只有 2000 分鐘／月）。所以：
  - README 精簡，不詳述這個軟體在做什麼；詳細說明放 docs/。
  - 用 GitHub Issue 送的通知，標題和內文只寫數量，不寫內容——公開 Issue 會被搜尋引擎收錄。
  - 網站加 `<meta name="robots" content="noindex, nofollow">`。
- 用到付費 API 時提醒我設預算上限。

## 5. 做事的方式
- 先重現再修：修 bug 前用實際資料或真的瀏覽器（Playwright）重現，修完用同樣方法驗證。
- 新功能附離線可跑的自我測試（例如 `python xxx.py selftest`），涵蓋邊界情況。
- 不要默默失敗：
  - 全部失敗時不交出空檔案；部分失敗要講幾個、哪幾個。
  - 分清楚「還沒設定」（安靜等、不發失敗通知）和「真的壞掉」（紅燈、通知我）。
  - 錯誤訊息要說我能做什麼，不是只丟 HTTP 代碼。
- 註解寫「為什麼」與避開了什麼失敗情境，不重述程式在做什麼。
- commit 訊息寫：問題、根本原因、怎麼修、怎麼驗證。
- 不寫死外部 API 的模型名稱（常改名下架）；能自動挑就自動挑，也能用環境變數指定。
  呼叫 AI 時用 skl 的 `ai-model-router`（只寫等級 fast／smart／best／auto，下架自動換、不合格自動升級）。
- 範圍外的問題簡短提出，不擅自擴大範圍。
- 裝了新技能（app-bootstrap）之後不要停在「裝好了」：先列出執行方案（每個新技能要拿來做什麼、會改哪些檔案、
  技能需要的套件寫進哪個設定檔、怎麼驗證、有沒有費用），列完就直接做，不用等我回覆。需要我提供的東西
  （金鑰、帳號、付款）或本守則沒授權的動作才先問我；我說「只裝不做」時才停。
- 不留死碼；改名、搬檔後全專案搜一次舊名稱的殘留參照。
- CLAUDE.md 每次對話都整份載入，控制在 200 行內：只跟某些檔案有關的細節放 `.claude/rules/`（開頭寫 `paths:`），
  讀或改到那些檔案才載入。

## 6. 本專案資訊
- 用途：個人的 Claude Code 技能庫（fork 自開源 claude-skills，MIT），加上自製外掛
  `xin-toolkit/`，用來讓其他 App 的 repo 一鍵取得技能與工作守則。
- 主要檔案：
  - `xin-toolkit/skills/app-guardrails-audit/scripts/guardrails_audit.py`：低資源防禦準則掃描。
  - `xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py`：把技能複製進其他 repo。
  - `xin-toolkit/skills/app-bootstrap/assets/packs.json`：技能組合清單（改這裡增減技能）。
  - `xin-toolkit/skills/tw-stock/scripts/tw_stock_fetch.py`：台股官方 OpenAPI 快照抓取（存 SQLite）；
    `assets/tw-stock-daily.yml` 是給其他 App 的每日排程範本。真實 API 驗證用 `TW stock API smoke test`
    工作流（手動執行）。
  - `xin-toolkit/skills/app-bootstrap/assets/CLAUDE.template.md`：給其他 App 的工作守則範本
    （含 PWA + Actions 章節）。改守則時兩邊都要改。
  - 網頁（GitHub Pages，`pages.yml` 部署）：`web/index.html` 技能挑選、`web/dev-guide.html`＋`web/agent.html` 兩份教學
    （共用 `web/guide/`）、`web/ai.html` AI 指南、`web/xray.html` Repo X-Ray。各頁的細節規則在 `.claude/rules/`，
    讀或改到那些檔案時才自動載入。共同規則：費用與規定附官方出處；改 `web/ai/` 的資料後必須跑
    `python3 web/ai/check_ai_data.py`、改教學頁的內嵌資料後跑 `python3 web/check_guides.py`，通過才能推。
  - `ops/weekly_report.py`＋`Weekly maintenance report` 工作流：每週一 08:00 健檢 `.github/apps.json` 列的 App、
    比對上游（基準在 `.github/upstream-sync.json`）、檢查中文說明覆蓋率 → 只寫數量的 Issue。
    同步上游後要更新 upstream-sync.json 的 commit。
  - `ops/skill_eval.py`：自製技能「有技能／沒技能」對照測試（`run` 用 `claude -p` 跑、會用掉用量，手動執行；`selftest` 在 CI）。
    結果記在 `docs/skill-evals.md`；改了自製技能想知道有沒有變好就重跑。
  - `xin-toolkit/skills/file-intake`：使用者上傳檔案要「做出來」時的標準流程。
  - `xin-toolkit/skills/ai-model-router`：App 呼叫 AI 的自動挑模型模組（`scripts/model_router.py`、`assets/model_router.js`，
    兩邊規則要一致；selftest 都在 CI 跑）。組合 `ai`。
  - `xin-toolkit/skills/web-to-markdown`：網頁轉 Markdown（firecrawl 的免費替代，純內建模組、robots.txt＋限流、擋內網位址）；
    真實網路驗證用 `Web to markdown smoke test` 工作流（改到技能時自動跑）。組合 `python`。
  - `xin-toolkit/skills/auto-video-edit`：全自動影片剪輯（ffmpeg）；`Video auto edit` 工作流每 30 分鐘處理
    以 video 開頭的「草稿 Release」裡的影片，成品＋3×3 縮圖傳回同一個草稿（草稿不公開）。工作流的 `timeout-minutes`
    和 runner 的 `--minutes` 要一起改（被砍掉也算一次嘗試，靠 `.start` 標記）。
  - `superpowers/`（obra/superpowers，MIT，15 個開發方法論技能）、`ecc/`（affaan-m/ECC 精選 25 個、安全掃描 PASS）：
    使用者上傳的外部技能，原樣收錄、不含對方的 hooks／agents／rules；授權與來源見各自 LICENSE、README。
  - `community/skills/`：使用者在 Repo X-Ray 挑中的社群技能（MIT），組合 `visual`、`writing`、`research`。
    來源、版本、修改與「評估過沒收的」都記在 `community/README.md`——評估新專案前先查，避免重複評估。
  - `web/build_zips.py`：部署時把每個技能打包成 ZIP（給 claude.ai 網頁版上傳）；`scripts/skl_skill_lint.py`：skl 自己的技能格式警告
    （上游的 check_frontmatter／check_paths 不改）。
  - `xin-toolkit/skills/app-bootstrap/assets/security-reviewed.json`：掃描 FAIL 但人工審查為誤判的技能清單，
    以內容指紋（`bootstrap_app.py hash <路徑>`）綁定；技能一改就失效、要重審。不要為了「裝得上」隨便加。
  - `.claude-plugin/marketplace.json`：外掛市集清單；`docs/upstream/`：上游原始 README／CLAUDE.md。
- 需要的 Secrets：無（選用 `SKL_READ_TOKEN`：唯讀 Token，讓每週報告能掃私人 repo）。
- 部署在哪：網頁 https://xin7355-collab.github.io/skl/ （`Deploy web page` 工作流）；
  另有 `CI Quality Gate`。兩者都在 push 到 main 時跑。
- 網頁 E2E 測試：`web/e2e/`（Playwright，`@playwright/test` 版本鎖在 package-lock.json；GitHub API／raw／翻譯全用
  `fixtures.js` 的假資料，沒列到的外部連線會判失敗）。本機：`cd web/e2e && npm ci && npx playwright test`；
  CI 是 `CI Quality Gate` 的 `web-e2e` job。改 `web/*.html` 的行為就補對應測試；假資料要像真的
  （例如檔案樹照 GitHub 的字母排序，否則會藏住 bug）。
- 特別注意：
  - 上游約 388 個技能照原樣保留，除非必要不改，方便日後同步上游。
  - 技能本體（SKILL.md）維持英文，不翻譯；中文只放 `web/zh.json`。新增技能後要補 zh.json
    （`python3 web/build_catalog.py --missing` 會列出缺的）。
  - 新增技能後跑：`python3 scripts/check_frontmatter.py --all`、`python3 scripts/check_paths.py --all`、
    兩支工具的 `selftest`。
  - 不要把上游的 `.claude/settings.json`（會自動載入第三方外掛）、`.mcp.json`、`.claude/commands/`（上游維護者自己的指令，
    會開 PR、刪分支，跟本守則衝突，已刪除）加回來；同步上游時也要排除。
  - `.claude/skills/` 是用 app-bootstrap 從本 repo 複製給「skl 自己的工作階段」用的 11 個技能（清單在
    `.claude/skills/.skl-vendor.json`）；原始檔改了之後跑
    `python3 xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py update --target . --no-hook` 同步。
