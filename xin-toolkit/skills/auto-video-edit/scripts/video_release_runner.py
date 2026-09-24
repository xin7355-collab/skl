#!/usr/bin/env python3
"""video_release_runner.py — 在 GitHub Actions 上自動處理「草稿 Release」裡的影片。

流程（手機就能操作）：
  1. 在 repo 建一個草稿 Release，標籤（tag）或標題以 video 開頭，附上影片。
     草稿只有 repo 擁有者看得到——公開 repo 也不會外流。
  2. 內文可寫選項（每行一個）：「模式: short」「長度: 45」「直式」；不寫就是全自動。
  3. 排程每 30 分鐘跑一次：找還沒剪過的影片 → 下載 → 剪輯 → 把成品傳回同一個 Release。

為什麼一次只處理一部：單部長片可能要跑很久，設時間預算，剩下的下一輪接著做；
失敗會留下 .error 說明檔，下一輪自動重試，最多 3 次，避免無限重來。
下載與上傳都用串流，不把影片讀進記憶體。

用法：
  GITHUB_TOKEN=… GITHUB_REPOSITORY=owner/repo python3 video_release_runner.py run [--max 1]
  python3 video_release_runner.py selftest
"""

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import video_auto_edit as vae  # noqa: E402

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
MAX_TRIES = 3
CHUNK = 1024 * 1024


def _req(url, token, method="GET", data=None, headers=None, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, data=data, method=method, headers={"Accept": accept, **(headers or {})})
    # Token 只給 GitHub API；下載時會被轉址到儲存空間，不能把 Token 一起帶過去。
    req.add_unredirected_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=120)


def list_video_drafts(repo, token, api=API):
    out = []
    for page in (1, 2, 3):
        with _req(f"{api}/repos/{repo}/releases?per_page=100&page={page}", token) as r:
            items = json.load(r)
        out += [x for x in items if x.get("draft") and
                ((x.get("tag_name") or "").lower().startswith("video") or (x.get("name") or "").lower().startswith("video"))]
        if len(items) < 100:
            break
    return out


def parse_options(body):
    """從 Release 內文讀選項，中英文都可以。"""
    body = body or ""
    opt = {"mode": "auto", "target": 60.0, "vertical": False}
    m = re.search(r"(?:模式|mode)\s*[:：]\s*(auto|jumpcut|highlight|short|自動|去空白|精華|短影音)", body, re.I)
    if m:
        opt["mode"] = {"自動": "auto", "去空白": "jumpcut", "精華": "highlight", "短影音": "short"}.get(m.group(1), m.group(1).lower())
    m = re.search(r"(?:長度|target)\s*[:：]\s*(\d+)", body, re.I)
    if m:
        opt["target"] = float(m.group(1))
    if re.search(r"直式|vertical", body, re.I):
        opt["vertical"] = True
    return opt


def pending_jobs(release):
    names = {a["name"] for a in release.get("assets", [])}
    jobs = []
    for a in release.get("assets", []):
        stem, ext = os.path.splitext(a["name"])
        if ext.lower() not in vae.VIDEO_EXT or "-edited" in stem:
            continue
        if any(n.startswith(stem + "-edited") for n in names):
            continue  # 已經剪好
        tries = sum(1 for n in names if n.startswith(stem + ".error"))
        if tries >= MAX_TRIES:
            continue  # 失敗太多次，等使用者處理（刪掉 .error 檔就會重試）
        jobs.append({"asset": a, "stem": stem, "tries": tries})
    return jobs


def download(asset, token, dest):
    with _req(asset["url"], token, accept="application/octet-stream") as r, open(dest, "wb") as f:
        while True:
            b = r.read(CHUNK)
            if not b:
                break
            f.write(b)


def upload(release, token, path, name, ctype):
    url = re.sub(r"\{.*\}$", "", release["upload_url"]) + "?name=" + urllib.request.quote(name)
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        with _req(url, token, method="POST", data=f,
                  headers={"Content-Type": ctype, "Content-Length": str(size)}) as r:
            return json.load(r)


def run(repo, token, max_jobs=1, api=API, processor=None, log=print):
    processor = processor or (lambda src, out, o: vae.process(src, out, o["mode"], o["target"], o["vertical"],
                                                              log=log))
    drafts = list_video_drafts(repo, token, api)
    todo = [(r, j) for r in drafts for j in pending_jobs(r)]
    if not todo:
        log("沒有待剪的影片（草稿 Release 的標籤或標題要以 video 開頭）。")
        return 0
    done = failed = 0
    for rel, job in todo[:max_jobs]:
        opt = parse_options(rel.get("body"))
        name = job["asset"]["name"]
        log(f"處理 {name}（{rel.get('name') or rel.get('tag_name')}），選項 {opt}")
        with tempfile.TemporaryDirectory(prefix="vrr-") as tmp:
            src = os.path.join(tmp, name)
            out_name = f"{job['stem']}-edited-{opt['mode']}.mp4"
            out = os.path.join(tmp, out_name)
            try:
                download(job["asset"], token, src)
                res = processor(src, out, opt)
                upload(rel, token, out, out_name, "video/mp4")
                log(f"✓ 完成：{out_name} {res}")
                done += 1
            except Exception as e:  # noqa: BLE001 — 任何失敗都要留下說明，讓下一輪重試
                msg = f"第 {job['tries'] + 1} 次剪輯失敗：{e}\n刪掉這個 .error 檔就會重新嘗試；連續失敗 {MAX_TRIES} 次後停止自動重試。\n"
                errf = os.path.join(tmp, "err.txt")
                with open(errf, "w", encoding="utf-8") as f:
                    f.write(msg)
                try:
                    upload(rel, token, errf, f"{job['stem']}.error{job['tries'] + 1}.txt", "text/plain")
                except (urllib.error.URLError, OSError):
                    pass
                log(f"✗ {name}：{e}")
                failed += 1
    left = len(todo) - done - failed
    log(f"本輪完成 {done}、失敗 {failed}、留到下一輪 {max(left, 0)}。")
    return 1 if failed else 0


def selftest():
    import http.server

    ok = True

    def check(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    check(parse_options("模式：short\n長度: 30") == {"mode": "short", "target": 30.0, "vertical": False}, "解析中文選項")
    check(parse_options("mode: jumpcut\nvertical")["vertical"] is True, "解析英文選項與直式")
    check(parse_options("模式: 精華")["mode"] == "highlight", "中文模式別名")
    check(parse_options(None)["mode"] == "auto", "沒寫選項就全自動")

    uploads, auth_on_blob = [], []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            if self.path.startswith("/repos/o/r/releases?"):
                rel = lambda i, tag, draft, assets, body="": {  # noqa: E731
                    "id": i, "tag_name": tag, "name": tag, "draft": draft, "body": body,
                    "upload_url": f"{base}/upload/{i}/assets{{?name,label}}",
                    "assets": [{"name": n, "url": f"{base}/asset/{n}"} for n in assets]}
                self._json([
                    rel(1, "video-0924", True, ["a.mp4", "b.mov", "c.mp4", "c-edited-auto.mp4", "d.mp4",
                                                "d.error1.txt", "d.error2.txt", "d.error3.txt", "note.txt"], "模式: short"),
                    rel(2, "video-pub", False, ["x.mp4"]),       # 已公開的不處理（避免外流）
                    rel(3, "v1.0", True, ["y.mp4"]),             # 不是 video 開頭不處理
                ])
            elif self.path.startswith("/asset/"):
                # 模擬 GitHub 轉址到儲存空間
                self.send_response(302)
                self.send_header("Location", f"{base}/blob{self.path[6:]}")
                self.end_headers()
            elif self.path.startswith("/blob/"):
                auth_on_blob.append(self.headers.get("Authorization"))
                b = b"x" * (3 * CHUNK + 5)
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            else:
                self._json({}, 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            uploads.append((self.path, len(body), self.headers.get("Authorization")))
            self._json({"ok": True}, 201)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    api = f"http://127.0.0.1:{srv.server_address[1]}"

    rels = list_video_drafts("o/r", "tok", api)
    check([r["id"] for r in rels] == [1], "只挑 video 開頭的草稿 Release")
    jobs = pending_jobs(rels[0])
    check([j["asset"]["name"] for j in jobs] == ["a.mp4", "b.mov"], "已剪好／失敗 3 次／非影片都跳過")

    seen = []

    def fake_ok(src, out, o):
        seen.append((os.path.getsize(src), o["mode"]))
        with open(out, "wb") as f:
            f.write(b"edited")
        return {"output_seconds": 1}

    logs = []
    code = run("o/r", "tok", max_jobs=1, api=api, processor=fake_ok, log=logs.append)
    check(code == 0 and seen == [(3 * CHUNK + 5, "short")], "下載完整（串流、跨轉址）並套用內文選項")
    check(uploads and "name=a-edited-short.mp4" in uploads[-1][0], "成品上傳回同一個 Release")
    check(auth_on_blob == [None], "轉址到儲存空間時不帶 Token")
    check(uploads[-1][2] == "Bearer tok", "上傳時帶 Token")
    check(any("留到下一輪 1" in ln for ln in logs), "一次只處理一部，其餘留到下一輪")

    def fake_fail(src, out, o):
        raise RuntimeError("壞掉的影片")

    code = run("o/r", "tok", max_jobs=1, api=api, processor=fake_fail, log=logs.append)
    check(code == 1 and "name=a.error1.txt" in uploads[-1][0], "失敗時結束碼 1 並留下 .error 說明檔")
    srv.shutdown()

    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="自動剪輯草稿 Release 裡的影片")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--max", type=int, default=1, help="這一輪最多處理幾部")
    sub.add_parser("selftest")
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "run":
        token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
        if not token or not repo:
            print("需要環境變數 GITHUB_TOKEN 與 GITHUB_REPOSITORY（在 GitHub Actions 上會自動提供）。", file=sys.stderr)
            return 2
        return run(repo, token, args.max)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
