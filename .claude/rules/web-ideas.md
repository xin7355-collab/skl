---
paths:
  - "web/ideas.html"
  - "web/ideas.json"
  - "web/check_ideas.py"
  - "web/e2e/ideas.spec.js"
---

# App 點子頁（web/ideas.html）

- 2026-10-31 使用者要「可以做的程式推薦，還有要如何做」。資料在 `web/ideas.json`：每個點子 id／類別／難度（小中大）／一句話／
  為什麼值得做／技術／第一版（mvp）／之後加（next）／要注意（notes）／技能組合（packs，必須是 packs.json 的名字）／費用（text＋src，
  寫到免費、付費、金額就一定要有官方出處，出處鍵在 `srcs`）／X-Ray 參考搜尋字（xq）。改完跑 `python3 web/check_ideas.py`（CI 也跑）。
- 費用數字沿用 `dev-guide.html` 已查證的那幾條（Pages、Actions、Workers）；新增別的服務要先查官方頁面，查不到就不要寫金額。
- 「📋 複製開工指令」`startPrompt`：叫新 repo 的 Claude 用 app-bootstrap 裝組合（跟首頁同一行 FETCH_CMD／TOOL），先做第一版。
- 篩選存 `ideas-ui-v1`。首頁頂端是兩欄工具卡 `.tools`（使用者不要一行一行的文字連結），新增頁面時一起加一張卡。
