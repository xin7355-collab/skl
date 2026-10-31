#!/usr/bin/env python3
"""discover_apps.py — 自動找出 GitHub 帳號底下所有的 App repo，產生「我的 App」清單。

為什麼要有這支：以前清單（.github/apps.json）是手寫的，開了新 repo 沒人記得加，
X-Ray「我的 App」、每天自動健檢、每週維護報告就都看不到它（2026-10-31 使用者發現 5 個沒掃到）。
現在每天部署、每週報告都先問 GitHub「這個帳號有哪些 repo」，手寫清單只留：
  - url：App 的線上網址（X-Ray 優先顯示，猜不到時才需要）
  - private：私人 repo（沒有 SKL_READ_TOKEN 時找不到它，但還是要列著）
  - exclude：不是 App 的 repo（例如 skl 自己）

用法：
  python3 ops/discover_apps.py --config .github/apps.json --out _site/apps.json
  python3 ops/discover_apps.py selftest
環境變數（都選用，Token 只走 HTTP 標頭、不放網址）：
  GITHUB_TOKEN / GH_TOKEN  提高 API 額度（沒有也能找公開 repo，每小時 60 次）
  SKL_READ_TOKEN           唯讀 Token：有它才找得到私人 repo
  GITHUB_REPOSITORY        正在跑的 repo（skl 自己），自動排除

結束碼：一律 0，不擋部署；但找不到就退回手寫清單，並印 ::warning:: 說原因（不默默失敗）。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APPS_CFG = os.path.join(ROOT, ".github", "apps.json")
API = "https://api.github.com"
MAX_PAGES = 10          # 每頁 100 個，1000 個 repo 以內；防止 API 異常時無限翻頁
NOTE = ("自動產生（ops/discover_apps.py）：帳號底下的 repo 每天自動加入；"
        "要補線上網址、標私人、排除不是 App 的 repo，改 skl 的 .github/apps.json。")


def _get(url, token="", tries=4, sleep=time.sleep):
    """GET JSON；429／5xx／連線失敗用指數退避（2、4、8 秒），401／403／404 這種不會自己好的直接丟出。"""
    last = None
    for i in range(tries):
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "skl-discover-apps"})
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise
            wait = e.headers.get("Retry-After") if e.headers else None
            delay = min(int(wait), 60) if wait and wait.isdigit() else 2 ** (i + 1)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            delay = 2 ** (i + 1)
        if i < tries - 1:
            sleep(delay)
    raise last


def _pages(url, token, get):
    out = []
    for p in range(1, MAX_PAGES + 1):
        batch = get(f"{url}{'&' if '?' in url else '?'}per_page=100&page={p}", token)
        if not isinstance(batch, list):
            raise ValueError("GitHub 回的不是清單")
        out += batch
        if len(batch) < 100:
            break
    return out


def list_repos(owner, token="", read_token="", get=_get):
    """回傳 (repos, 有沒有找私人 repo)。公開的一定找；有唯讀 Token 再補私人的。"""
    found = {r["full_name"].lower(): r for r in _pages(f"{API}/users/{owner}/repos?type=owner&sort=pushed", token, get)}
    with_private = False
    if read_token:
        # 唯讀 Token 可能只開放部分 repo，所以公開清單照上面那份，這裡只補「這個帳號自己的」私人 repo
        for r in _pages(f"{API}/user/repos?affiliation=owner&visibility=private", read_token, get):
            if (r.get("owner") or {}).get("login", "").lower() == owner.lower():
                found.setdefault(r["full_name"].lower(), r)
        with_private = True
    return list(found.values()), with_private


def merge(cfg, repos, self_repo="", with_private=False):
    """手寫清單＋自動找到的 → 一份清單。回傳 (apps, notfound, skipped, hidden_private)。

    ⛔ 自動找到的「私人」repo 不放進清單，只算數量：這份清單會放上公開網站、每週報告是公開 Issue，
       私人 repo 的名字一放就公開了。要掃它，使用者自己把它寫進 .github/apps.json（等於同意公開名稱）。

    - 手寫清單的 url／private 一律保留（自動找不會知道線上網址）。
    - 排除：exclude、正在跑的 repo（skl 自己）、個人首頁 repo（帳號名/帳號名）、封存的 repo。
    - 手寫清單有、GitHub 找不到：私人的照列（可能只是沒權限）；公開的也照列但記進 notfound，
      讓健檢把它標成失敗——可能是改名或刪掉了，要人看一下，不能悄悄消失。
    """
    owner = (cfg.get("owner") or "").lower()
    excl = {x.lower() for x in cfg.get("exclude") or []}
    if self_repo:
        excl.add(self_repo.lower())
    manual = {a["repo"].lower(): a for a in cfg.get("apps") or [] if a.get("repo")}
    apps, seen, skipped, hidden = [], set(), [], 0
    # 最近有推的排前面：正在做的 App 在 X-Ray 清單最上面
    for r in sorted(repos, key=lambda x: x.get("pushed_at") or "", reverse=True):
        full = r["full_name"]
        key = full.lower()
        name_is_owner = key.split("/")[-1] == key.split("/")[0]
        why = ("排除清單" if key in excl else "個人首頁 repo" if name_is_owner else "已封存" if r.get("archived") else "")
        if why:
            skipped.append([full, why])
            continue
        m = manual.get(key)
        if r.get("private") and not m:
            hidden += 1
            seen.add(key)
            continue
        a = {"repo": full}
        if m:
            a.update({k: v for k, v in m.items() if k != "repo"})
        else:
            a["auto"] = True
        if r.get("private"):
            a["private"] = True
        if r.get("pushed_at"):
            a["pushed_at"] = r["pushed_at"]
        apps.append(a)
        seen.add(key)
    notfound = []
    for key, m in manual.items():
        if key in seen or key in excl:
            continue
        if owner and key.split("/")[0] != owner:
            apps.append(dict(m))            # 別的帳號的 repo：自動找本來就找不到，照手寫的列
            continue
        apps.append(dict(m))
        if not (m.get("private") and not with_private):
            notfound.append(m["repo"])
    return apps, notfound, skipped, hidden


def resolve(cfg, token="", read_token="", self_repo="", get=_get):
    """給部署與每週報告共用：成功回自動清單；GitHub 查不到就退回手寫清單並附 error（不默默失敗）。"""
    owner = cfg.get("owner") or ""
    base = {"_說明": NOTE, "owner": owner, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if not owner:
        return {**base, "source": "manual", "apps": list(cfg.get("apps") or []),
                "error": "apps.json 沒寫 owner（GitHub 帳號），只能用手寫清單"}
    try:
        repos, with_private = list_repos(owner, token, read_token, get)
    except Exception as e:  # noqa: BLE001 — 任何失敗都退回手寫清單，部署照跑
        code = getattr(e, "code", "")
        why = f"HTTP {code}" if code else type(e).__name__
        hint = "（額度用完的話明天會自己好）" if code in (403, 429) else ""
        return {**base, "source": "manual", "apps": list(cfg.get("apps") or []),
                "error": f"自動找 App 失敗：{why}{hint}，先用手寫清單"}
    apps, notfound, skipped, hidden = merge(cfg, repos, self_repo, with_private)
    return {**base, "source": "auto", "private_scanned": with_private, "apps": apps,
            "new": [a["repo"] for a in apps if a.get("auto")], "notfound": notfound, "skipped": skipped,
            "hidden_private": hidden}


def load_cfg(path=APPS_CFG):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def report(data, out=sys.stdout):
    """給 Actions log 看的摘要；有狀況印 ::warning::（工作流頁面看得到黃燈）。"""
    if data.get("error"):
        print(f"::warning::{data['error']}", file=out)
    n = len(data["apps"])
    print(f"🧭 我的 App：{n} 個（{'自動找' if data['source'] == 'auto' else '手寫清單'}）"
          + (f"，新找到 {len(data.get('new') or [])} 個：{'、'.join(r.split('/')[-1] for r in data.get('new') or [])}"
             if data.get("new") else ""), file=out)
    for full, why in data.get("skipped") or []:
        print(f"   略過 {full}（{why}）", file=out)
    if data.get("hidden_private"):
        print(f"   另有 {data['hidden_private']} 個私人 repo 沒列出（網站與報告是公開的；要掃就寫進 .github/apps.json）", file=out)
    if data.get("notfound"):
        print("::warning::清單裡有、GitHub 帳號找不到（可能改名、刪除或改成私人）："
              + "、".join(data["notfound"]) + "。確認後改 skl 的 .github/apps.json", file=out)


def selftest():
    fails = []

    def check(c, name):
        print(("✅ " if c else "❌ ") + name)
        if not c:
            fails.append(name)

    cfg = {"owner": "me", "exclude": ["me/notes"],
           "apps": [{"repo": "me/Shop", "url": "https://shop.example"}, {"repo": "me/secret", "private": True},
                    {"repo": "me/gone"}, {"repo": "other/app"}]}
    repos = [{"full_name": "me/shop", "pushed_at": "2026-10-01T00:00:00Z"},
             {"full_name": "me/NewApp", "pushed_at": "2026-10-31T00:00:00Z"},
             {"full_name": "me/skl", "pushed_at": "2026-10-30T00:00:00Z"},
             {"full_name": "me/me"}, {"full_name": "me/old", "archived": True}, {"full_name": "me/notes"}]
    apps, notfound, skipped, _ = merge(cfg, repos, self_repo="me/skl")
    names = [a["repo"] for a in apps]
    check(names[:2] == ["me/NewApp", "me/shop"], "最近有推的排前面")
    check(next(a for a in apps if a["repo"] == "me/NewApp").get("auto") is True, "新 repo 自動加入並標 auto")
    shop = next(a for a in apps if a["repo"] == "me/shop")
    check(shop.get("url") == "https://shop.example" and "auto" not in shop, "大小寫不同也認得手寫的那筆，url 保留")
    check(not {"me/skl", "me/me", "me/old", "me/notes"} & set(names), "排除：skl 自己、個人首頁、封存、exclude")
    check({s[0] for s in skipped} == {"me/skl", "me/me", "me/old", "me/notes"}, "排除的每一個都說得出原因")
    check("me/secret" in names and "me/secret" not in notfound, "私人 repo 沒 Token 找不到：照列、不算找不到")
    check("me/gone" in names and notfound == ["me/gone"], "公開的找不到：照列並記 notfound（不悄悄消失）")
    check("other/app" in names and "other/app" not in notfound, "別的帳號的 repo 照手寫的列")
    _, nf2, _, _ = merge(cfg, repos, self_repo="me/skl", with_private=True)
    check(set(nf2) == {"me/gone", "me/secret"}, "有唯讀 Token 還找不到私人 repo → 也算找不到")

    # resolve：成功、分頁、私人、失敗退回
    calls = []

    def fake(url, token=""):
        calls.append((url, token))
        if "/users/me/repos" in url:
            page = int(url.rsplit("page=", 1)[1])
            if page == 1:
                return [{"full_name": f"me/a{i}", "pushed_at": "2026-01-01"} for i in range(100)]
            return [{"full_name": "me/last"}]
        if "/user/repos" in url:
            return [{"full_name": "me/priv", "private": True, "owner": {"login": "me"}},
                    {"full_name": "me/listed", "private": True, "owner": {"login": "me"}},
                    {"full_name": "org/x", "private": True, "owner": {"login": "org"}}]
        raise AssertionError(url)
    d = resolve({"owner": "me", "apps": [{"repo": "me/listed", "private": True}]}, token="T1", read_token="R1", get=fake)
    names = [a["repo"] for a in d["apps"]]
    check(d["source"] == "auto" and sum(1 for n in names if n.startswith("me/a")) == 100 and "me/last" in names,
          "翻頁：100 個一頁，第二頁不滿就停")
    check("me/priv" not in names and d["hidden_private"] == 1, "自動找到的私人 repo 不公開名稱，只算數量")
    check("me/listed" in names and "me/listed" not in d["notfound"], "手寫清單列過的私人 repo 才列出、而且找得到")
    check("org/x" not in names, "別人的私人 repo 不收")
    check(all(t == "T1" for u, t in calls if "/users/" in u) and all(t == "R1" for u, t in calls if "/user/repos" in u),
          "Token 各用在對的請求")
    check(all("T1" not in u and "R1" not in u for u, _ in calls), "Token 不放進網址")

    def boom(url, token=""):
        raise urllib.error.HTTPError(url, 403, "rate limited", {}, None)
    d = resolve({"owner": "me", "apps": [{"repo": "me/keep"}]}, get=boom)
    check(d["source"] == "manual" and d["apps"] == [{"repo": "me/keep"}] and "HTTP 403" in d["error"],
          "GitHub 查不到 → 退回手寫清單，並說原因")
    d = resolve({"apps": [{"repo": "me/keep"}]}, get=fake)
    check(d["source"] == "manual" and "owner" in d["error"], "沒寫 owner → 手寫清單＋說要補什麼")

    # _get：會重試的才重試、有退避；401/404 不重試
    tries, slept = [], []

    def opener_factory(codes):
        it = iter(codes)

        def opener(req, timeout=20):
            tries.append(req.full_url)
            c = next(it)
            if c == 200:
                class R:
                    def __enter__(s): return s
                    def __exit__(s, *a): return False
                    def read(s): return b"[1]"
                return R()
            raise urllib.error.HTTPError(req.full_url, c, "x", {}, None)
        return opener
    real = urllib.request.urlopen
    try:
        urllib.request.urlopen = opener_factory([502, 503, 200])
        check(_get("https://x/a", sleep=slept.append) == [1] and slept == [2, 4], "502／503 退避 2、4 秒後成功")
        tries.clear()
        urllib.request.urlopen = opener_factory([404])
        try:
            _get("https://x/b", sleep=slept.append)
            check(False, "404 要丟出")
        except urllib.error.HTTPError:
            check(len(tries) == 1, "404 不重試")
    finally:
        urllib.request.urlopen = real

    # 真的設定檔要能讀、有 owner，而且排除 skl 自己
    real_cfg = load_cfg()
    check(bool(real_cfg.get("owner")), ".github/apps.json 有寫 owner")
    check(any(x.lower().endswith("/skl") for x in real_cfg.get("exclude") or []), ".github/apps.json 排除 skl 自己")

    print(f"❌ {len(fails)} 條沒過" if fails else "✅ discover_apps selftest 通過")
    return 1 if fails else 0


def main():
    if sys.argv[1:] == ["selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description="自動找出 GitHub 帳號底下的 App repo，產生「我的 App」清單")
    ap.add_argument("--config", default=APPS_CFG, help="手寫設定（owner、exclude、url、private）")
    ap.add_argument("--out", required=True, help="輸出的 apps.json")
    a = ap.parse_args()
    data = resolve(load_cfg(a.config),
                   token=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "",
                   read_token=os.environ.get("SKL_READ_TOKEN") or "",
                   self_repo=os.environ.get("GITHUB_REPOSITORY") or "")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    report(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
