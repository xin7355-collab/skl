---
name: app-guardrails-audit
description: 依「低資源 App 防禦準則」審計一個專案——記憶體（OOM）、SQLite WAL 與鎖、外部 API 限流與指數退避、金鑰外洩、iOS 分享檔案、PWA 版本號、網頁 noindex、GitHub Actions 逾時與 tee。使用時機：使用者說「幫我審計／健檢這個 App」「上線前檢查」「會不會當機／被封鎖／OOM」「檢查有沒有違反 CLAUDE.md 規則」，或剛改完涉及資料庫、爬蟲、上傳、工作流的程式。
---

# App 防禦準則審計

在 1GB RAM 免費主機、iPhone Safari、GitHub Actions 免費額度這類嚴苛環境下，
找出「平常測不出來、上線後才爆」的寫法，並給出可以直接照做的修法。

## 流程

1. **先跑靜態掃描**（離線、只用標準函式庫、逐行讀檔不吃記憶體）：
   ```bash
   python3 .claude/skills/app-guardrails-audit/scripts/guardrails_audit.py audit .
   ```
   外掛模式安裝時，腳本在外掛目錄的 `skills/app-guardrails-audit/scripts/` 下；找不到就用
   `find / -name guardrails_audit.py 2>/dev/null | head -1`。
   要給程式讀就加 `--json`。結束碼 1 代表有「必修」。
2. **逐條人工複查**：掃描是啟發式的，每一條都要打開原始碼確認是真問題，
   誤報就說明為什麼是誤報；真的是刻意寫法，在該行加註解 `guardrails: ignore`。
3. **補掃描抓不到的項目**（要讀程式判斷）：
   - 大量資料是否分批（chunk）或串流處理，而不是整批載入陣列。
   - SQLite：寫入是否有重試；長交易是否擋住讀取；連線是否重複開關。
   - 外部請求：是否尊重 `Retry-After`、有沒有全域限流（佇列、每秒上限）。
   - GitHub Actions：長工作是否「做完一個存一個進度」；發佈步驟有沒有 `if: always()`；
     「抓下來」和「處理成功」是否分開記錄、失敗會自動重試。
   - 錯誤處理：全部失敗時不交出空檔案；「還沒設定」與「真的壞掉」分得清楚。
4. **回報格式**：依嚴重度分三級（🔴 必修／🟡 建議修／🔵 提醒），每條寫
   「檔案:行號 → 問題 → 會在什麼情境爆 → 怎麼修」。最後分開列出「驗證過的」與「沒驗到的」。
5. 使用者要求修正時，一次修一類問題，修完重跑掃描確認該條消失。

## 自我測試

```bash
python3 scripts/guardrails_audit.py selftest
```

## 規則一覽

| 規則 | 等級 | 抓什麼 |
|---|---|---|
| secret-in-url | 必修 | 網址參數帶 key/token |
| hardcoded-secret | 必修 | 寫死的真金鑰格式 |
| ios-share-title | 必修 | `navigator.share` 同時帶 files 與 title |
| log-secret | 建議 | 把 token/secret 印到 log |
| unbounded-promise-all / unbounded-gather | 建議 | 沒有併發上限的批次請求 |
| file-to-arraybuffer | 建議 | 前端把整個檔案讀進記憶體 |
| sqlite-no-wal / sqlite-no-timeout | 建議 | SQLite 沒 WAL、沒等鎖逾時 |
| hardcoded-model | 建議 | 寫死 AI 模型名稱 |
| html-no-noindex | 建議 | 網頁缺 noindex |
| workflow-no-timeout / workflow-tee-no-mkdir | 建議 | Actions 沒逾時、tee 前沒 mkdir |
| http-no-retry / pandas-no-chunks / pwa-no-version | 提醒 | 沒重試、pandas 整檔讀、PWA 沒版本號 |
