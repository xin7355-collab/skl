---
name: web-to-markdown
description: 把網頁轉成乾淨的 Markdown（去掉導覽列、廣告、腳本），給 AI 讀或存進 App；只用 Python 內建模組，GitHub Actions 免費排程就能跑。是 firecrawl 這類付費／要架伺服器的抓取服務的免費替代（自己寫的，不含 firecrawl 的 AGPL 程式）。遵守 robots.txt、同網站限流、429／5xx 指數退避、串流讀取有大小上限。使用時機：使用者要「把這幾個網頁抓下來給 AI 看」「每天把某些網頁存成文字」「做新聞／公告／文件的收集器」「參考 firecrawl 做自己的爬蟲」。
---

# 網頁轉 Markdown

## 什麼時候用、什麼時候不要用

- **用**：文章、公告、文件頁、部落格、維基這類「打開就看得到文字」的網頁。
- **不要用**：
  - 要登入才看得到的頁面。
  - robots.txt 禁止抓的網站：工具會自動略過並說明。
  - 要執行 JavaScript 才長出內容的網站（SPA）：工具會回報「內容太少」，不會交出空檔。這類網站先找官方 API 或 RSS；真的要抓，就在 Actions 用 Playwright 開瀏覽器。
- 有官方 API 的資料一律走 API。例如台股用 `tw-stock`，不要爬網頁。

## 用法

```bash
python3 scripts/web2md.py fetch https://example.com/post --out post.md
python3 scripts/web2md.py batch urls.txt --out-dir pages/ --delay 2
python3 scripts/web2md.py selftest
```

- 輸出開頭附 `title`、`url`、`fetched_at`，方便 AI 引用出處。
- 批次模式一個一個抓，不併發；同一個網站之間至少隔 `--delay` 秒。
- 部分失敗時照樣存成功的，最後列出失敗的網址與原因。全部失敗才回傳非 0。
- 內容沒變就不改檔，排程不會每天產生空 commit。

## 放進 App（每天自動抓）

1. 把 `scripts/web2md.py` 複製到 App 的 `scripts/web2md.py`。
2. 在 App 根目錄放 `urls.txt`，一行一個網址，`#` 開頭是註解。
3. 把 `assets/web2md.yml` 複製到 App 的 `.github/workflows/web2md.yml`。它每天台灣時間 06:17 執行，也可以手動按，成果存在 `pages/` 並 commit。
4. 網頁要顯示這些內容時，讓 GitHub Pages 的前端讀 `pages/*.md`，或在 Actions 裡另外產生一份 JSON 索引。
5. 要 AI 摘要：在同一個工作流後面接 `ai-model-router`，用 `fast` 等級，模型名稱不要寫死。

## 跟 firecrawl 的差別

| | firecrawl | web-to-markdown |
|---|---|---|
| 架設 | Docker＋Redis＋Postgres＋Playwright，預設資源上限合計約 12GB RAM；或付費雲端 API | 一支 Python 檔，Actions 免費跑 |
| JavaScript 網站 | 會開瀏覽器渲染 | 不支援，會明確回報 |
| 搜尋、整站爬、點按鈕 | 有 | 沒有（只抓清單裡的網址） |
| 授權 | AGPL-3.0（改了拿去提供服務要公開原始碼） | 本技能庫 MIT |

## 驗證

- `selftest` 離線涵蓋以下情況：轉換規則、big5 編碼、robots.txt、404、內容太少、非網頁、503 重試、批次部分失敗、限流間隔、大小上限、內容沒變不改檔。
- 真實網路由 skl 的 `Web to markdown smoke test` 工作流驗證：改到這個技能時自動跑，也可以手動執行。
