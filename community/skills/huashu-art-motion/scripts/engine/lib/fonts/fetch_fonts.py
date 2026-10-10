#!/usr/bin/env python3
"""下載動畫引擎用的字型（skl 收錄時拿掉了 16MB 的 .woff，避免每個 App 安裝技能都背著字型）。

為什麼這樣做：
- 字型固定從原作者 repo 的同一個 commit 下載，並用原作者 release-manifest.json 裡的 sha256 逐檔驗證，
  檔案被換掉或下載不完整都會拒收，不會默默用壞檔。
- 已經存在且雜湊正確的檔案不重抓；網路失敗用指數退避重試。

用法：
  python3 fetch_fonts.py            # 補齊缺少的字型
  python3 fetch_fonts.py --check    # 只檢查，不下載（缺幾個、壞幾個）
  python3 fetch_fonts.py selftest   # 離線自我測試
"""
import argparse
import hashlib
import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

COMMIT = "d861767d180008d27675819932070a670a3ae43f"
BASE = "https://raw.githubusercontent.com/alchaincyf/huashu-art-motion/" + COMMIT + "/"
PREFIX = "scripts/engine/lib/fonts/"
HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parents[3]
MAX_BYTES = 8 * 1024 * 1024  # 單一字型最大的約 4.3MB；超過就當成異常，不寫進硬碟


def wanted(manifest_path):
    files = json.loads(Path(manifest_path).read_text(encoding="utf-8")).get("files", {})
    return {k[len(PREFIX):]: v for k, v in files.items() if k.startswith(PREFIX) and k.endswith(".woff")}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, tries=4, opener=urllib.request.urlopen, sleep=time.sleep):
    for i in range(tries):
        try:
            with opener(url, timeout=60) as r:
                data = r.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("檔案異常地大")
            return data
        except Exception as e:  # 網路、逾時、HTTP 錯誤都重試
            if i == tries - 1:
                raise RuntimeError(f"下載失敗（{e}）") from e
            sleep(2 ** (i + 1))
    return None


def fetch(dest, want, opener=urllib.request.urlopen, sleep=time.sleep, check_only=False):
    """回傳 (已就緒, 新下載, 失敗清單)。"""
    ok, got, bad = 0, 0, []
    for name, digest in sorted(want.items()):
        p = Path(dest) / name
        if p.exists() and sha256(p) == digest:
            ok += 1
            continue
        if check_only:
            bad.append((name, "缺少" if not p.exists() else "雜湊不符"))
            continue
        try:
            data = download(BASE + PREFIX + name, opener=opener, sleep=sleep)
        except RuntimeError as e:
            bad.append((name, str(e)))
            continue
        if hashlib.sha256(data).hexdigest() != digest:
            bad.append((name, "下載內容的雜湊不符，拒收"))
            continue
        tmp = p.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(p)
        got += 1
    return ok, got, bad


def selftest():
    fails = 0

    def check(cond, name):
        nonlocal fails
        print(("PASS " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    good, evil = b"font-A", b"evil"
    want = {"A.woff": hashlib.sha256(good).hexdigest(), "B.woff": hashlib.sha256(b"font-B").hexdigest()}

    class Resp:
        def __init__(self, data):
            self.data = data

        def read(self, n):
            return self.data[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    calls = []

    def opener(url, timeout):
        calls.append(url)
        if url.endswith("A.woff"):
            return Resp(good)
        if url.endswith("B.woff"):
            return Resp(evil)
        raise OSError("boom")

    with tempfile.TemporaryDirectory() as td:
        ok, got, bad = fetch(td, want, opener=opener, sleep=lambda s: None)
        check(got == 1 and (Path(td) / "A.woff").read_bytes() == good, "雜湊正確才寫入")
        check(len(bad) == 1 and bad[0][0] == "B.woff" and not (Path(td) / "B.woff").exists(), "雜湊不符拒收、不留壞檔")
        check(all(COMMIT in u for u in calls), "固定從同一個 commit 下載")
        n = len(calls)
        ok, got, bad = fetch(td, {"A.woff": want["A.woff"]}, opener=opener, sleep=lambda s: None)
        check(ok == 1 and got == 0 and len(calls) == n, "已存在且正確就不重抓")
        ok, got, bad = fetch(td, {"C.woff": "x"}, opener=opener, sleep=lambda s: None)
        check(len(bad) == 1 and "下載失敗" in bad[0][1], "網路失敗會重試後回報，不當掉")
        ok, got, bad = fetch(td, want, check_only=True)
        check(ok == 1 and bad == [("B.woff", "缺少")], "--check 只檢查不下載")
    real = wanted(SKILL_ROOT / "release-manifest.json")
    check(len(real) >= 40, f"從 release-manifest.json 讀到 {len(real)} 個字型")
    print("自我測試全部通過" if not fails else f"{fails} 項失敗")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description="下載並驗證動畫引擎的字型")
    ap.add_argument("cmd", nargs="?", choices=["selftest"], help="selftest：離線自我測試")
    ap.add_argument("--check", action="store_true", help="只檢查缺哪些，不下載")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    want = wanted(SKILL_ROOT / "release-manifest.json")
    ok, got, bad = fetch(HERE, want, check_only=a.check)
    print(f"字型 {len(want)} 個：已就緒 {ok}、這次下載 {got}、失敗 {len(bad)}")
    for name, why in bad:
        print(f"  ✗ {name}：{why}")
    if bad:
        print("沒有字型時畫面會改用系統字型（排版會跟原作不同）。網路恢復後再執行一次即可。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
