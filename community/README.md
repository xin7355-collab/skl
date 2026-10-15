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
| `zh-tw-humanizer` | https://github.com/acchuang/zh-tw-humanizer | 963f124（v1.8.0） | 收 `SKILL.md`、離線檢查腳本 `scripts/check.py`、`LICENSE`。**移除** `scripts/verify_semantic.py`：會把原文與改寫稿前 4000 字送到第三方商業服務 api.typesafe.ai（需付費金鑰）。不收 evals、網站、README。安全掃描 PASS。 |
| `tw-writing` | https://github.com/kirinlin/tw-writing | d49a58f | 收 `SKILL.md`、`references/`、`LICENSE`。不收作者的開發工具紀錄 `.agentflow/`（掃描的 PROMPT-EXFIL 誤判出自這裡，是作者給審查 AI 的限制說明）與 README。 |
| `voice-profile-extraction` | https://github.com/Coolkidlab-Yin/voice-profile-extraction（`skills/voice-profile-extraction`） | e8eeb9c | 原樣。安全掃描 PASS。 |
| `lit-review` | https://github.com/Zachariah9420/lit-review-skill | b0db1e0 | 收 `SKILL.md`、`references/`、`scripts/lit_api.py`、`scripts/cite_integrity.py`、`LICENSE`。不收 evals（SKILL.md 提到的迴歸測試是給維護者用的，所以不在這裡）、圖片、範例、打包腳本。安全掃描 FAIL 為誤判：金鑰只送到對應的學術 API。注意它會讀目前資料夾與家目錄的 `.env`。選用金鑰 `S2_API_KEY`（Semantic Scholar，免費申請）。 |
| `academic-writing-discipline` | https://github.com/wayhong0928/mis-thesis-skills（`plugins/thesis-toolkit/skills/academic-writing-discipline`） | ba4f62c | 只收這一個技能與 `LICENSE`。不收 19MB 的評測紀錄與每個技能的 `evals/`（SKILL.md 引用的 evals 是作者的評測證據）；另外 3 個是論文專用技能，不收。安全掃描 PASS。 |
| `design-taste-frontend` | https://github.com/Leonxlnx/taste-skill（`skills/taste-skill`） | f624f37 | 不收 evals 與 README；同 repo 其他 12 個技能（舊版、實驗版、Codex 生圖、品牌包等）互相重疊，不收。安全掃描 FAIL 為誤判（DevTools 協定在本機無頭 Chrome 裡執行自己的檢查、spawn 只開 Chrome）。素材搜尋只連公開圖庫 API；`draw.mjs` 會寫 `~/.taste-skill/history.json`；範例會直接引用 picsum.photos 圖片，放上 GitHub Pages 前請改用 `--get` 下載到專案。 |
| `redesign-existing-projects` | 同上（`skills/redesign-skill`） | f624f37 | 原樣。安全掃描 PASS。 |
| `ponytail` | https://github.com/DietrichGebert/ponytail（`skills/ponytail`） | 84740e3 | 只收核心技能與 `LICENSE`。不收 hooks（會寫 `~/.claude` 並提議改 `settings.json` 的狀態列）、benchmarks、各平台安裝檔，以及 review／audit／debt／gain／help 子技能。它主張「最小改動、小改動不一定要測試」，跟 superpowers 的 test-driven-development 有取捨，需要時手動叫用。安全掃描 PASS。 |
| `wireframe`、`qa-acceptance` | https://github.com/paperclipai/paperclip（`packages/skills-catalog/catalog/bundled/`） | 5ec93da | 只從這個 179MB 的「AI 員工管理 App」挑出 2 個不依賴它伺服器的通用技能，原樣收錄（frontmatter 的 `key:` 是原 App 的目錄代號，不影響使用）。其他 77 個技能要接 Paperclip 伺服器或是維護者流程，不收；App 本身有匿名使用統計。安全掃描 PASS。 |
| `statistical-analysis`、`statsmodels`、`polars`、`markitdown` | https://github.com/K-Dense-AI/scientific-agent-skills（`skills/`） | a1043ef | 177 個技能裡約 90% 是生物化學，只挑這 4 個資料分析與文件轉換用的（不需要金鑰）。**修改**：刪掉每份 SKILL.md 結尾的「Citing Scientific Agent Skills」段落（它要求在你的報告與程式裡附上作者論文引用）。frontmatter 的 `license:` 是被介紹的套件本身的授權。markitdown 的掃描 FAIL 為誤判（命中的是「小心文件裡藏的注入指令」安全說明）；其餘 PASS。 |

## 評估過但沒有收錄（2026-10-14）

| 專案 | 原因 |
|---|---|
| Raymondhou0917/speak-human-tw | 品質好，但跟 zh-tw-humanizer 幾乎重疊（兩個都會在「去 AI 味」時被叫用，互相搶）；而且每次都要先列修改再等你確認。 |
| aeopress/writing-skills.TW、techtipstts-tech/de-ai-feel、shyuan/writing-humanizer、allenloves/de-ai-tone | 同類去 AI 味技能，功能被 zh-tw-humanizer 涵蓋；de-ai-tone 是作者個人風格（CC BY-SA 4.0），而且描述寫成任何中文寫作都會觸發。 |
| KyrieHuang/humanizer_zh、dolphinsue319/kd-humanizer | 大多是英文 humanizer 的翻譯，台灣在地化不足（例如寫「質量評分」）。 |
| whatsphone-creator/humanizer-hk | 香港書面語／粵語版，不適合台灣用語。 |
| rainday/smart-blog-skills | 沒有 LICENSE 檔、沒寫著作權人，而且大量改寫自另一個 MIT 專案卻沒保留對方的授權聲明；部分技能指向不存在的腳本，還附會自動觸發的 hook。 |
| mashbean/research-publishing-pipeline、zz465213/ai-novel-creator、wolfgangyu/content-writer | 沒有授權（預設保留所有權利，不能收）。 |
| FW1201/tw-research-skills | 26 個都是很短的規則清單，多數需要統計套件；對寫報告與部落格幫助有限。 |
| FW1201/tw-stu-skills | 給國高中學生與老師用（會考、學習歷程），不符合用途。 |
| Bookie0918/writer-ai-os | 綁定單一香港創作者的資料夾與流程，腳本會寫到家目錄並呼叫 Telegram。 |
| morluto/rea | 逆向工程工具：技能離不開作者的 MCP 伺服器，安裝用 `curl | bash`、每次用 `npx @latest` 抓最新版；反編譯商業 App 可能違反授權條款；58MB 且含 GPL 元件。 |
| wheresryan22/anatomy | 跟 hairline-create（等角線條插圖）高度重疊；附的投稿腳本會讀你的 Claude 對話紀錄（用時、用量、提示文字）放進公開的 PR。 |
| JuliusBrussee/caveman | 讓回覆極度精簡（類似「穴居人說話」），跟「用白話繁中解釋清楚」相衝突；作者自己的實測相對「請簡潔回答」只省約 3%（舊版宣稱的 65% 已撤回）；中文模式是文言文；另有商業雲端服務推廣與會改 `~/.claude/settings.json` 的安裝器。 |
| K-Dense-AI/scientific-agent-skills 其餘 173 個 | 生物、化學、物理專用，或需要付費金鑰（OpenRouter、Parallel、Exa）。 |
