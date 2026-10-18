#!/usr/bin/env python3
"""check_guides.py — 檢查兩份教學頁（web/dev-guide.html、web/agent.html）裡內嵌的資料。

為什麼要檢查：題目、範例、參考資源是寫在頁面裡的 JSON，網頁讀到不認得的值會靜靜地丟掉
（回到預設），使用者按「套用」看起來成功、其實少了一半設定。改題目或改選項名稱時，
範例很容易就對不上；頁面內連結（#st-xxx）改名後也會變成按了沒反應。部署與 CI 都跑這支。

用法：
  python3 web/check_guides.py            # 檢查，錯誤時結束碼 1
  python3 web/check_guides.py selftest
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAGES = ["dev-guide.html", "agent.html"]
MIN_TEMPLATES = 25
MIN_REFS = 15
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


def embedded(html, sid):
    m = re.search(r'<script type="application/json" id="%s">(.*?)</script>' % re.escape(sid), html, re.S)
    if not m:
        raise ValueError(f"找不到 <script id=\"{sid}\">")
    return json.loads(m.group(1))


def js_list(html, name):
    # 頁面程式裡的 cats: [...]、kinds: [...]：範例分類與參考資源種類的唯一來源
    m = re.search(name + r':\s*(\[[^\]]*\])', html)
    return json.loads(m.group(1)) if m else None


def check_def(d):
    errs, fields = [], {}
    qids = set()
    for q in d.get("questions") or []:
        if q.get("id") in qids:
            errs.append(f"題目 id 重複：{q.get('id')}")
        qids.add(q.get("id"))
        if not q.get("title"):
            errs.append(f"題目 {q.get('id')} 沒有標題")
        for f in q.get("fields") or []:
            fid = f.get("id")
            if fid in fields:
                errs.append(f"欄位 id 重複：{fid}")
            fields[fid] = f
            t = f.get("type")
            if t not in ("text", "textarea", "single", "multi"):
                errs.append(f"欄位 {fid} 的 type 不認得：{t}")
                continue
            if t in ("single", "multi"):
                vals = [o.get("v") for o in f.get("options") or []]
                if not vals:
                    errs.append(f"欄位 {fid} 沒有選項")
                if len(vals) != len(set(vals)):
                    errs.append(f"欄位 {fid} 的選項值重複")
                if t == "single" and "default" in f and f["default"] not in vals:
                    errs.append(f"欄位 {fid} 的預設值不在選項裡：{f['default']}")
                if t == "multi" and "default" in f and not set(f["default"]) <= set(vals):
                    errs.append(f"欄位 {fid} 的預設值不在選項裡：{f['default']}")
    if not d.get("questions"):
        errs.append("沒有任何題目")
    for fid, f in fields.items():
        si = f.get("showIf")
        if not si:
            continue
        src = fields.get(si.get("field"))
        if not src:
            errs.append(f"欄位 {fid} 的 showIf 指到不存在的欄位：{si.get('field')}")
        elif src.get("type") == "single":
            bad = [v for v in si.get("in", []) if v not in [o["v"] for o in src["options"]]]
            if bad:
                errs.append(f"欄位 {fid} 的 showIf 用了不存在的選項：{bad}")
    return errs, fields


def check_answers(where, ans, fields):
    errs = []
    if not isinstance(ans, dict) or not ans:
        return [f"{where} 沒有 answers"]
    for k, v in ans.items():
        f = fields.get(k)
        if not f:
            errs.append(f"{where} 用了不存在的欄位：{k}")
            continue
        t = f["type"]
        vals = [o["v"] for o in f.get("options", [])]
        if t == "single" and v not in vals:
            errs.append(f"{where} 的 {k} 不是有效選項：{v}")
        elif t == "multi":
            if not isinstance(v, list):
                errs.append(f"{where} 的 {k} 要是清單")
            else:
                bad = [x for x in v if x not in vals]
                if bad:
                    errs.append(f"{where} 的 {k} 有無效選項：{bad}")
        elif t in ("text", "textarea") and not isinstance(v, str):
            errs.append(f"{where} 的 {k} 要是文字")
        si = f.get("showIf")
        # showIf 指到不存在的欄位已在 check_def 報過，這裡不重複報、也不能因此整支當掉
        if si and si.get("field") in fields:
            cur = ans.get(si["field"], fields[si["field"]].get("default"))
            if cur not in si.get("in", []):
                errs.append(f"{where} 填了 {k}，但 {si['field']}={cur} 時這欄不會顯示")
    return errs


def check_ref(where, r, kinds=None):
    errs = []
    url, repo = r.get("url"), r.get("repo")
    if not url and not repo:
        errs.append(f"{where} 沒有網址也沒有 repo")
    if url and not url.startswith("https://"):
        errs.append(f"{where} 的網址不是 https：{url}")
    if repo and not REPO_RE.match(repo):
        errs.append(f"{where} 的 repo 格式要是 帳號/名稱：{repo}")
    if not r.get("why"):
        errs.append(f"{where} 缺 why（為什麼值得看）")
    if kinds is not None:
        if not r.get("name"):
            errs.append(f"{where} 缺 name")
        if r.get("kind") not in kinds:
            errs.append(f"{where} 的種類不在清單裡：{r.get('kind')}")
    return errs


def check_page(html, name="page"):
    errs = []
    try:
        d, tpl, refs = embedded(html, "wiz-def"), embedded(html, "templates"), embedded(html, "refs")
    except (ValueError, json.JSONDecodeError) as e:
        return [f"{name}：內嵌資料讀不到或不是合法 JSON：{e}"]
    e, fields = check_def(d)
    errs += e
    cats, kinds = js_list(html, "cats"), js_list(html, "kinds")
    if cats is None:
        errs.append("找不到範例分類（cats: [...]）")
    if kinds is None:
        errs.append("找不到參考資源種類（kinds: [...]）")
    if len(tpl) < MIN_TEMPLATES:
        errs.append(f"範例只有 {len(tpl)} 個，至少要 {MIN_TEMPLATES} 個")
    ids = set()
    for t in tpl:
        tid = t.get("id", "?")
        if tid in ids:
            errs.append(f"範例 id 重複：{tid}")
        ids.add(tid)
        for k in ("icon", "title", "cat", "desc"):
            if not t.get(k):
                errs.append(f"範例 {tid} 缺 {k}")
        if cats is not None and t.get("cat") not in cats:
            errs.append(f"範例 {tid} 的分類不在清單裡：{t.get('cat')}")
        if t.get("level") not in (1, 2, 3):
            errs.append(f"範例 {tid} 的難度要是 1～3")
        errs += check_answers(f"範例 {tid}", t.get("answers"), fields)
        for i, r in enumerate(t.get("refs") or []):
            errs += check_ref(f"範例 {tid} 的參考 {i + 1}", r)
    if len(refs) < MIN_REFS:
        errs.append(f"參考資源只有 {len(refs)} 個，至少要 {MIN_REFS} 個")
    names = set()
    for r in refs:
        if r.get("name") in names:
            errs.append(f"參考資源重複：{r.get('name')}")
        names.add(r.get("name"))
        errs += check_ref(f"參考「{r.get('name', '?')}」", r, kinds or [])
    # 頁面內連結與路線圖節點：目標改名後按了會沒反應
    page_ids = set(re.findall(r'\sid="([^"]+)"', html))
    for target in sorted(set(re.findall(r'href="#([^"]+)"', html))):
        if target not in page_ids:
            errs.append(f"頁面內連結指到不存在的位置：#{target}")
    steps = set(re.findall(r'<details class="step" id="([^"]+)"', html))
    for node in sorted(set(re.findall(r'data-node="([^"]+)"', html))):
        if node not in steps:
            errs.append(f"路線圖的節點指到不存在的步驟：{node}")
    for sid in sorted(steps):
        m = re.search(r'<details class="step" id="%s"([^>]*)>' % re.escape(sid), html)
        if 'data-title="' not in m.group(1):
            errs.append(f"步驟 {sid} 缺 data-title（進度清單要顯示）")
    return [f"{name}：{x}" for x in errs]


def selftest():
    ok = True

    def t(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok &= bool(cond)

    for p in PAGES:
        with open(os.path.join(HERE, p), encoding="utf-8") as f:
            t(check_page(f.read(), p) == [], f"目前的 {p} 全部有效")

    d = {"questions": [{"id": "q1", "title": "Q", "fields": [
        {"id": "a", "type": "single", "default": "x", "options": [{"v": "x"}, {"v": "y"}]},
        {"id": "m", "type": "multi", "options": [{"v": "m1"}, {"v": "m2"}]},
        {"id": "s", "type": "text", "showIf": {"field": "a", "in": ["y"]}}]}]}
    tpl = [{"id": f"t{i}", "icon": "★", "title": "T", "cat": "c", "desc": "d", "level": 1, "answers": {"a": "y", "m": ["m1"], "s": "ok"}}
           for i in range(MIN_TEMPLATES)]
    refs = [{"name": f"r{i}", "kind": "k", "url": "https://example.com", "repo": "o/n", "why": "w"} for i in range(MIN_REFS)]
    body = ('<a href="#st-a">x</a><button data-node="st-a"></button>'
            '<details class="step" id="st-a" data-step data-title="A"></details>'
            '<script>x({cats: ["c"]}); y({kinds: ["k"]});</script>')

    def page(d=d, tpl=tpl, refs=refs, body=body):
        return (f'<script type="application/json" id="wiz-def">{json.dumps(d)}</script>'
                f'<script type="application/json" id="templates">{json.dumps(tpl)}</script>'
                f'<script type="application/json" id="refs">{json.dumps(refs)}</script>' + body)

    def bad_tpl(**kw):
        x = json.loads(json.dumps(tpl))
        x[0]["answers"].update(kw)
        return check_page(page(tpl=x))

    t(check_page(page()) == [], "正確的頁面通過")
    t(any("不存在的欄位" in e for e in bad_tpl(gone=1)), "範例用了被刪掉的欄位會被擋下")
    t(any("不是有效選項" in e for e in bad_tpl(a="renamed")), "範例用了改名的選項會被擋下")
    t(any("無效選項" in e for e in bad_tpl(m=["m9"])), "多選題的無效值會被擋下")
    t(any("要是清單" in e for e in bad_tpl(m="m1")), "多選題寫成文字會被擋下")
    x = json.loads(json.dumps(tpl))
    x[0]["answers"] = {"s": "ok"}
    t(any("不會顯示" in e for e in check_page(page(tpl=x))), "填了條件不成立、畫面上看不到的欄位會被擋下")
    t(any("至少要" in e for e in check_page(page(tpl=tpl[:3]))), "範例太少會被擋下")
    x = json.loads(json.dumps(tpl))
    x[1]["id"] = "t0"
    t(any("id 重複" in e for e in check_page(page(tpl=x))), "範例 id 重複會被擋下")
    x = json.loads(json.dumps(tpl))
    x[0]["cat"] = "nope"
    t(any("分類不在清單" in e for e in check_page(page(tpl=x))), "分類打錯會被擋下")
    x = json.loads(json.dumps(tpl))
    x[0]["level"] = 5
    t(any("難度" in e for e in check_page(page(tpl=x))), "難度超出 1～3 會被擋下")
    r = json.loads(json.dumps(refs))
    r[0]["url"] = "http://example.com"
    t(any("不是 https" in e for e in check_page(page(refs=r))), "http 網址會被擋下")
    r = json.loads(json.dumps(refs))
    r[0]["repo"] = "https://github.com/o/n"
    t(any("帳號/名稱" in e for e in check_page(page(refs=r))), "repo 寫成網址會被擋下")
    r = json.loads(json.dumps(refs))
    r[0]["kind"] = "nope"
    t(any("種類不在清單" in e for e in check_page(page(refs=r))), "參考資源種類打錯會被擋下")
    d2 = json.loads(json.dumps(d))
    d2["questions"][0]["fields"][0]["default"] = "z"
    t(any("預設值不在選項" in e for e in check_page(page(d=d2))), "預設值不在選項裡會被擋下")
    d2 = json.loads(json.dumps(d))
    d2["questions"][0]["fields"][2]["showIf"]["field"] = "nope"
    t(any("showIf 指到不存在" in e for e in check_page(page(d=d2))), "showIf 指到不存在的欄位會被擋下")
    t(any("#st-gone" in e for e in check_page(page(body=body + '<a href="#st-gone">x</a>'))), "頁面內連結指到不存在的位置會被擋下")
    t(any("st-gone" in e for e in check_page(page(body=body + '<button data-node="st-gone"></button>'))), "路線圖節點指到不存在的步驟會被擋下")
    t(any("data-title" in e for e in check_page(page(body=body + '<details class="step" id="st-b" data-step></details>'))), "步驟缺標題會被擋下")
    t(any("讀不到" in e for e in check_page("<html></html>")), "沒有內嵌資料會被擋下")
    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv):
    ap = argparse.ArgumentParser(description="檢查教學頁（dev-guide、agent）的題目、範例、參考資源與頁面內連結")
    ap.add_argument("cmd", nargs="?", choices=["selftest"], help="selftest：離線自我測試")
    a = ap.parse_args(argv[1:])
    if a.cmd == "selftest":
        return selftest()
    errs = []
    for p in PAGES:
        try:
            with open(os.path.join(HERE, p), encoding="utf-8") as f:
                errs += check_page(f.read(), p)
        except OSError as e:
            errs.append(f"{p}：讀不到檔案：{e}")
    for e in errs:
        print("✗ " + e)
    print("教學頁資料檢查：" + ("通過" if not errs else f"{len(errs)} 個問題"))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
