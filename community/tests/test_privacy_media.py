# skl 自寫：sanitize-metadata、compress-video、slice-video（chiphoton/MiniMax-H3-Codex-Drama 的 privacy 外掛，MIT）
# 上游沒附測試。這三個技能的價值全在「不外洩」：GPS 有沒有真的拿掉、輸入檔有沒有被改、
# 會不會覆蓋既有檔、會不會接受網址。所以用 ffmpeg 現做帶 GPS／作者的假照片與假影片，
# 實際跑一次腳本再用 exiftool／ffprobe 從外面檢查，不信任腳本自己的回報。
"""Offline tests for the privacy media skills (needs ffmpeg, ffprobe, exiftool)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _skill_helpers import skill_root

TIMEOUT = 120
SANITIZE = skill_root("sanitize-metadata") / "scripts" / "sanitize_media.py"
COMPRESS = skill_root("compress-video") / "scripts" / "compress_video.sh"
SLICE = skill_root("slice-video") / "scripts" / "slice_video.sh"
PROBE = skill_root("slice-video") / "scripts" / "probe_video.sh"


def _need_tools() -> None:
    # 缺工具就失敗、不 skip：工作流會先 apt 裝好，少了代表工作流漏裝，綠燈會是假的。
    missing = [t for t in ("ffmpeg", "ffprobe", "exiftool", "file") if not shutil.which(t)]
    if missing:
        raise AssertionError(f"缺少 {', '.join(missing)}：請在工作流用 apt 安裝（exiftool 的套件名稱是 libimage-exiftool-perl）")


def run(*args, ok=True) -> subprocess.CompletedProcess:
    proc = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=TIMEOUT)
    if ok and proc.returncode != 0:
        raise AssertionError(f"{args[0]} 失敗（{proc.returncode}）：{proc.stderr[-500:]}")
    return proc


def kv(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def exif(path: Path, *tags: str) -> dict:
    out = run("exiftool", "-j", *[f"-{t}" for t in tags], path).stdout
    return {k: v for k, v in json.loads(out)[0].items() if k != "SourceFile"}


def format_tags(path: Path) -> dict:
    out = run("ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", path).stdout
    return json.loads(out).get("format", {}).get("tags", {})


class PrivacyMediaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _need_tools()
        cls.tmp = Path(tempfile.mkdtemp(prefix="skl-privacy-"))
        cls.photo = cls.tmp / "photo.jpg"
        run("ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120",
            "-frames:v", "1", cls.photo)
        run("exiftool", "-overwrite_original", "-GPSLatitude=25.0330", "-GPSLatitudeRef=N",
            "-GPSLongitude=121.5654", "-GPSLongitudeRef=E", "-Make=Apple", "-Model=iPhone",
            "-Artist=Tester", cls.photo)
        cls.video = cls.tmp / "clip.mp4"
        run("ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-metadata", "title=secret-title", "-metadata", "location=+25.0330+121.5654/", cls.video)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_fixtures_really_carry_private_metadata(self):
        # 前提沒成立的話，後面「已清除」的檢查全都會假性通過
        self.assertIn("GPSLatitude", exif(self.photo, "GPSLatitude"))
        self.assertEqual(format_tags(self.video).get("title"), "secret-title")

    def test_photo_audit_reports_counts_only(self):
        out = run(sys.executable, SANITIZE, "audit", "--input", self.photo).stdout
        self.assertEqual(kv(out).get("sensitive_metadata_present"), "yes")
        # 只回報數量，不能把值印出來
        for secret in ("25.03", "Tester", "iPhone"):
            self.assertNotIn(secret, out)

    def test_photo_sanitize_removes_gps_and_keeps_input(self):
        dst = self.tmp / "photo-clean.jpg"
        out = run(sys.executable, SANITIZE, "sanitize", "--input", self.photo, "--output", dst).stdout
        self.assertEqual(kv(out).get("sensitive_field_count_after"), "0")
        self.assertEqual(exif(dst, "GPSLatitude", "Artist", "Make", "Model"), {})
        # 原檔不動：清錯檔的話使用者的原始照片就回不來了
        self.assertIn("GPSLatitude", exif(self.photo, "GPSLatitude"))
        # 已有輸出檔且沒給 --overwrite → 拒絕，而且不碰既有檔
        before = dst.read_bytes()
        again = run(sys.executable, SANITIZE, "sanitize", "--input", self.photo, "--output", dst, ok=False)
        self.assertNotEqual(again.returncode, 0)
        self.assertEqual(dst.read_bytes(), before)

    def test_sanitize_rejects_url_and_in_place(self):
        url = run(sys.executable, SANITIZE, "audit", "--input", "https://example.com/a.jpg", ok=False)
        self.assertNotEqual(url.returncode, 0)
        same = run(sys.executable, SANITIZE, "sanitize", "--input", self.photo, "--output", self.photo, ok=False)
        self.assertNotEqual(same.returncode, 0)

    def test_video_sanitize_strips_container_tags(self):
        dst = self.tmp / "clip-clean.mp4"
        out = run(sys.executable, SANITIZE, "sanitize", "--input", self.video, "--output", dst).stdout
        self.assertEqual(kv(out).get("sensitive_field_count_after"), "0")
        tags = format_tags(dst)
        self.assertNotIn("title", tags)
        self.assertNotIn("location", tags)

    def test_compress_strips_metadata_and_reports_sizes(self):
        dst = self.tmp / "clip-small.mp4"
        out = kv(run("bash", COMPRESS, "--input", self.video, "--output", dst, "--codec", "h264",
                     "--crf", "30", "--preset", "veryfast", "--audio-bitrate", "64k").stdout)
        self.assertEqual(out.get("status"), "ok")
        self.assertTrue(dst.is_file() and dst.stat().st_size > 0)
        self.assertGreater(float(out["output_duration_seconds"]), 2.5)
        tags = format_tags(dst)
        self.assertNotIn("title", tags)
        self.assertNotIn("location", tags)
        # 預設不覆蓋
        self.assertNotEqual(run("bash", COMPRESS, "--input", self.video, "--output", dst, ok=False).returncode, 0)

    def test_slice_precise_one_second_chunks(self):
        outdir = self.tmp / "parts"
        raw = run("bash", PROBE, "--input", self.video).stdout
        probe = json.loads(raw)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 3.0, delta=0.2)
        # probe 只能給技術欄位：標題、位置這些 tag 不能出現
        self.assertNotIn("secret-title", raw)
        self.assertNotIn("+25.0330", raw)
        out = kv(run("bash", SLICE, "--input", self.video, "--output-dir", outdir,
                     "--segment-seconds", "1", "--prefix", "part", "--mode", "precise").stdout)
        self.assertEqual(out.get("status"), "ok")
        files = sorted(outdir.glob("part*"))
        self.assertEqual(int(out["segment_count"]), len(files))
        self.assertEqual(len(files), 3)
        # 同一個前綴已經有檔案 → 拒絕，避免舊片段混進新結果
        again = run("bash", SLICE, "--input", self.video, "--output-dir", outdir,
                    "--segment-seconds", "1", "--prefix", "part", "--mode", "precise", ok=False)
        self.assertNotEqual(again.returncode, 0)


if __name__ == "__main__":
    unittest.main()
