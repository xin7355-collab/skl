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
- 範圍外的問題簡短提出，不擅自擴大範圍。
- 不留死碼；改名、搬檔後全專案搜一次舊名稱的殘留參照。

## 6. 本專案資訊
- 用途：個人的 Claude Code 技能庫（fork 自開源 claude-skills，MIT），加上自製外掛
  `xin-toolkit/`，用來讓其他 App 的 repo 一鍵取得技能與工作守則。
- 主要檔案：
  - `xin-toolkit/skills/app-guardrails-audit/scripts/guardrails_audit.py`：低資源防禦準則掃描。
  - `xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py`：把技能複製進其他 repo。
  - `xin-toolkit/skills/app-bootstrap/assets/packs.json`：技能組合清單（改這裡增減技能）。
  - `xin-toolkit/skills/app-bootstrap/assets/CLAUDE.template.md`：給其他 App 的工作守則範本
    （含 PWA + Actions 章節）。改守則時兩邊都要改。
  - `.claude-plugin/marketplace.json`：外掛市集清單；`docs/upstream/`：上游原始 README／CLAUDE.md。
- 需要的 Secrets：無。
- 部署在哪：不部署網站；GitHub Actions 只跑 `CI Quality Gate`（push 到 main 時）。
- 特別注意：
  - 上游約 388 個技能照原樣保留，除非必要不改，方便日後同步上游。
  - 新增技能後跑：`python3 scripts/check_frontmatter.py --all`、`python3 scripts/check_paths.py --all`、
    兩支工具的 `selftest`。
  - 不要把上游的 `.claude/settings.json`（會自動載入第三方外掛）或 `.mcp.json` 加回來。
