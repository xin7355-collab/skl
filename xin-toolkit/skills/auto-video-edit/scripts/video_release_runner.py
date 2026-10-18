#!/usr/bin/env python3
"""video_release_runner.py — 在 GitHub Actions 上自動處理「草稿 Release」裡的影片。

流程（手機就能操作）：
  1. 在 repo 建一個草稿 Release，標籤（tag）或標題以 video 開頭，附上影片。
     草稿只有 repo 擁有者看得到——公開 repo 也不會外流。
  2. 內文可寫選項（每行一個）：「模式: short」「長度: 45」「直式」「直式: 模糊背景」；不寫就是全自動。
  3. 排程每 30 分鐘跑一次：找還沒剪過的影片 → 下載 → 剪輯 → 把成品與縮圖總覽傳回同一個 Release。

每部影片的嘗試次數怎麼算（為什麼要「開始標記」）：
  開始處理前先上傳 `{檔名}.start{N}.txt`；失敗時再傳 `{檔名}.error{N}.txt`。
  以前只在 Python 例外時留 .error，但工作流逾時是整個 job 被砍，什麼都留不下，嘗試次數永遠是 0，
  同一部片被無限重剪、後面的影片全卡住。現在「有開始、沒成品」就算一次嘗試，滿 3 次停止自動重試。
  還沒試過的影片排在重試的前面，一部壞片不會擋住整個佇列。

時間預算（--minutes，要比工作流的 timeout-minutes 小）：
  先依影片長度與解析度估時，明顯剪不完就直接留 `{檔名}.error{N}-too-long.txt`（不會再自動重試）；
  開剪後也有 deadline，時間到 ffmpeg 會被停下，留 10 分鐘給上傳成品與說明檔。
下載與上傳都用串流，不把影片讀進記憶體。

用法：
  GITHUB_TOKEN=… GITHUB_REPOSITORY=owner/repo python3 video_release_runner.py run [--max 1] [--minutes 135]
  python3 video_release_runner.py selftest
"""

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import video_auto_edit as vae  # noqa: E402

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
MAX_TRIES = 3
CHUNK = 1024 * 1024
UPLOAD_RESERVE = 10 * 60  # 秒；deadline 之後留給上傳成品、縮圖與說明檔
RETRY_HINT = "刪掉這部影片的 .start 與 .error 檔就會重新嘗試"


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
    opt = {"mode": "auto", "target": 60.0, "vertical": False, "fill": "crop"}
    m = re.search(r"(?:模式|mode)\s*[:：]\s*(auto|jumpcut|highlight|short|自動|去空白|精華|短影音)", body, re.I)
    if m:
        opt["mode"] = {"自動": "auto", "去空白": "jumpcut", "精華": "highlight", "短影音": "short"}.get(m.group(1), m.group(1).lower())
    m = re.search(r"(?:長度|target)\s*[:：]\s*(\d+)", body, re.I)
    if m:
        opt["target"] = float(m.group(1))
    if re.search(r"直式|vertical", body, re.I):
        opt["vertical"] = True
    # 「直式: 模糊背景」「直式：模糊」「vertical: blur」；也接受單獨寫一行「模糊背景」。
    if re.search(r"(?:直式|vertical)\s*[:：]\s*(?:模糊|blur)|模糊背景|blur\s*background", body, re.I):
        opt["vertical"], opt["fill"] = True, "blur"
    return opt


def _attempts(stem, names):
    """回傳 (嘗試過的編號集合, 有 .error 的編號集合, 是否已判定太長)。"""
    rx = re.compile(rf"^{re.escape(stem)}\.(start|error)(\d+)(-too-long)?\.txt$")
    started, errored, too_long = set(), set(), False
    for n in names:
        m = rx.match(n)
        if m:
            (started if m.group(1) == "start" else errored).add(int(m.group(2)))
            too_long |= bool(m.group(3))
    return started | errored, errored, too_long


def scan_release(release):
    """回傳 (待處理, 要補說明的)。後者是：試滿次數、最後一次是被砍（只有 start 沒有 error）——
    使用者在 Release 頁看不到原因，要補一個 .error 說清楚，不然看起來像默默不做了。"""
    names = {a["name"] for a in release.get("assets", [])}
    jobs, gave_up = [], []
    for a in release.get("assets", []):
        stem, ext = os.path.splitext(a["name"])
        if ext.lower() not in vae.VIDEO_EXT or "-edited" in stem:
            continue
        if any(n.startswith(stem + "-edited") for n in names):
            continue  # 已經剪好（有成品就不再算嘗試次數）
        tried, errored, too_long = _attempts(stem, names)
        job = {"asset": a, "stem": stem, "tries": len(tried), "next": max(tried, default=0) + 1}
        if too_long:
            continue  # 已判定剪不完，重試也一樣；使用者刪掉說明檔才重來
        if len(tried) >= MAX_TRIES:
            last = max(tried)
            if last not in errored:
                gave_up.append(dict(job, interrupted=last))
            continue
        jobs.append(job)
    return jobs, gave_up


def pending_jobs(release):
    return scan_release(release)[0]


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


def upload_text(release, token, tmp, name, text):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return upload(release, token, p, name, "text/plain")


def delete_asset(repo, token, asset_id, api=API):
    with _req(f"{api}/repos/{repo}/releases/assets/{asset_id}", token, method="DELETE"):
        pass


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _default_processor(log):
    def proc(src, out, o, deadline=None, preview=None):
        return vae.process(src, out, o["mode"], o["target"], o["vertical"], fill=o.get("fill", "crop"),
                           preview=preview, deadline=deadline, log=log)
    return proc


def run(repo, token, max_jobs=1, api=API, processor=None, inspector=None, minutes=None, log=print,
        clock=time.monotonic):
    processor = processor or _default_processor(log)
    inspector = inspector or vae.probe
    t0 = clock()
    # budget：一部片最多能花多少秒處理；deadline：這一輪處理必須停下的時間點（之後留給上傳）。
    budget = max(60.0, minutes * 60 - UPLOAD_RESERVE) if minutes else None
    deadline = t0 + budget if budget else None
    drafts = list_video_drafts(repo, token, api)
    todo, gave_up = [], []
    for rel in drafts:
        jobs, gu = scan_release(rel)
        todo += [(rel, j) for j in jobs]
        gave_up += [(rel, j) for j in gu]
    todo.sort(key=lambda rj: rj[1]["tries"])  # 穩定排序：沒試過的先做，同次數維持原順序
    done = failed = deferred = 0
    with tempfile.TemporaryDirectory(prefix="vrr-") as tmp:
        for rel, job in gave_up:
            msg = (f"第 {job['interrupted']} 次處理沒有完成就被中斷（多半是超過工作流的時間上限），"
                   f"已連續 {MAX_TRIES} 次沒剪完，停止自動重試。\n"
                   f"影片可能太長：請分段上傳，或在 Release 內文改用「模式: short」／「模式: highlight」。\n{RETRY_HINT}。\n")
            try:
                upload_text(rel, token, tmp, f"{job['stem']}.error{job['interrupted']}.txt", msg)
            except (urllib.error.URLError, OSError) as e:
                log(f"✗ 補不上 {job['stem']} 的中斷說明：{e}")
            log(f"✗ {job['asset']['name']}：連續 {MAX_TRIES} 次被中斷，已停止自動重試")
            failed += 1
    if not todo:
        if not gave_up:
            log("沒有待剪的影片（草稿 Release 的標籤或標題要以 video 開頭）。")
        return 1 if failed else 0
    for idx, (rel, job) in enumerate(todo[:max_jobs]):
        if deadline and idx > 0 and deadline - clock() < 15 * 60:
            break  # 剩不到 15 分鐘就不開新的一部，留到下一輪（還沒上傳開始標記，不會算一次嘗試）
        opt = parse_options(rel.get("body"))
        name, n = job["asset"]["name"], job["next"]
        log(f"處理 {name}（{rel.get('name') or rel.get('tag_name')}，第 {n} 次），選項 {opt}")
        with tempfile.TemporaryDirectory(prefix="vrr-") as tmp:
            src = os.path.join(tmp, name)
            out_name = f"{job['stem']}-edited-{opt['mode']}.mp4"
            out = os.path.join(tmp, out_name)
            preview = os.path.join(tmp, f"{job['stem']}-preview.jpg")
            err_name = f"{job['stem']}.error{n}.txt"
            try:
                # 先留開始標記再做事：就算整個 job 被砍，下一輪也知道這部試過一次。
                marker = upload_text(rel, token, tmp, f"{job['stem']}.start{n}.txt",
                                     f"{_now()} 開始第 {n} 次處理。\n"
                                     f"如果之後沒有出現成品也沒有 .error 檔，表示這次被中斷（多半是逾時），"
                                     f"下一輪會自動重試，最多 {MAX_TRIES} 次。\n")
                download(job["asset"], token, src)
                info = inspector(src)
                est = vae.estimate_seconds(info, opt["mode"], opt["target"])
                if budget and est > budget:
                    err_name = f"{job['stem']}.error{n}-too-long.txt"
                    raise vae.BudgetExceeded(
                        f"影片太長：{(info.get('duration') or 0) / 60:.0f} 分鐘、{info.get('width')}x{info.get('height')}，"
                        f"估計要 {est / 60:.0f} 分鐘，超過每輪上限 {budget / 60:.0f} 分鐘，所以沒有開始剪。")
                if deadline and est > deadline - clock():
                    # 這一輪前面已經剪過別部、剩的時間不夠：撤掉開始標記，留到下一輪，不算一次嘗試。
                    try:
                        delete_asset(repo, token, marker["id"], api)
                    except (urllib.error.URLError, OSError, KeyError) as e:
                        log(f"  撤不掉開始標記（下一輪會算成一次嘗試）：{e}")
                    log(f"  剩的時間不夠剪 {name}（估 {est / 60:.0f} 分鐘），留到下一輪")
                    deferred += 1
                    continue
                res = processor(src, out, opt, deadline=deadline, preview=preview)
                upload(rel, token, out, out_name, "video/mp4")
                note = ""
                if os.path.exists(preview):
                    try:
                        upload(rel, token, preview, os.path.basename(preview), "image/jpeg")
                    except (urllib.error.URLError, OSError) as e:
                        note = f"（成品已上傳，但縮圖上傳失敗：{e}）"
                else:
                    note = f"（成品已上傳，但沒有縮圖：{res.get('preview_error', '未知原因')}）"
                log(f"✓ 完成：{out_name} {res}{note}")
                done += 1
            except vae.BudgetExceeded as e:
                if not err_name.endswith("-too-long.txt"):
                    err_name = f"{job['stem']}.error{n}-too-long.txt"
                msg = (f"{e}\n請把影片分段上傳，或在 Release 內文改用「模式: short」／「模式: highlight」"
                       f"（只轉精華，快很多）。\n這個檔名有 too-long，不會自動重試；{RETRY_HINT}。\n")
                failed += _report_error(rel, token, tmp, err_name, msg, name, e, log)
            except Exception as e:  # noqa: BLE001 — 任何失敗都要留下說明，讓下一輪重試
                msg = f"第 {n} 次剪輯失敗：{e}\n下一輪會自動重試；連續失敗 {MAX_TRIES} 次後停止。{RETRY_HINT}。\n"
                failed += _report_error(rel, token, tmp, err_name, msg, name, e, log)
    left = len(todo) - done - failed + len(gave_up)
    log(f"本輪完成 {done}、失敗 {failed}、留到下一輪 {max(left, 0)}" + (f"（其中 {deferred} 部是時間不夠先跳過）" if deferred else "") + "。")
    return 1 if failed else 0


def _report_error(rel, token, tmp, err_name, msg, name, e, log):
    try:
        upload_text(rel, token, tmp, err_name, msg)
    except (urllib.error.URLError, OSError) as ue:
        log(f"  說明檔也傳不上去：{ue}")
    log(f"✗ {name}：{e}")
    return 1


def selftest():
    import http.server

    ok = True

    def check(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    base_opt = {"mode": "short", "target": 30.0, "vertical": False, "fill": "crop"}
    check(parse_options("模式：short\n長度: 30") == base_opt, "解析中文選項")
    check(parse_options("mode: jumpcut\nvertical")["vertical"] is True, "解析英文選項與直式")
    check(parse_options("模式: 精華")["mode"] == "highlight", "中文模式別名")
    check(parse_options(None)["mode"] == "auto", "沒寫選項就全自動")
    check(parse_options("直式")["fill"] == "crop", "只寫直式＝裁切")
    for body in ("直式: 模糊背景", "直式：模糊", "vertical: blur", "模式: short\n模糊背景"):
        o = parse_options(body)
        check(o["vertical"] and o["fill"] == "blur", f"直式模糊背景選項：{body!r}")

    def rel_of(names):
        return {"assets": [{"name": n, "url": n} for n in names]}

    # 上一次被砍：只有 start 標記、沒有 error → 算一次嘗試，仍會再挑。
    j = pending_jobs(rel_of(["k.mp4", "k.start1.txt"]))
    check(len(j) == 1 and j[0]["tries"] == 1 and j[0]["next"] == 2, "只有開始標記（上次被砍）算一次嘗試")
    j = pending_jobs(rel_of(["k.mp4", "k.start1.txt", "k.error1.txt", "k.start2.txt"]))
    check(j and j[0]["tries"] == 2, "start＋error 同一次只算一次")
    jobs, gu = scan_release(rel_of(["k.mp4", "k.start1.txt", "k.start2.txt", "k.start3.txt"]))
    check(jobs == [] and [g["interrupted"] for g in gu] == [3], "被砍滿 3 次就不再挑，並補一個中斷說明")
    jobs, gu = scan_release(rel_of(["k.mp4", "k.start1.txt", "k.start2.txt", "k.start3.txt", "k.error3.txt"]))
    check(jobs == [] and gu == [], "說明補過之後不重複補")
    check(pending_jobs(rel_of(["k.mp4", "k.start1.txt", "k.error1-too-long.txt"])) == [], "判定太長的不自動重試")
    check(pending_jobs(rel_of(["k.mp4", "k.start1.txt", "k.start2.txt", "k-edited-auto.mp4"])) == [], "有成品就不再處理")
    check(pending_jobs(rel_of(["k.mp4", "kk.start1.txt", "kk.start2.txt", "kk.start3.txt"]))[0]["tries"] == 0,
          "別部影片的標記不會算到這部（檔名前綴相同也一樣）")

    uploads, auth_on_blob, deletes = [], [], []
    state = {"assets": ["a.mp4", "b.mov", "c.mp4", "c-edited-auto.mp4", "d.mp4",
                        "d.error1.txt", "d.error2.txt", "d.error3.txt", "note.txt"]}

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
                    rel(1, "video-0924", True, list(state["assets"]), "模式: short"),
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
            uploads.append((self.path, len(body), self.headers.get("Authorization"), body))
            name = urllib.parse.unquote(self.path.split("name=", 1)[1])
            state["assets"].append(name)  # 像 GitHub 一樣，上傳後下一次列出就看得到
            self._json({"id": len(uploads), "name": name}, 201)

        def do_DELETE(self):
            deletes.append(self.path)
            self.send_response(204)
            self.end_headers()

    import urllib.parse
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    api = f"http://127.0.0.1:{srv.server_address[1]}"

    rels = list_video_drafts("o/r", "tok", api)
    check([r["id"] for r in rels] == [1], "只挑 video 開頭的草稿 Release")
    jobs = pending_jobs(rels[0])
    check([j["asset"]["name"] for j in jobs] == ["a.mp4", "b.mov"], "已剪好／失敗 3 次／非影片都跳過")

    seen = []
    small = lambda src: {"duration": 10.0, "width": 1280, "height": 720}  # noqa: E731

    def fake_ok(src, out, o, deadline=None, preview=None):
        seen.append((os.path.getsize(src), o["mode"], deadline))
        with open(out, "wb") as f:
            f.write(b"edited")
        with open(preview, "wb") as f:
            f.write(b"jpg")
        return {"output_seconds": 1}

    def names_uploaded():
        return [urllib.parse.unquote(p.split("name=", 1)[1]) for p, *_ in uploads]

    logs = []
    code = run("o/r", "tok", max_jobs=1, api=api, processor=fake_ok, inspector=small, minutes=135, log=logs.append)
    check(code == 0 and [(s, m) for s, m, _ in seen] == [(3 * CHUNK + 5, "short")], "下載完整（串流、跨轉址）並套用內文選項")
    check(names_uploaded() == ["a.start1.txt", "a-edited-short.mp4", "a-preview.jpg"],
          f"先傳開始標記、再傳成品與縮圖總覽（{names_uploaded()}）")
    check(seen[0][2] is not None, "剪輯時有 deadline（不會跑到工作流被砍）")
    check(auth_on_blob == [None], "轉址到儲存空間時不帶 Token")
    check(uploads[-1][2] == "Bearer tok", "上傳時帶 Token")
    check(any("留到下一輪 1" in ln for ln in logs), "一次只處理一部，其餘留到下一輪")

    # 模擬 b.mov 這一輪被整個砍掉：只留下開始標記。下一輪應該先做新來的 e.mp4，不是一直卡在 b。
    state["assets"] += ["b.start1.txt", "e.mp4"]
    seen.clear()
    run("o/r", "tok", max_jobs=1, api=api, processor=fake_ok, inspector=small, minutes=135, log=logs.append)
    check("e-edited-short.mp4" in names_uploaded() and "b-edited-short.mp4" not in names_uploaded(),
          "上次被砍的影片排到後面，佇列換下一部")

    def fake_fail(src, out, o, deadline=None, preview=None):
        raise RuntimeError("壞掉的影片")

    code = run("o/r", "tok", max_jobs=1, api=api, processor=fake_fail, inspector=small, minutes=135, log=logs.append)
    check(code == 1 and names_uploaded()[-2:] == ["b.start2.txt", "b.error2.txt"],
          f"失敗時結束碼 1 並留下 .error 說明檔、編號接續（{names_uploaded()[-2:]}）")
    check("重新嘗試" in uploads[-1][3].decode(), "說明檔寫了怎麼重試")

    # b 再被砍一次（只有 start3）→ 滿 3 次：不再剪，補一個中斷說明。
    state["assets"].append("b.start3.txt")
    seen.clear()
    n_before = len(uploads)
    code = run("o/r", "tok", max_jobs=1, api=api, processor=fake_ok, inspector=small, minutes=135, log=logs.append)
    check(seen == [] and names_uploaded()[n_before:] == ["b.error3.txt"] and code == 1,
          "被砍滿 3 次就不再挑，補上中斷說明並亮紅燈")
    check("分段" in uploads[-1][3].decode(), "中斷說明告訴使用者怎麼辦（分段或改模式）")
    n_before = len(uploads)
    code = run("o/r", "tok", max_jobs=1, api=api, processor=fake_ok, inspector=small, minutes=135, log=logs.append)
    check(code == 0 and names_uploaded()[n_before:] == [] and any("沒有待剪" in ln for ln in logs[-1:]),
          "全部處理完後安靜等待，不重複發失敗")

    # 太長：估時超過預算 → 不開始剪，直接留 too-long 說明，之後也不重試。
    state["assets"].append("long.mp4")
    seen.clear()
    huge = lambda src: {"duration": 5 * 3600.0, "width": 3840, "height": 2160}  # noqa: E731
    code = run("o/r", "tok", max_jobs=1, api=api, processor=fake_ok, inspector=huge, minutes=135, log=logs.append)
    check(code == 1 and seen == [] and names_uploaded()[-1] == "long.error1-too-long.txt", "估計剪不完：不開始剪，留 too-long 說明")
    check("影片太長" in uploads[-1][3].decode() and "分段" in uploads[-1][3].decode(), "太長的說明有原因和建議")
    rels = list_video_drafts("o/r", "tok", api)
    check(all(j["stem"] != "long" for j in pending_jobs(rels[0])), "判定太長後不再自動重試")

    # 剪到一半超過 deadline（估時估太少）→ 一樣留 too-long，不會被 job 砍掉而什麼都沒留。
    state["assets"].append("mid.mp4")

    def fake_timeout(src, out, o, deadline=None, preview=None):
        raise vae.BudgetExceeded("處理超過時間預算，已停止。")

    run("o/r", "tok", max_jobs=1, api=api, processor=fake_timeout, inspector=small, minutes=135, log=logs.append)
    check(names_uploaded()[-1] == "mid.error1-too-long.txt", "剪到一半時間到：停下並留 too-long 說明")

    # 一輪處理多部時，前一部用掉大部分時間 → 下一部撤掉開始標記、留到下一輪，不算嘗試。
    state["assets"] += ["p.mp4", "q.mp4"]
    fake_now = [0.0]

    def slow_ok(src, out, o, deadline=None, preview=None):
        fake_now[0] += 100 * 60  # 這部花了 100 分鐘
        return fake_ok(src, out, o, deadline, preview)

    # 1 小時 4K 的 short：估約 51 分鐘，單獨一部剪得完（< 125 分鐘），但前一部用掉 100 分鐘後就不夠了。
    medium = lambda src: {"duration": 3600.0, "width": 3840, "height": 2160}  # noqa: E731
    run("o/r", "tok", max_jobs=2, api=api, processor=slow_ok, inspector=medium, minutes=135, log=logs.append,
        clock=lambda: fake_now[0])
    check("p-edited-short.mp4" in names_uploaded() and "q-edited-short.mp4" not in names_uploaded() and deletes,
          "剩的時間不夠剪下一部：撤掉開始標記、留到下一輪")
    srv.shutdown()

    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="自動剪輯草稿 Release 裡的影片")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--max", type=int, default=1, help="這一輪最多處理幾部")
    r.add_argument("--minutes", type=float, default=None,
                   help="這一輪最多跑幾分鐘（要比工作流的 timeout-minutes 小，內含 10 分鐘上傳時間）")
    sub.add_parser("selftest")
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "run":
        token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
        if not token or not repo:
            print("需要環境變數 GITHUB_TOKEN 與 GITHUB_REPOSITORY（在 GitHub Actions 上會自動提供）。", file=sys.stderr)
            return 2
        return run(repo, token, args.max, minutes=args.minutes)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
