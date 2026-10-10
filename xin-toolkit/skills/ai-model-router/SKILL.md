---
name: ai-model-router
description: 讓 App 呼叫 AI 時自動挑模型、模型下架自動換、難的工作自動升級，不再寫死模型名稱（Groq、OpenRouter、Gemini、Claude）。附 Python（GitHub Actions／後端）與 JavaScript（網頁／Cloudflare Worker）兩個可直接複製的模組，以及「什麼情況用哪個等級」的規則。使用時機：App 程式裡有呼叫 AI API、寫死了 gemini-1.5-flash／llama-3.1-8b-instant／claude-sonnet-5 之類的模型名稱、AI 功能突然全部 404、想省 AI 費用、使用者問「該用哪個模型」「能不能自動切換模型」。
---

# AI 模型自動切換

## 為什麼

- 模型名稱寫死，供應商一下架就整批 404，而且畫面只顯示「API 錯誤 404」。實際案例：Groq 下架
  `llama-3.1-8b-instant` 後，翻譯、情緒判讀、垃圾過濾同時壞掉；`gemini-1.5-flash` 已被 Google 停用。
- 每件工作難度不同：翻一行標題用最便宜的模型就夠，股票分析才值得用大模型。全部用大模型浪費錢與額度，
  全部用小模型品質不夠。

## 三個等級（App 裡只寫等級，不寫模型名稱）

| 等級 | 什麼工作 | Groq | Gemini | Claude |
|---|---|---|---|---|
| `fast` | 翻譯、摘要、分類、抽欄位、判斷是不是垃圾、大量小工作 | 最新 8b | 最新 flash-lite／flash | 最新 Haiku |
| `smart` | 分析、比較、推理、寫一段像樣的文字 | 最新 70b | 最新 flash | 最新 Sonnet |
| `best` | 最難、最長、要寫程式或完整報告 | gpt-oss-120b／最新大模型 | 最新 pro | 最新 Opus |
| `auto` | 依內容自動判斷（翻譯→fast、分析→smart、程式→best、很長→升級） | | | |

原則：**先用便宜的，不合格再升級**。呼叫時傳 `validate`（例如「要能解析成 JSON」），不合格會自動換更高一級重試。

## 怎麼接進 App

1. 複製模組到 App：
   - Python（GitHub Actions、後端腳本）：`scripts/model_router.py`
   - 網頁、Cloudflare Worker、Node：`assets/model_router.js`
2. 把所有寫死的模型名稱換成等級：

```python
from model_router import Router
r = Router("groq")                      # 金鑰讀 GROQ_API_KEY（可用逗號放多把）
title = r.chat("fast", [{"role": "user", "content": "翻成繁體中文：" + en}])
report = r.chat("smart", msgs, validate=lambda s: s.strip().startswith("{"))
```

```js
const r = new ModelRouter("gemini", { keys: [env.GEMINI_API_KEY] });
const text = await r.chat("auto", [{ role: "user", content: prompt }], { validate: (s) => s.includes("{") });
```

3. 金鑰放 repo Secrets／Worker 環境變數／使用者自己裝置，**只走 HTTP 標頭**（模組已處理），不放網址。
4. 要暫時指定某個模型：Python 設環境變數 `MODEL_ROUTER_<供應商>_<等級>`（例如 `MODEL_ROUTER_GEMINI_FAST`），
   JS 傳 `{ override: { fast: "..." } }`。
5. 跑 `python3 model_router.py selftest` 與 `node model_router.js selftest`；有金鑰時可跑
   `python3 model_router.py list groq` 看現在各等級會挑到誰。

## 它會自動處理的事

- 官方 `/models` 清單每 6 小時重抓一次；非聊天模型（語音、嵌入、圖片）與預覽版會排在後面或濾掉。
- 404／`model_decommissioned` → 清快取、避開那個模型、換下一個再打。
- 429／5xx／網路斷線 → 指數退避 2、4、8 秒（尊重 Retry-After）；401／403 → 換下一把金鑰。
- 全部失敗 → 丟出中文錯誤，列出試過哪些模型、可以怎麼做；**絕不印出金鑰**。

## 遷移檢查清單（改舊 App 時照做）

- [ ] 全專案搜尋寫死的模型名稱：`gemini-`、`llama-`、`gpt-`、`claude-`、`deepseek`、`qwen`。
- [ ] 每個呼叫點決定等級（大部分是 `fast`），換成 `chat(等級, ...)`。
- [ ] 需要固定格式（JSON）的地方加 `validate`。
- [ ] 失敗時畫面要說使用者能做什麼（例如「AI 額度用完，稍後再試」），不是只顯示 HTTP 代碼。
- [ ] 用到付費 API（Claude、Gemini 付費層）時提醒使用者到供應商後台設**預算上限**。
- [ ] 已經有自己一套自動挑模型的 App（例如 StockAI-DB 的 groq_common.py）不用硬換，規則一致即可。

## 在 Claude Code 裡（寫程式時）用哪個模型

- 預設的 **Opus 5.5** 是官方推薦的主力，大部分工作用它就好。
- 最難、最長的任務（大重構、找不到原因的 bug）→ `/model fable`。
- 小改動、問答、想快一點 → `/model sonnet`；最簡單的工作 → `/model haiku`。
- 半自動：`/model opusplan` 會在**規劃階段用 Opus、動手寫時自動換 Sonnet**。
- 子代理（subagent）可以在定義裡寫 `model: haiku`，簡單的子任務就自動用便宜的模型。
- 主模型忙線時自動換備援：設定 `fallbackModel`（例如 `["claude-sonnet-5-5", "claude-haiku-5-5"]`）。
- Claude Code 目前**沒有完全自動挑模型的模式**；上面幾種是官方提供的自動切換方式。
