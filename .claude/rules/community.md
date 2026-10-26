---
paths:
  - "community/**"
---

# 社群技能（community/）

（從 CLAUDE.md 搬來：只有讀或改到上面這些檔案時才載入，其他工作不用每次都帶著。）

- `community/skills/`：使用者在 Repo X-Ray 挑中的社群技能（oil-ui、hairline-create、live-panel、huashu-art-motion、manimgx、archify、ui-ux-pro-max、karpathy-guidelines、answer-me-with-html、constraint-driven-development、source-driven-development、performance-optimization、critique-defense-copilot、humanizer、zh-tw-humanizer、tw-writing、voice-profile-extraction、lit-review、academic-writing-discipline、design-taste-frontend、redesign-existing-projects、ponytail、wireframe、qa-acceptance、statistical-analysis、statsmodels、polars、markitdown、chinese-novelist、short-drama 系列 11 個、dramake、clone-website、sanitize-metadata、compress-video、slice-video、codebase-design、prototype、teach，MIT）；來源、版本與修改
  記在 `community/README.md`（oil-ui 已移除自動更新與推銷；huashu-art-motion 的字型不進 repo，用 fetch_fonts.py 下載驗證；評估過沒收的也列在裡面，避免重複評估）。組合 `visual`、`writing`、`research`、`story`。
- `short-drama-produce` 會呼叫付費 API（OpenAI、火山引擎、MiniMax），模型名稱寫死在上游程式；改它要重審 security-reviewed.json。
- archify 已升到 v3.2（含一頁說明 explainer）；skl 版關掉檢查更新、產出頁面加 noindex，所以上游測試會有已知失敗。
- `community/tests/`：statistical-analysis、statsmodels、polars、markitdown 的離線測試（版本固定在 requirements.txt），
  以及 skl 自寫的隱私影音技能測試 `test_privacy_media.py`（工作流用 apt 裝 ffmpeg、exiftool），
  由 `Community skill tests` 工作流每月跑；放在技能資料夾外，免得 bootstrap 把測試複製到別的 repo。
