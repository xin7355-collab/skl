#!/usr/bin/env python3
"""網頁 E2E 測試用的本機伺服器：照 Deploy web page 的方式提供 web/ 底下的網頁。

為什麼不直接 python -m http.server web/：
- 部署時會把 .github/apps.json 複製成網站根目錄的 apps.json（技能庫網頁與 X-Ray 都讀它），
  這裡用對應的方式提供，不必先複製檔案、也不會在 web/ 留下多餘檔案。
- catalog.json 與 zips/ 部署時才產生（不進 git）。每次啟動都重新產生：舊的 catalog.json
  沒有 zip 欄位，留著會讓「下載 ZIP」的測試在本機跟 CI 結果不同。
  ZIP 放在系統暫存資料夾（依 port 固定名稱），結束就刪；就算被強制結束，下次啟動也是覆蓋同一個資料夾，
  不會越積越多，也不會在 web/ 留下檔案。

用法：python3 serve.py [--port 8790]
"""
import argparse
import functools
import http.server
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
APPS = ROOT / ".github" / "apps.json"


class Handler(http.server.SimpleHTTPRequestHandler):
    zips_dir = None

    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean == "/apps.json":
            return str(APPS)
        if clean.startswith("/zips/") and self.zips_dir:
            # 交給父類別算出 web/ 底下的路徑（已處理 .. 與編碼），再換成暫存的 zips 資料夾
            mapped = Path(super().translate_path(path))
            rel = mapped.relative_to(WEB / "zips")
            return str(Path(self.zips_dir) / rel)
        return super().translate_path(path)

    def log_message(self, fmt, *args):  # 測試輸出只留 Playwright 的結果，不洗版
        pass


def main():
    ap = argparse.ArgumentParser(description="提供 web/ 給 Playwright 測試（apps.json 對應 .github/apps.json）")
    ap.add_argument("--port", type=int, default=8790)
    a = ap.parse_args()
    r = subprocess.run([sys.executable, str(WEB / "build_catalog.py")], cwd=str(ROOT))
    if r.returncode != 0:
        print("產生 catalog.json 失敗，技能庫網頁會讀不到技能清單", file=sys.stderr)
        return 1
    # Playwright 結束時送 SIGTERM；轉成正常結束，下面的 finally 才會刪掉暫存的 ZIP
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    zips = Path(tempfile.gettempdir()) / f"skl-e2e-zips-{a.port}"
    try:
        r = subprocess.run([sys.executable, str(WEB / "build_zips.py"), "--out", str(zips),
                            "--catalog", str(WEB / "catalog.json")], cwd=str(ROOT))
        if r.returncode != 0:
            print("打包技能 ZIP 失敗，「下載 ZIP」連結的測試會失敗", file=sys.stderr)
            return 1
        Handler.zips_dir = str(zips)
        handler = functools.partial(Handler, directory=str(WEB))
        with http.server.ThreadingHTTPServer(("127.0.0.1", a.port), handler) as srv:
            print(f"serving {WEB} on http://127.0.0.1:{a.port}", flush=True)
            srv.serve_forever()
    finally:
        shutil.rmtree(zips, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
