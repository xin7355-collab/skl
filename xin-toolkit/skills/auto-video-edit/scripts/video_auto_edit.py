#!/usr/bin/env python3
"""video_auto_edit.py — 全自動影片剪輯（只需要 ffmpeg，不用任何付費 API）。

模式：
  jumpcut   剪掉沒講話的空白（講解、Vlog、會議錄影）
  highlight 自動挑精華：鏡頭切換＋聲音最熱鬧的片段，剪成指定長度
  short     精華＋直式 9:16（Reels／Shorts／TikTok）
  auto      有聲音且空白夠多 → jumpcut；否則 → highlight（預設）
所有模式都會把音量標準化（loudnorm），手機上聽起來不會忽大忽小。

為什麼一段一段輸出再串接：一次把整部影片丟進濾鏡鏈，長影片在 1GB RAM 主機會爆記憶體；
逐段轉檔＋concat 只需要固定記憶體，也能看到進度。

用法：
  python3 video_auto_edit.py process in.mp4 out.mp4 [--mode auto] [--target 60] [--vertical]
  python3 video_auto_edit.py plan in.mp4 [--mode auto]      # 只列出會保留哪些片段，不輸出
  python3 video_auto_edit.py selftest

找 ffmpeg 的順序：環境變數 FFMPEG → PATH 上的 ffmpeg。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")


def ffmpeg_bin():
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("找不到 ffmpeg。GitHub Actions 上已內建；本機請安裝 ffmpeg，或設定環境變數 FFMPEG 指到執行檔。")
    return exe


def _run(args, timeout=None):
    return subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostdin", *args],
                          capture_output=True, text=True, errors="replace", timeout=timeout)


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


# ---------- 分析與輸出（需要 ffmpeg） ----------

def probe(path):
    return parse_probe(_run(["-i", path]).stderr)


def analyze(path, mode="auto", target=60.0, silence_db=-35, min_silence=0.6):
    info = probe(path)
    if not info["duration"]:
        raise ValueError("讀不到影片長度，檔案可能損壞或不是影片。")
    dur = info["duration"]
    used = mode
    if mode in ("auto", "jumpcut") and info["has_audio"]:
        r = _run(["-i", path, "-vn", "-af", f"silencedetect=noise={silence_db}dB:d={min_silence}", "-f", "null", "-"])
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
    r = _run(["-i", path, "-an", "-vf", "scale=320:-2,select='gt(scene,0.35)',showinfo", "-f", "null", "-"])
    scenes = parse_scenes(r.stderr)
    loud = []
    if info["has_audio"]:
        r = _run(["-i", path, "-vn", "-af", "ebur128", "-f", "null", "-"])
        loud = parse_loudness(r.stderr)
    return info, used, pick_highlights(dur, scenes, loud, target=target)


def render(path, segments, out, vertical=False, has_audio=True, crf=23, log=print):
    if not segments:
        raise ValueError("沒有可保留的片段（整部都是空白？）。可調低 --silence-db 或改用 highlight。")
    vf = "scale=-2:1920,crop=1080:1920" if vertical else "scale='min(1920,iw)':-2"
    with tempfile.TemporaryDirectory(prefix="vae-") as tmp:
        parts = []
        for i, (s, e) in enumerate(segments):
            part = os.path.join(tmp, f"p{i:04d}.mp4")
            args = ["-y", "-ss", f"{s:.3f}", "-to", f"{e:.3f}", "-i", path]
            # 每段都用同樣的音訊格式，concat 時才不會對不上；沒聲音的影片補一段靜音軌。
            if has_audio:
                args += ["-map", "0:v:0", "-map", "0:a:0", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
            else:
                args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
            args += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
                     "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
            r = _run(args + [part])
            if r.returncode != 0 or not os.path.exists(part):
                raise RuntimeError(f"第 {i + 1} 段轉檔失敗：{r.stderr.strip().splitlines()[-1] if r.stderr.strip() else '未知原因'}")
            parts.append(part)
            log(f"  片段 {i + 1}/{len(segments)} 完成（{s:.1f}–{e:.1f} 秒）")
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p in parts:
                f.write(f"file '{p}'\n")
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        r = _run(["-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", out])
        if r.returncode != 0:
            raise RuntimeError("串接失敗：" + (r.stderr.strip().splitlines() or ["未知原因"])[-1])


def process(path, out, mode="auto", target=60.0, vertical=False, silence_db=-35, min_silence=0.6, log=print):
    if mode == "short":
        vertical, base_mode = True, "highlight"
        target = min(target, 58.0)  # Shorts 上限 60 秒，留一點緩衝
    else:
        base_mode = mode
    info, used, segs = analyze(path, base_mode, target, silence_db, min_silence)
    log(f"原片 {info['duration']:.1f} 秒，使用 {used} 模式，保留 {len(segs)} 段、共 {sum(e - s for s, e in segs):.1f} 秒")
    render(path, segs, out, vertical=vertical, has_audio=info["has_audio"], log=log)
    res = probe(out)
    return {"input_seconds": round(info["duration"], 2), "output_seconds": round(res["duration"] or 0, 2),
            "mode": used + ("+vertical" if vertical else ""), "segments": len(segs)}


# ---------- 自我測試 ----------

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

    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        print("SKIP 找不到 ffmpeg，略過實際轉檔測試（CI 與 GitHub Actions 上會跑）")
    else:
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
            res = process(src, out, mode="auto", log=lambda *_: None)
            check(res["mode"] == "jumpcut" and 4.5 <= res["output_seconds"] <= 6.0,
                  f"auto 模式剪掉 3 秒空白（輸出 {res['output_seconds']} 秒）")
            out2 = os.path.join(d, "o2.mp4")
            res2 = process(src, out2, mode="short", target=4, log=lambda *_: None)
            info2 = probe(out2)
            check((info2["width"], info2["height"]) == (1080, 1920) and res2["output_seconds"] <= 6,
                  "short 模式輸出直式 1080x1920 且長度受控")
            mute = os.path.join(d, "mute.mp4")
            _run(["-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=6", "-c:v", "libx264",
                  "-preset", "ultrafast", mute])
            res3 = process(mute, os.path.join(d, "o3.mp4"), mode="auto", target=3, log=lambda *_: None)
            check(res3["mode"] == "highlight" and res3["output_seconds"] > 0, "沒有聲音的影片自動改用 highlight")
            try:
                process(mute, os.path.join(d, "o4.mp4"), mode="jumpcut", log=lambda *_: None)
                check(False, "沒聲音卻要求 jumpcut 應提示改模式")
            except ValueError as e:
                check("改用" in str(e), "沒聲音卻要求 jumpcut 會說明該怎麼做")

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
        p.add_argument("--mode", default="auto", choices=["auto", "jumpcut", "highlight", "short"])
        p.add_argument("--target", type=float, default=60.0, help="highlight／short 的目標秒數")
        p.add_argument("--vertical", action="store_true", help="輸出直式 9:16")
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
            print(json.dumps({"duration": info["duration"], "mode": used, "segments": segs}, ensure_ascii=False))
            return 0
        res = process(args.input, args.output, args.mode, args.target, args.vertical, args.silence_db, args.min_silence)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError) as e:
        print(f"剪輯失敗：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
