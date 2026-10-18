---
paths:
  - "web/index.html"
  - "web/build_catalog.py"
  - "web/zh.json"
  - "web/categories.json"
---

# 技能挑選網頁（web/index.html）

（從 CLAUDE.md 搬來：只有讀或改到上面這些檔案時才載入，其他工作不用每次都帶著。）

- `web/index.html`：技能挑選網頁（GitHub Pages，`pages.yml` 部署）；`web/build_catalog.py` 產生
  `catalog.json`（部署時才產生，不進 git）；`web/zh.json`：每個技能的中文說明（只給網頁顯示用）；
  `web/categories.json`：技能用途分類規則（由上而下比對，可用 overrides 指定）。「健檢更新」的「該裝哪些技能」
  （`advicePrompt`，只建議不安裝；App 清單讀部署時複製的 `apps.json`，用原生 `<select>`，不要用 `<datalist>`——iPhone Safari 常選不到；可一鍵開 X-Ray 顧問）。
- 「下載 ZIP」：`web/build_zips.py`（selftest 在 CI）部署時把技能打包到 `_site/zips/`，並在 catalog.json 寫 `zip` 欄位；
  卡片只對有這個欄位的技能顯示連結。ZIP 裡超過 200 字元的說明會縮短（claude.ai 規定），原檔不動。
