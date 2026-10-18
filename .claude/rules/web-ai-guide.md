---
paths:
  - "web/ai.html"
  - "web/ai/**"
---

# AI 模型與功能指南（web/ai.html）

（從 CLAUDE.md 搬來：只有讀或改到上面這些檔案時才載入，其他工作不用每次都帶著。）

- `web/ai.html`＋`web/ai/models.json`、`web/ai/features.json`：AI 模型與功能指南（含 Cowork 分頁：分區對照、範例只放通用情境，不放工作單位等敏感資訊）。資料由每週 Routine 自動
  研究更新（自我進化）；改資料後必須跑 `python3 web/ai/check_ai_data.py`，通過才能推。跑分一律附出處網址。
