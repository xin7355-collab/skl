#!/usr/bin/env python3
"""video_auto_edit.py — 全自動影片剪輯（只需要 ffmpeg，不用任何付費 API）。

模式：
  jumpcut   剪掉沒講話的空白（講解、Vlog、會議錄影）
  highlight 自動挑精華：鏡頭切換＋聲音最熱鬧的片段，剪成指定長度
  short     精華＋直式 9:16（Reels／Shorts／TikTok）
  auto      有聲音且空白夠多 → jumpcut；否則 → highlight（預設）
所有模式都會把音量標準化（loudnorm），手機上聽起來不會忽大忽小。

直式的兩種填法（--fill）：
  crop  放大到蓋滿 1080x1920 再裁掉多的（預設；任何比例都行，橫片左右會被裁）
  blur  原畫面完整縮放置中，背景是同一畫面放大、模糊、壓暗（橫片不會被裁掉內容）

為什麼一段一段輸出再串接：一次把整部影片丟進濾鏡鏈，長影片在 1GB RAM 主機會爆記憶體；
逐段轉檔＋concat 只需要固定記憶體，也能看到進度。
為什麼中間檔用 .mkv＋無損 PCM 音訊、最後才編一次 AAC：每段各自編 AAC 時，每段開頭都有
編碼器的前置靜音（priming），串接後每個接縫會掉出 10～20 毫秒的空白（實測量得到），聽起來像小爆音。
音量標準化仍是每段各做一次：實測同一部片前後音量差 20 dB 時，只在最後做一次 loudnorm
會讓各段差到 20 LU 以上；逐段做則各段都落在 -16 LUFS。

用法：
  python3 video_auto_edit.py process in.mp4 out.mp4 [--mode auto] [--target 60] [--vertical] [--fill blur]
                                                    [--preview 縮圖.jpg]
  python3 video_auto_edit.py plan in.mp4 [--mode auto]      # 只列出會保留哪些片段，不輸出
  python3 video_auto_edit.py selftest

找 ffmpeg 的順序：環境變數 FFMPEG → PATH 上的 ffmpeg。
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")
OUT_W, OUT_H = 1080, 1920  # 直式輸出尺寸
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


class BudgetExceeded(RuntimeError):
    """超過呼叫端給的時間預算。和一般失敗分開，讓 runner 知道重試也沒用（影片太長），不要再重剪。"""


def ffmpeg_bin():
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("找不到 ffmpeg。GitHub Actions 上已內建；本機請安裝 ffmpeg，或設定環境變數 FFMPEG 指到執行檔。")
    return exe


def _run(args, deadline=None):
    # deadline 是 time.monotonic() 的時間點：ffmpeg 跑到一半超時會被 subprocess 砍掉，
    # 讓 runner 有時間留下 .error 說明，而不是整個 Actions job 被砍、什麼都沒留下。
    timeout = None
    if deadline is not None:
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise BudgetExceeded("時間預算已用完，還沒開始這一步就停下。")
    try:
        return subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostdin", *args],
                              capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BudgetExceeded("處理超過時間預算，已停止。") from None


def ffmpeg_reason(stderr):
    """ffmpeg 失敗時最後一行通常只是「Conversion failed!」，看不出原因；挑真正說明錯誤的那一行。"""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    generic = re.compile(r"conversion failed|error while filtering|failed to inject frame|error reinitializing", re.I)
    for ln in lines:
        if re.search(r"error|invalid|no such|not found|unsupported|cannot|failed", ln, re.I) and not generic.search(ln):
            return ln
    return lines[-1] if lines else "未知原因"


# ---------- 解析（純函式，離線可測） ----------

def parse_probe(stderr):
    """從 `ffmpeg -i` 的輸出取長度、是否有聲音、畫面尺寸（不依賴 ffprobe）。"""
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    v = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", stderr)
    return {"duration": dur, "has_audio": "Audio:" in stderr,
            "width": int(v.group(1)) if v else None, "height": int(v.group(2)) if v else None}


def parse_silence(stderr, duration):
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", stderr)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration  # 結尾一直安靜時沒有 silence_end
        out.append((max(0.0, s), e))
    return out


def keep_segments(duration, silences, pad=0.15, min_keep=0.4):
    """空白以外的片段；前後各留 pad 秒避免字被切掉，太短的碎片丟掉。"""
    keep, cur = [], 0.0
    for s, e in sorted(silences):
        if s - cur > 0:
            keep.append((max(0.0, cur - pad), min(duration, s + pad)))
        cur = max(cur, e)
    if duration - cur > 0:
        keep.append((max(0.0, cur - pad), duration))
    merged = []
    for s, e in keep:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return [(round(s, 3), round(e, 3)) for s, e in merged if e - s >= min_keep]


def parse_scenes(stderr):
    return sorted({round(float(x), 3) for x in re.findall(r"pts_time:([\d.]+)", stderr)})


def parse_loudness(stderr):
    """ebur128 每 0.1 秒一筆「瞬間響度 M」；回傳 [(秒, LUFS)]。"""
    return [(float(t), float(m)) for t, m in re.findall(r"\bt:\s*([\d.]+)\s+TARGET:.*?\bM:\s*(-?[\d.]+)", stderr)]


def pick_highlights(duration, scenes, loud, target=60.0, clip=4.0):
    """以鏡頭切換點（沒有就每 clip 秒）為起點，算每個候選片段的平均響度，
    挑最熱鬧、不重疊的片段湊滿 target 秒，再依時間排序。"""
    if duration <= target:
        return [(0.0, round(duration, 3))]
    starts = sorted(set([round(s, 3) for s in scenes if s < duration - 1] +
                        [round(i * clip, 3) for i in range(int(duration // clip))]))
    def score(s):
        vals = [m for t, m in loud if s <= t < s + clip]
        base = sum(vals) / len(vals) if vals else -70.0
        return base + (3.0 if s in scenes else 0.0)  # 剛好在鏡頭切換點的片段通常比較完整
    chosen, total = [], 0.0
    for s in sorted(starts, key=score, reverse=True):
        e = min(duration, s + clip)
        if any(not (e <= a or s >= b) for a, b in chosen):
            continue
        chosen.append((s, e))
        total += e - s
        if total >= target:
            break
    return sorted((round(a, 3), round(b, 3)) for a, b in chosen)


def video_filter(vertical=False, fill="crop"):
    """回傳 -vf 濾鏡字串。直式一律先「放大到蓋滿」再裁：舊寫法 scale=-2:1920 只對齊高度，
    遇到比 9:16 更窄的影片（例如 iPhone 螢幕錄影 1170x2532 → 887x1920）寬度不夠 1080，crop 直接失敗。"""
    if not vertical:
        return "scale='min(1920,iw)':-2"
    if fill == "blur":
        # 模糊背景的做法參考 rehan-remade/universal-modder 的 um/video.py `_fill_filter`
        # （MIT License，Copyright (c) 2026 Rehan and universal-modder contributors）。
        # 背景先縮到 1/4 再模糊、再放大：在小圖上模糊省很多運算，放大後本來就要糊，看不出差別。
        bw, bh = OUT_W // 4, OUT_H // 4
        return (f"split=2[bg0][fg0];"
                f"[bg0]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
                f"boxblur=12:2,eq=brightness=-0.12:saturation=1.1,scale={OUT_W}:{OUT_H}[bg];"
                f"[fg0]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease:force_divisible_by=2[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    return f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},setsar=1"


# 估時用的係數（秒處理時間／秒影片，以 1080p 為基準）。在 4 核心機器上實測 1080p 30fps：
# 精華模式的分析（鏡頭＋響度偵測）0.15、去空白只聽聲音幾乎是 0；轉檔 0.88、直式模糊背景 1.06。
# 各乘約 1.3 留餘裕（runner 可能更慢、手機 HEVC 片源更難解）。不乘太多：估太高會把剪得完的片判成「太長」；
# 估太低也沒關係，還有 deadline 兜底，不會被整個 job 砍掉。
EST_ANALYZE, EST_RENDER, EST_OVERHEAD = 0.2, 1.2, 30.0


def estimate_seconds(info, mode="auto", target=60.0):
    """依長度與解析度估計要處理幾秒。highlight／short 只轉 target 秒，但分析一定要掃完整部片。"""
    dur = info.get("duration") or 0.0
    px = (info.get("width") or 1920) * (info.get("height") or 1080)
    factor = max(0.25, px / (1920 * 1080))  # 4K 片解碼約是 1080p 的 4 倍
    out_sec = min(dur, target) if mode in ("highlight", "short") else dur
    return EST_OVERHEAD + dur * EST_ANALYZE * factor + out_sec * EST_RENDER * max(1.0, factor)


def fmt_time(sec):
    sec = int(sec)
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def preview_points(segments, max_tiles=9):
    """縮圖總覽要截哪幾格：回傳 [(成品裡的秒數, 第幾段, 原片開始, 原片結束)]。
    片段不多就每段一格；太多就平均挑 max_tiles 段，讓使用者一眼看到頭、中、尾各保留了什麼。"""
    if not segments:
        return []
    offsets, t = [], 0.0
    for s, e in segments:
        offsets.append(t)
        t += e - s
    n = len(segments)
    idx = list(range(n)) if n <= max_tiles else sorted({round(j * (n - 1) / (max_tiles - 1)) for j in range(max_tiles)})
    out = []
    for i in idx:
        s, e = segments[i]
        # 取段內偏前、但不是第一格的畫面：第一格常是轉場或黑畫面。
        at = min(offsets[i] + min((e - s) / 2, 1.0), max(0.0, t - 0.1))
        out.append((round(at, 3), i + 1, s, e))
    return out


def _drawtext_escape(text):
    # drawtext 的 text 參數裡冒號、反斜線、單引號都有特殊意義。
    return text.replace("\\", "\\\\").replace("'", "\u2019").replace(":", "\\:").replace("%", "\\%")


# ---------- 分析與輸出（需要 ffmpeg） ----------

def probe(path, deadline=None):
    return parse_probe(_run(["-i", path], deadline).stderr)


def analyze(path, mode="auto", target=60.0, silence_db=-35, min_silence=0.6, deadline=None):
    info = probe(path, deadline)
    if not info["duration"]:
        raise ValueError("讀不到影片長度，檔案可能損壞或不是影片。")
    dur = info["duration"]
    used = mode
    if mode in ("auto", "jumpcut") and info["has_audio"]:
        r = _run(["-i", path, "-vn", "-af", f"silencedetect=noise={silence_db}dB:d={min_silence}", "-f", "null", "-"],
                 deadline)
        sil = parse_silence(r.stderr, dur)
        silent = sum(e - s for s, e in sil)
        if mode == "jumpcut" or silent / dur >= 0.05:
            return info, "jumpcut", keep_segments(dur, sil)
        used = "highlight"
    elif mode == "jumpcut":
        raise ValueError("這部影片沒有聲音，無法剪空白；請改用 highlight 或 short 模式。")
    elif mode == "auto":
        used = "highlight"
    # highlight / short：縮小畫面再偵測鏡頭切換，速度快很多。
    r = _run(["-i", path, "-an", "-vf", "scale=320:-2,select='gt(scene,0.35)',showinfo", "-f", "null", "-"], deadline)
    scenes = parse_scenes(r.stderr)
    loud = []
    if info["has_audio"]:
        r = _run(["-i", path, "-vn", "-af", "ebur128", "-f", "null", "-"], deadline)
        loud = parse_loudness(r.stderr)
    return info, used, pick_highlights(dur, scenes, loud, target=target)


def render(path, segments, out, vertical=False, has_audio=True, crf=23, fill="crop", deadline=None, log=print):
    if not segments:
        raise ValueError("沒有可保留的片段（整部都是空白？）。可調低 --silence-db 或改用 highlight。")
    vf = video_filter(vertical, fill)
    with tempfile.TemporaryDirectory(prefix="vae-") as tmp:
        parts = []
        for i, (s, e) in enumerate(segments):
            # 中間檔用 mkv＋PCM（無損）：原因見檔頭。PCM 一小時約 690MB 暫存，Actions 的磁碟放得下。
            part = os.path.join(tmp, f"p{i:04d}.mkv")
            args = ["-y", "-ss", f"{s:.3f}", "-to", f"{e:.3f}", "-i", path]
            # 每段都用同樣的音訊格式，concat 時才不會對不上；沒聲音的影片補一段靜音軌。
            if has_audio:
                args += ["-map", "0:v:0", "-map", "0:a:0", "-af", LOUDNORM]
            else:
                args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
            args += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
                     "-r", "30", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]
            r = _run(args + [part], deadline)
            if r.returncode != 0 or not os.path.exists(part):
                raise RuntimeError(f"第 {i + 1} 段轉檔失敗：{ffmpeg_reason(r.stderr)}")
            parts.append(part)
            log(f"  片段 {i + 1}/{len(segments)} 完成（{s:.1f}–{e:.1f} 秒）")
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p in parts:
                f.write(f"file '{p}'\n")
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        # 畫面直接複製（已經是 H.264），聲音整條接好後才編一次 AAC，接縫不會有編碼器補的空白。
        r = _run(["-y", "-f", "concat", "-safe", "0", "-i", lst, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                  "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", out], deadline)
        if r.returncode != 0:
            raise RuntimeError("串接失敗：" + ffmpeg_reason(r.stderr))


def make_preview(video, segments, out_jpg, cols=3, max_tiles=9, deadline=None):
    """成品的格狀縮圖（預設 3x3），每格標「第幾段＋原片時間」，手機上看一張圖就知道留了哪些段。
    每格用輸入端 -ss 快速跳到該時間只解一格，而不是 select 濾鏡掃整部片：長片也只要幾秒。
    字型找不到（drawtext 失敗）時退回不標字的版本，總比沒有縮圖好。回傳是否有標時間。"""
    pts = preview_points(segments, max_tiles)
    if not pts:
        raise ValueError("沒有片段可做縮圖。")
    info = probe(video, deadline)
    tw, th = (270, 480) if (info["height"] or 0) > (info["width"] or 0) else (480, 270)
    cols = min(cols, len(pts))
    rows = math.ceil(len(pts) / cols)
    inputs = []
    for at, *_ in pts:
        inputs += ["-ss", f"{at:.3f}", "-t", "1", "-i", video]
    for labeled in (True, False):
        chains = []
        for k, (_, n, s, e) in enumerate(pts):
            f = (f"[{k}:v]trim=end_frame=1,setpts=PTS-STARTPTS,"
                 f"scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2,setsar=1")
            if labeled:
                txt = _drawtext_escape(f"#{n}  {fmt_time(s)}-{fmt_time(e)}")
                f += (f",drawtext=text='{txt}':fontcolor=white:fontsize={max(16, tw // 15)}:"
                      f"box=1:boxcolor=black@0.6:boxborderw=6:x=10:y=h-th-12")
            chains.append(f + f"[t{k}]")
        graph = ";".join(chains) + ";" + "".join(f"[t{k}]" for k in range(len(pts))) + \
            f"concat=n={len(pts)}:v=1:a=0,tile={cols}x{rows}:padding=6:margin=6:color=0x202020[out]"
        r = _run(["-y", *inputs, "-filter_complex", graph, "-map", "[out]", "-frames:v", "1", "-q:v", "3", out_jpg],
                 deadline)
        if r.returncode == 0 and os.path.exists(out_jpg):
            return labeled
    raise RuntimeError("縮圖失敗：" + ffmpeg_reason(r.stderr))


def process(path, out, mode="auto", target=60.0, vertical=False, silence_db=-35, min_silence=0.6, fill="crop",
            preview=None, deadline=None, log=print):
    if mode == "short":
        vertical, base_mode = True, "highlight"
        target = min(target, 58.0)  # Shorts 上限 60 秒，留一點緩衝
    else:
        base_mode = mode
    info, used, segs = analyze(path, base_mode, target, silence_db, min_silence, deadline)
    log(f"原片 {info['duration']:.1f} 秒，使用 {used} 模式，保留 {len(segs)} 段、共 {sum(e - s for s, e in segs):.1f} 秒")
    render(path, segs, out, vertical=vertical, has_audio=info["has_audio"], fill=fill, deadline=deadline, log=log)
    res = probe(out, deadline)
    result = {"input_seconds": round(info["duration"], 2), "output_seconds": round(res["duration"] or 0, 2),
              "mode": used + ("+vertical" if vertical else "") + ("+blur" if vertical and fill == "blur" else ""),
              "segments": len(segs)}
    if preview:
        # 縮圖只是附加資訊：失敗不影響成品，但要講出來，不默默少一個檔。
        try:
            result["preview_labeled"] = make_preview(out, segs, preview, deadline=deadline)
        except (RuntimeError, ValueError) as e:
            result["preview_error"] = str(e)
            log(f"  縮圖沒做成（成品不受影響）：{e}")
    return result


# ---------- 自我測試 ----------

def _segment_loudness(path, start, length):
    """量一段的整合響度（LUFS），自我測試用。"""
    r = _run(["-ss", f"{start}", "-t", f"{length}", "-i", path, "-vn", "-af", "ebur128", "-f", "null", "-"])
    vals = re.findall(r"I:\s*(-?[\d.]+) LUFS", r.stderr)
    return float(vals[-1]) if vals else None


def _dropouts(path, window=480, floor=-40.0):
    """每 10 毫秒（480 取樣）算一次音量，回傳低於 floor dB 的時間點：連續正弦波不該有任何一格掉下去。"""
    r = _run(["-i", path, "-vn", "-af",
              f"asetnsamples={window},astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
              "-f", "null", "-"])
    rows = re.findall(r"pts_time:([\d.]+)\s*\n.*?RMS_level=(-?[\w.]+)", r.stderr)
    return [float(t) for t, v in rows if v in ("-inf", "inf", "nan") or float(v) < floor]


def selftest():
    ok = True

    def check(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    p = parse_probe("  Duration: 00:01:02.50, start: 0\n Stream #0:0: Video: h264, yuv420p, 1920x1080\n Stream #0:1: Audio: aac")
    check(p == {"duration": 62.5, "has_audio": True, "width": 1920, "height": 1080}, "解析長度／尺寸／有無聲音")
    sil = parse_silence("silence_start: 2.0\nsilence_end: 4.0 | silence_duration: 2\nsilence_start: 9.5\n", 10.0)
    check(sil == [(2.0, 4.0), (9.5, 10.0)], "解析空白（結尾沒有 silence_end 也能處理）")
    check(keep_segments(10.0, sil) == [(0.0, 2.15), (3.85, 9.65)], "保留片段含前後緩衝")
    check(keep_segments(10.0, [(0.0, 10.0)]) == [], "整段空白時不保留")
    check(keep_segments(10.0, [(2.0, 2.2)]) == [(0.0, 10.0)], "很短的空白會被合併，不切碎")
    check(parse_scenes("pts_time:3.2 x\npts_time:7.9 y\npts_time:3.2") == [3.2, 7.9], "解析鏡頭切換點並去重複")
    loud = parse_loudness("t: 0.1 TARGET:-23 LUFS M: -30.0 S:-30\nt: 4.2 TARGET:-23 LUFS M: -10.5 S:-20")
    check(loud == [(0.1, -30.0), (4.2, -10.5)], "解析瞬間響度")
    h = pick_highlights(40.0, [8.0], [(t / 10, -10.0 if 80 <= t < 120 else -40.0) for t in range(400)], target=8, clip=4)
    check((8.0, 12.0) in h and sum(e - s for s, e in h) >= 8, "精華優先挑最熱鬧、在鏡頭切換點的片段")
    check(all(h[i][1] <= h[i + 1][0] for i in range(len(h) - 1)), "精華片段不重疊且依時間排序")
    check(pick_highlights(30.0, [], [], target=60) == [(0.0, 30.0)], "影片比目標短時整部保留")
    check("force_original_aspect_ratio=increase" in video_filter(True) and "-2:1920" not in video_filter(True),
          "直式先放大蓋滿再裁（不再只對齊高度）")
    check("boxblur" in video_filter(True, "blur") and "decrease" in video_filter(True, "blur"), "模糊背景：前景完整縮放＋背景模糊")
    check(ffmpeg_reason("Stream #0\n[crop] Invalid too big size\nError reinitializing filters!\nConversion failed!")
          == "[crop] Invalid too big size", "錯誤訊息挑出真正原因，不是只有 Conversion failed")
    one_hour = estimate_seconds({"duration": 3600, "width": 1920, "height": 1080}, "jumpcut")
    check(3600 < one_hour < 2 * 3600, f"估時：1 小時 1080p 去空白約 {one_hour / 60:.0f} 分鐘")
    check(estimate_seconds({"duration": 3600, "width": 1920, "height": 1080}, "short", 58) < one_hour / 3,
          "估時：精華／短影音只轉目標長度，比去空白快很多")
    check(estimate_seconds({"duration": 600, "width": 3840, "height": 2160}, "jumpcut") >
          3 * estimate_seconds({"duration": 600, "width": 1920, "height": 1080}, "jumpcut") * 0.9, "估時：4K 比 1080p 慢約 4 倍")
    pp = preview_points([(0, 2), (5, 9), (20, 21)])
    check([(n, s) for _, n, s, _ in pp] == [(1, 0), (2, 5), (3, 20)] and pp[1][0] == 3.0, "縮圖：片段少時每段一格、時間對應成品")
    many = preview_points([(i * 10, i * 10 + 2) for i in range(30)])
    check(len(many) == 9 and many[0][1] == 1 and many[-1][1] == 30, "縮圖：片段多時平均挑 9 段（含頭尾）")
    check(fmt_time(3725) == "1:02:05" and fmt_time(65) == "1:05", "時間格式")
    check(_drawtext_escape("#1 0:05") == "#1 0\\:05", "縮圖文字的冒號會跳脫")

    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        print("SKIP 找不到 ffmpeg，略過實際轉檔測試（CI 與 GitHub Actions 上會跑）")
    else:
        quiet = lambda *_: None  # noqa: E731
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in.mp4")
            # 8 秒測試片：2～5 秒完全安靜，其餘有 440Hz 聲音；第 4 秒換畫面。
            r = _run(["-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=4",
                      "-f", "lavfi", "-i", "smptebars=size=640x360:rate=30:duration=4",
                      "-f", "lavfi", "-i", "aevalsrc='if(between(t,2,5),0,0.5*sin(2*PI*440*t))':s=48000:d=8",
                      "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
                      "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", src])
            check(r.returncode == 0, "產生測試影片")
            out = os.path.join(d, "o1.mp4")
            jpg = os.path.join(d, "o1-preview.jpg")
            res = process(src, out, mode="auto", preview=jpg, log=quiet)
            check(res["mode"] == "jumpcut" and 4.5 <= res["output_seconds"] <= 6.0,
                  f"auto 模式剪掉 3 秒空白（輸出 {res['output_seconds']} 秒）")
            pv = probe(jpg) if os.path.exists(jpg) else {}
            check("preview_error" not in res and pv.get("width") and pv.get("width") > pv.get("height", 0),
                  f"產生縮圖總覽（{pv.get('width')}x{pv.get('height')}，標時間：{res.get('preview_labeled')}）")
            out2 = os.path.join(d, "o2.mp4")
            res2 = process(src, out2, mode="short", target=4, log=quiet)
            info2 = probe(out2)
            check((info2["width"], info2["height"]) == (1080, 1920) and res2["output_seconds"] <= 6,
                  "short 模式輸出直式 1080x1920 且長度受控")

            # iPhone 螢幕錄影比例（1170x2532，比 9:16 更窄）：舊寫法 crop 會失敗。
            tall = os.path.join(d, "tall.mp4")
            _run(["-y", "-f", "lavfi", "-i", "testsrc=size=1170x2532:rate=30:duration=3", "-f", "lavfi", "-i",
                  "sine=f=440:d=3", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", tall])
            for fill in ("crop", "blur"):
                o = os.path.join(d, f"tall-{fill}.mp4")
                try:
                    process(tall, o, mode="highlight", vertical=True, fill=fill, log=quiet)
                    ti = probe(o)
                    check((ti["width"], ti["height"]) == (1080, 1920), f"比 9:16 更窄的影片直式（{fill}）輸出 1080x1920")
                except RuntimeError as e:
                    check(False, f"比 9:16 更窄的影片直式（{fill}）：{e}")
            # 橫片＋模糊背景：中間是完整原畫面、上下是模糊背景（亮度比純黑高，也比原畫面暗）。
            ob = os.path.join(d, "wide-blur.mp4")
            res_b = process(src, ob, mode="highlight", target=3, vertical=True, fill="blur", log=quiet)
            ib = probe(ob)
            check((ib["width"], ib["height"]) == (1080, 1920) and res_b["mode"].endswith("+blur"),
                  "橫片模糊背景輸出 1080x1920")

            # 音量與接縫：連續正弦波、每 3 秒換一次音量（0.5／0.05／0.2／0.5，前後差 20 dB），各取 2 秒接起來。
            tone = os.path.join(d, "tone.mp4")
            _run(["-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=12", "-f", "lavfi", "-i",
                  "aevalsrc='(if(lt(t,3),0.5,if(lt(t,6),0.05,if(lt(t,9),0.2,0.5))))*sin(2*PI*440*t)':s=48000:d=12",
                  "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "192k", "-shortest", tone])
            ot = os.path.join(d, "tone-out.mp4")
            render(tone, [(0.5, 2.5), (3.5, 5.5), (6.5, 8.5), (9.5, 11.5)], ot, log=quiet)
            levels = [_segment_loudness(ot, i * 2 + 0.2, 1.6) for i in range(4)]
            check(all(v is not None for v in levels) and max(levels) - min(levels) <= 1.0,
                  f"串接後各段音量一致（{levels} LUFS，差距 ≤ 1 LU）")
            gaps = _dropouts(ot)
            check(gaps == [], f"串接處沒有空白或掉音（10 毫秒格低於 -40 dB：{gaps}）")

            mute = os.path.join(d, "mute.mp4")
            _run(["-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=6", "-c:v", "libx264",
                  "-preset", "ultrafast", mute])
            res3 = process(mute, os.path.join(d, "o3.mp4"), mode="auto", target=3, log=quiet)
            check(res3["mode"] == "highlight" and res3["output_seconds"] > 0, "沒有聲音的影片自動改用 highlight")
            try:
                process(mute, os.path.join(d, "o4.mp4"), mode="jumpcut", log=quiet)
                check(False, "沒聲音卻要求 jumpcut 應提示改模式")
            except ValueError as e:
                check("改用" in str(e), "沒聲音卻要求 jumpcut 會說明該怎麼做")
            try:
                process(src, os.path.join(d, "o5.mp4"), mode="auto", deadline=time.monotonic() + 0.05, log=quiet)
                check(False, "時間預算用完應停下")
            except BudgetExceeded as e:
                check("預算" in str(e), "超過時間預算時停下並說明（不會一直跑到被砍）")

    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="全自動影片剪輯")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("process", "plan"):
        p = sub.add_parser(name)
        p.add_argument("input")
        if name == "process":
            p.add_argument("output")
            p.add_argument("--preview", help="另外輸出格狀縮圖總覽（jpg）")
        p.add_argument("--mode", default="auto", choices=["auto", "jumpcut", "highlight", "short"])
        p.add_argument("--target", type=float, default=60.0, help="highlight／short 的目標秒數")
        p.add_argument("--vertical", action="store_true", help="輸出直式 9:16")
        p.add_argument("--fill", default="crop", choices=["crop", "blur"], help="直式的填法：裁切或模糊背景")
        p.add_argument("--silence-db", type=float, default=-35, help="低於此音量視為空白（dB）")
        p.add_argument("--min-silence", type=float, default=0.6, help="空白至少幾秒才剪")
    sub.add_parser("selftest")
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    if args.cmd not in ("process", "plan"):
        ap.print_help()
        return 2
    if not os.path.isfile(args.input):
        print(f"找不到影片：{args.input}", file=sys.stderr)
        return 2
    try:
        if args.cmd == "plan":
            info, used, segs = analyze(args.input, "highlight" if args.mode == "short" else args.mode,
                                       args.target, args.silence_db, args.min_silence)
            print(json.dumps({"duration": info["duration"], "mode": used, "segments": segs,
                              "estimated_seconds": round(estimate_seconds(info, args.mode, args.target))},
                             ensure_ascii=False))
            return 0
        res = process(args.input, args.output, args.mode, args.target, args.vertical, args.silence_db, args.min_silence,
                      fill=args.fill, preview=args.preview)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError) as e:
        print(f"剪輯失敗：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
