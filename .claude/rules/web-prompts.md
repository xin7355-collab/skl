---
paths:
  - "web/prompts.html"
  - "web/prompts/**"
  - "web/build_prompts.py"
  - "web/e2e/prompts.spec.js"
  - ".github/workflows/prompts-sync.yml"
---

# 提示詞庫（web/prompts.html）

- 資料來自 prompts.chat（f/awesome-chatgpt-prompts，提示詞 CC0、程式 MIT）。使用者原本看到的 PromptGenius 是 GPL，
  而且它的範例資料庫含真實使用者帳號，不要收它的任何檔案。
- `web/build_prompts.py build`：用 `git ls-remote` 找 commit（不打 GitHub API）→ 下載 prompts.csv → 過濾（超過 1.2 萬字、
  越獄 `HARD`、成人 `SOFT`；`SOFT` 前面有否定語不算，否則負面提示詞清單、彩妝 nude 色號會被誤殺）→ 分類（`CATS`，標題 3 分、內文 1 分）
  → 翻標題（沿用 index.json 已翻的；Google 限流就停手）→ 寫 `web/prompts/index.json`＋32 片 `p-XX.json`（編號前兩碼決定片）。
  改分類或過濾規則要補 selftest，並用真資料看被擋下的清單（build 會印出標題）。
- 雲端工作階段的 IP 很容易被 Google 翻譯擋（429）：標題翻譯交給 `Prompts sync` 工作流（每月 3 日＋手動），它 commit 回 main
  後用 `gh workflow run pages.yml` 重新部署。網頁對沒有中文的標題會在手機上翻畫面上那一頁（`fillTitles`，快取 `prompts-ztr-v1`）。
- 網頁：送到 AI 的網址格式取自 prompts.chat 的 run-prompt-button.tsx（`AI` 陣列；沒有 `q` 的是先複製再開，網址超過 `URL_MAX` 也改成先複製）。
  iPhone 只准在點擊當下寫剪貼簿、開新分頁：所以 AI 按鈕是 `<a href>`，複製只在全文已載入時同步做。
  localStorage：`prompts-fav-v1`、`prompts-use-v1`、`prompts-ui-v1`、`prompts-tr-v1`（內文翻譯，最多 40 則）、`prompts-ztr-v1`。
  填空 `${名稱:預設}`；翻成中文時先換成 ⟦n⟧ 保護再換回。返回手勢先關面板（history.pushState）。
