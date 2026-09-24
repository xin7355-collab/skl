---
name: auto-video-edit
description: 全自動影片剪輯（只用 ffmpeg、不需付費 API）：剪掉沒講話的空白、自動挑精華剪成指定長度、輸出直式 9:16 短影音、音量標準化。可在對話裡直接剪，也可以設定成「手機上傳影片到 GitHub 草稿 Release → 自動剪好傳回來」。使用時機：使用者說「幫我剪影片」「去掉空白」「剪成 60 秒精華」「做成 Reels／Shorts」「影片自動剪輯」。
---

# 全自動影片剪輯

## 模式

| 模式 | 做什麼 | 適合 |
|---|---|---|
| `auto`（預設） | 有講話且空白多 → 去空白；否則 → 精華 | 不確定時 |
| `jumpcut` | 剪掉安靜的段落（前後留 0.15 秒不切到字） | 講解、Vlog、會議錄影 |
| `highlight` | 依鏡頭切換＋聲音熱鬧程度挑片段，湊到 `--target` 秒 | 活動、旅遊、長片濃縮 |
| `short` | 精華＋直式 1080×1920、最長 58 秒 | Reels、Shorts、TikTok |

所有模式都會做音量標準化（-16 LUFS），並逐段轉檔再串接（長片也不會吃光記憶體）。

## 在對話裡直接剪

```bash
python3 scripts/video_auto_edit.py plan 影片.mp4 --mode auto          # 先看會保留哪些片段
python3 scripts/video_auto_edit.py process 影片.mp4 成品.mp4 --mode short --target 45
```
需要 ffmpeg（GitHub Actions 已內建；沒有時工具會說明怎麼裝）。

## 手機全自動（GitHub Actions）

1. repo 放 `assets/video-auto-edit.yml` 到 `.github/workflows/`（skl 已經裝好）。
2. 手機瀏覽器開 `https://github.com/<帳號>/<repo>/releases/new`：
   - 標籤填 `video-日期`（要以 **video** 開頭），附上影片（單檔上限 2 GB）。
   - 內文可寫選項（可省略）：`模式: short`、`長度: 45`、`直式`。
   - 按 **Save draft（存成草稿）**——草稿只有你看得到，公開 repo 也不會外流。
3. 每 30 分鐘自動檢查一次；剪好的 `原檔名-edited-模式.mp4` 會出現在同一個草稿裡，點了就能下載。
   急的話到 Actions 頁按 **Run workflow** 立刻跑。
4. 失敗會留下 `.error1.txt` 說明原因，下一輪自動重試，最多 3 次；刪掉 .error 檔可再重試。

## 自我測試

```bash
python3 scripts/video_auto_edit.py selftest     # 有 ffmpeg 時會實際產生測試影片剪一次
python3 scripts/video_release_runner.py selftest
```

## 限制（誠實說明）

- 「精華」是用鏡頭切換與音量判斷，不懂畫面內容；要依內容挑（例如「只要有貓的片段」）需要影像辨識模型。
- 不會自動上字幕：語音辨識需要另外的模型或付費 API，要加的話先提醒使用者設預算上限。
