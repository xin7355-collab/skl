# 社群精選技能（community）

使用者在 Repo X-Ray 挑中、要求收錄的外部技能。每個技能資料夾保留原作者的 LICENSE（皆為 MIT）。
收錄前都跑過 skill-security-auditor 並逐行人工檢查；下表記錄來源與修改。

| 技能 | 來源 | 版本 | 修改 |
|---|---|---|---|
| `oil-ui` | https://github.com/oil-oil/oil-ui | 2be7aab（v0.16.0） | **移除**載入時自動執行的版本檢查／自動更新（`scripts/check_update.*`：會連到作者伺服器，並可用 `npx` 下載執行作者的 CLI 改寫技能資料夾）與付費版推銷（`scripts/recommend_once.py`、「完整版提示」段落）；frontmatter 拿掉預先核准這些指令的 `allowed-tools`；不收 README 圖片與 tests。 |
| `hairline-create` | https://github.com/lucasmarkes/hairline（`skills/hairline-create`） | bc78224 | 原樣。安全掃描 FAIL 為誤判（正規表達式 `.exec()`、本機 `node --check`；預覽截圖時會 `npm install playwright-core@1` 到快取資料夾）。 |
| `live-panel` | https://github.com/ythx-101/live-panel-skill | 8a70aa2 | 範例只留 `config.json`（不收 7.6MB 的 mp4 與截圖）。安全掃描 FAIL 為誤判（透過 DevTools 管線驅動本機 headless Chrome、呼叫 ffmpeg）。Codex 範例的設計屬原作者 @thedelost，公開使用該範例的成果要保留署名（見 SKILL.md 的 Credit）。 |
| `humanizer` | https://github.com/blader/humanizer | 225a6f3（v3.1.0） | 只收 `SKILL.md` 與 `LICENSE`（不收 Cursor／Codex 外掛設定與 README）。規則以英文寫作為主，中文只有部分適用。安全掃描 PASS。 |
| `huashu-art-motion` | https://github.com/alchaincyf/huashu-art-motion | d861767 | **不收** 27MB 展示圖（含 README 推廣連結與第三方角色 GIF）、示範影片素材、tests 與 README。**字型（16MB、SIL OFL）不進 repo**：要渲染前跑 `scripts/engine/lib/fonts/fetch_fonts.py`，從原作者同一個 commit 下載並用 `release-manifest.json` 的 sha256 逐檔驗證。所以原作者的 `check_release.py` 會回報缺檔，屬正常現象。安全掃描 FAIL 為誤判：base64 是截圖與語音資料、`.exec()` 是正規表達式。唯一外連是預設關閉的火山引擎語音複刻（要使用者自己的金鑰）。說明是簡體中文。 |
