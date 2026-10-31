---
paths:
  - "web/dev-guide.html"
  - "web/agent.html"
  - "web/guide/**"
  - "web/check_guides.py"
  - "web/e2e/guide.spec.js"
  - "web/e2e/agent.spec.js"
---

# 教學頁（App 開發全流程、AI Agent 製作流程）

（從 CLAUDE.md 搬來：只有讀或改到上面這些檔案時才載入，其他工作不用每次都帶著。）

- `web/dev-guide.html`（App 開發全流程）、`web/agent.html`（AI Agent 製作流程）：兩頁同一套結構——路線圖（可點的流程圖）、
  教學（每一步：目標／在哪裡／怎麼做／要貼的／會看到／完成標準／卡住怎麼辦，可打勾）、起手式（一題一題的精靈）、範例（完整案例＋範例庫＋我的 App）、
  比較、注意、技術、參考。共用程式在 `web/guide/guide.js`＋`guide.css`（部署時複製到 `_site/guide/`，網址帶 `?v=__APP_VER__`）；
  題目、範例、參考資源是頁面裡的 `<script type="application/json" id="wiz-def|templates|refs">`，改了要跑 `python3 web/check_guides.py`
  （範例答案要對得上題目、分類與種類要在清單裡、頁面內連結要存在）。範例至少 25 個；參考的開源專案用 `xray.html?repo=` 開，要先確認 repo 存在。
  費用與規定**一律附官方出處**、頁首寫查證日；出處清單由頁面上的 `a.src` 自動整理，只能靠搜尋確認的標 ※。更新內容要重新查證並改查證日。
  「裝完接著做」規則 `FOLLOW_UP` 在 guide.js，跟 index.html 一字不差（E2E 比對）。舊版存檔（`guide-gen`／`agent-gen`）由各頁 `migrate()` 轉成精靈答案，
  不認得的值回到預設。存檔鍵：`guide-*-v2`／`agent-*-v2`、進度 `guide-done-v1`／`agent-done-v1`、Agent 做法 `agent-way`。
  範例與案例只用公開 App（StockAI-DB、PriceHub）的架構，不寫工作相關 App 的內容。
  dev-guide 的選項用到的規定（LINE、推播、地圖等）出處放「注意」的「功能的眉角」。
  agent 依 `pickWay` 由簡到繁建議做法（Cowork → Routine → GitHub Actions → 自己寫 API），教學只顯示選的做法的步驟；B 段裝組合 `ai`＋`agent`。
- 網址帶 `#st-…`／`#case-…`／`#tpl-…`（範例卡 id）／`#tab-…` 會切分頁並打開（超強搜尋連過來用）；改 id 規則要一起改 search.html 的教學索引。
