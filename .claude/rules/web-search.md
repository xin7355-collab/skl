---
paths:
  - "web/search.html"
  - "web/e2e/search.spec.js"
---

# 超強搜尋（web/search.html）

- 2026-10-31 使用者要「超強搜尋，如股票就幫我搜尋相關提示詞等等」。一個框搜五類，全部讀本站檔案（⛔ 不連外、不花額度）：
  提示詞 `prompts/index.json`、技能 `catalog.json`、點子 `ideas.json`、AI 功能 `ai/features.json`、教學（抓 `dev-guide.html`／`agent.html`
  用 DOMParser 取 `details[id]`、沒 id 的 `.card`（連到 `#tab-xxx`）、範例庫 JSON `#templates`（連到 `#tpl-xxx`））。
  每份各自載入，壞掉的那份在結果裡說「載入失敗、不代表沒有」，其他類照常搜；全部失敗才顯示 `#loadErr`。GitHub 交給 X-Ray（`xray.html?q=…&fz=1`）。
- 同義詞 `SYN_SRC`：每行「核心詞｜相關詞」，底線 = 空格。原字 1、核心詞 0.8、相關詞 0.4；打的只是相關詞時整組都當相關（不然「投資」會把股票專用的字排前面）。
  英文字整字比對加常見字尾（`SUFFIX`，免得 invest 對到 investigation、ai 對到 said）；中文找子字串，所以別放容易誤中的短詞（「家計」中了「搬家計劃」）。
  中文沒空格用 `splitCjk` 依字典切詞；「提示詞／技能／點子／教學」（`META`）只指定類別、不拿去比對；`-字` 排除；英文打錯先對字典（`guessTypo`，OSA 距離）。
- 排序三層（`searchAll`）：直接符合（每個詞都靠原字或核心詞）→ 只靠相關詞（標「相關」）→ 只對到部分的詞（標「部分符合」）；前一層夠多就不往下補。
  欄位權重：標題 10、短說明 5～6、分類 4～5、內文 1～3。改字典後用真資料試「股票、爬蟲、影片剪輯、履歷、stokc」看前 5 名合不合理。
- 各頁的深層連結（改那幾頁時別弄壞，E2E 有測）：`index.html?q=` → 瀏覽技能分頁並清掉類別篩選；`ideas.html#id` → 打開那個點子（被篩選藏住先切回全部）；
  guide.js 讀 `#st-…`／`#case-…`／`#tpl-…`／`#tab-…`（`setTimeout` 等範例庫畫完）；`ai.html?tab=claude&f=功能名` → 標 `.feat.hit`；
  `xray.html?fz=1` → 這次開模糊搜尋，不寫回 `xray-fuzzy`。
- 最近搜過 `search-recent-v1`（最多 8 筆，只存手機）。首頁頂端的搜尋列 `form.gsearch` 送到這頁。
