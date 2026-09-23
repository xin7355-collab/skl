---
name: app-bootstrap
description: 把 skl 技能庫裡挑好的技能組合（core／pwa／actions／backend／security）與 CLAUDE.md 工作守則，一鍵複製進另一個 App 的 repo，讓那個 App 之後每次開 Claude Code 工作階段都自動有這些技能。使用時機：使用者說「幫這個 App 裝技能」「把 skl 套到這個專案」「初始化新專案」「更新這個 repo 的技能」。
---

# 把技能庫套用到其他 App

## 為什麼用「複製」

雲端工作階段（claude.ai/code、手機）不一定會自動安裝外部 GitHub 外掛市集的外掛；
但 repo 內的 `.claude/skills/` 每次都會被讀到。所以把技能直接複製進目標 repo 最穩。
代價：技能庫更新後，要在目標 repo 跑一次 `update`。

## 流程（在「目標 App」的工作階段裡做）

1. 取得技能庫（公開 repo，唯讀淺層複製即可）：
   ```bash
   git clone --depth 1 https://github.com/xin7355-collab/skl /tmp/skl
   ```
2. 看有哪些組合，依專案性質挑：
   ```bash
   python3 /tmp/skl/xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py list
   ```
   判斷方式：有 `manifest.json`／service worker → `pwa`；有 `.github/workflows` → `actions`；
   有資料庫或 API 伺服器 → `backend`；處理金鑰、登入、使用者資料 → `security`。`core` 一律裝。
3. 先試跑再安裝：
   ```bash
   python3 /tmp/skl/xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py install --target . --packs core,pwa --dry-run
   python3 /tmp/skl/xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py install --target . --packs core,pwa
   ```
   - 目標已有同名技能且不是本工具裝的 → 預設略過（不覆蓋使用者自己的技能）。
   - **安裝前自動安全掃描**（skill-security-auditor）：FAIL 不安裝、WARN 照裝但列出；
     掃描工具壞掉時一律不裝（確認來源可信才用 `--no-security-scan`）。
   - **開工自動健檢**：加上 SessionStart hook（`.claude/hooks/skl-session-start.sh`），每次開對話先回報
     問題數量，技能超過 14 天沒更新會提醒；合併進既有 `.claude/settings.json`，不覆蓋原設定（`--no-hook` 可關）。
   - 目標沒有 `CLAUDE.md` → 建立工作守則範本；已有 → 不覆蓋，提示合併。
4. 若新建了 `CLAUDE.md`：依專案實際情況填第 7 節；不是 PWA + Actions 架構就刪掉第 6 節。
5. 裝完立刻跑一次 `app-guardrails-audit` 做健檢，把結果回報給使用者。
6. commit：`.claude/skills/` 與 `CLAUDE.md`（commit 訊息寫裝了哪些組合、來源 commit）。

只要個別技能（例如網頁上勾選複製來的路徑）時，用 `--skills`，可與 `--packs` 併用：
```bash
python3 /tmp/skl/xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py install --target . --skills engineering/skills/focused-fix,marketing-skill/skills/seo-audit
```

## 更新

```bash
git clone --depth 1 https://github.com/xin7355-collab/skl /tmp/skl
python3 /tmp/skl/xin-toolkit/skills/app-bootstrap/scripts/bootstrap_app.py update --target .
```
只更新 `.claude/skills/.skl-vendor.json` 記錄的技能，不動 CLAUDE.md。

## 自我測試

```bash
python3 scripts/bootstrap_app.py selftest
```
