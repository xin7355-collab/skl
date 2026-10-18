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

所有模式都會做音量標準化（每段 -16 LUFS），並逐段轉檔再串接（長片也不會吃光記憶體）；
中間檔用無損音訊、最後才編一次 AAC，接縫不會有小空白。

### 直式的兩種填法

| 選項 | 做什麼 | 適合 |
|---|---|---|
| `--fill crop`（預設） | 放大到蓋滿 1080×1920 再裁掉多的；任何比例都行（含 iPhone 螢幕錄影這種比 9:16 更窄的） | 本來就是直的、主角在中間 |
| `--fill blur` | 原畫面完整縮放置中，背景是同一畫面放大、模糊、壓暗 | 橫拍的片要轉直式、不想被裁掉內容 |

## 在對話裡直接剪

```bash
python3 scripts/video_auto_edit.py plan 影片.mp4 --mode auto          # 先看會保留哪些片段
python3 scripts/video_auto_edit.py process 影片.mp4 成品.mp4 --mode short --target 45
python3 scripts/video_auto_edit.py process 橫拍.mp4 成品.mp4 --vertical --fill blur --preview 縮圖.jpg
```
需要 ffmpeg（工作流會自動確認並安裝；本機沒有時工具會說明怎麼裝）。
`plan` 的輸出含 `estimated_seconds`（估計要剪幾秒），長片先看一下。

## 手機全自動（GitHub Actions）

1. repo 放 `assets/video-auto-edit.yml` 到 `.github/workflows/`（skl 已經裝好）。
2. 手機瀏覽器開 `https://github.com/<帳號>/<repo>/releases/new`：
   - 標籤填 `video-日期`（要以 **video** 開頭），附上影片（單檔上限 2 GB）。
   - 內文可寫選項（可省略，每行一個）：`模式: short`、`長度: 45`、`直式`、`直式: 模糊背景`
     （英文也行：`mode: short`、`target: 45`、`vertical`、`vertical: blur`）。
   - 按 **Save draft（存成草稿）**——草稿只有你看得到，公開 repo 也不會外流。
3. 每 30 分鐘自動檢查一次；剪好的 `原檔名-edited-模式.mp4` 會出現在同一個草稿裡，點了就能下載。
   另有 `原檔名-preview.jpg`：3×3 格狀縮圖，每格標「第幾段＋原片時間」，在手機上看一張圖就知道留了哪幾段。
   急的話到 Actions 頁按 **Run workflow** 立刻跑。
4. 開始處理時會先放一個 `原檔名.start1.txt`。失敗會留下 `原檔名.error1.txt` 說明原因，下一輪自動重試；
   被中斷（例如逾時）只會留下 start 檔，也算一次。最多 3 次，還沒試過的影片會排在重試的前面，
   一部壞片不會卡住後面的影片。要再重試：刪掉這部影片的 `.start*` 與 `.error*` 檔。
5. 時間上限：每輪最多處理 135 分鐘（工作流 `--minutes 135`，job 上限 150 分鐘）。依影片長度與解析度估計
   剪不完的，直接留 `原檔名.error1-too-long.txt`、不開始剪、也不自動重試——請分段上傳或改用 `short`／`highlight`
   （只轉精華，快很多）。實測 4 核心機器上 1080p 轉檔約 0.9 倍片長，去空白模式大約能處理 1.5 小時以內的 1080p 片；
   4K 約是 4 倍時間。

## 自我測試

```bash
python3 scripts/video_auto_edit.py selftest     # 有 ffmpeg 時會實際產生測試影片剪一次
python3 scripts/video_release_runner.py selftest
```

## 限制（誠實說明）

- 「精華」是用鏡頭切換與音量判斷，不懂畫面內容；要依內容挑（例如「只要有貓的片段」）需要影像辨識模型。
- 不會自動上字幕：語音辨識需要另外的模型或付費 API，要加的話先提醒使用者設預算上限。
