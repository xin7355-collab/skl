#!/usr/bin/env python3
"""網頁 E2E 測試用的本機伺服器：照 Deploy web page 的方式提供 web/ 底下的網頁。

為什麼不直接 python -m http.server web/：
- 部署時會把 .github/apps.json 複製成網站根目錄的 apps.json（技能庫網頁與 X-Ray 都讀它），
  這裡用對應的方式提供，不必先複製檔案、也不會在 web/ 留下多餘檔案。
- catalog.json 部署時才產生（不進 git），沒有就先跑 build_catalog.py。

用法：python3 serve.py [--port 8790]
"""
import argparse
import functools
import http.server
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
APPS = ROOT / ".github" / "apps.json"


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean == "/apps.json":
            return str(APPS)
        return super().translate_path(path)

    def log_message(self, fmt, *args):  # 測試輸出只留 Playwright 的結果，不洗版
        pass


def main():
    ap = argparse.ArgumentParser(description="提供 web/ 給 Playwright 測試（apps.json 對應 .github/apps.json）")
    ap.add_argument("--port", type=int, default=8790)
    a = ap.parse_args()
    if not (WEB / "catalog.json").exists():
        r = subprocess.run([sys.executable, str(WEB / "build_catalog.py")], cwd=str(ROOT))
        if r.returncode != 0:
            print("產生 catalog.json 失敗，技能庫網頁會讀不到技能清單", file=sys.stderr)
            return 1
    handler = functools.partial(Handler, directory=str(WEB))
    with http.server.ThreadingHTTPServer(("127.0.0.1", a.port), handler) as srv:
        print(f"serving {WEB} on http://127.0.0.1:{a.port}", flush=True)
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
