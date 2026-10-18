#!/usr/bin/env python3
"""build_zips.py — 把每個技能打包成 ZIP，給 claude.ai 網頁版「上傳技能」用（部署時產生到 _site/zips/）。

為什麼要做：使用者只用 iPhone，claude.ai 網頁版不能從 GitHub 裝技能，只能上傳 ZIP；
手機上沒辦法自己把資料夾壓成 ZIP，所以部署網站時先把每個技能壓好放上去，點一下就能下載。

做法參考 marketingskills 的 scripts/package_skills.py
（https://github.com/coreyhaines31/marketingskills，MIT License，Copyright (c) 2025 Corey Haines）：
固定時間戳與排序讓每次產出一模一樣、排除 evals／__pycache__、路徑安全檢查、ZIP 根資料夾＝技能名稱、
附 LICENSE 與 sha256 清單。改成讀工作目錄（部署時本來就是乾淨的 checkout），並配合 skl 的多來源授權。

claude.ai 的限制（官方說明 https://support.claude.com/en/articles/12512198-how-to-create-custom-skills ，
2026-10-18 查證）：ZIP 裡要有一個跟技能名稱同名的資料夾、name 最多 64 字元、description 最多 200 字元。
大部分技能的 description 超過 200 字元，所以 ZIP 裡的 SKILL.md 會把 description 縮成句子邊界內的
200 字元，原文整段放在內文第一段，Claude 讀到技能時仍看得到完整觸發條件；repo 裡的原檔不動。

用法：
  python3 web/build_zips.py --out _site/zips --catalog web/catalog.json
      # 依 catalog.json 的技能清單打包，並在 catalog.json 每個技能加上 "zip" 下載路徑
  python3 web/build_zips.py selftest

只用標準函式庫；一次只把一個技能讀進記憶體。
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import build_catalog  # noqa: E402  共用同一套 frontmatter 解析，網頁顯示與打包看到的描述才一致

DESC_LIMIT = 200          # claude.ai 官方：description 最多 200 字元
NAME_LIMIT = 64           # claude.ai 官方：name 最多 64 字元
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_FILE_BYTES = 10_000_000     # 單一檔案超過就不收（記在 manifest），避免把大型素材塞進每個 ZIP
MAX_SKILL_BYTES = 30_000_000    # 單一技能解壓後總大小上限；超過整個技能略過（記在 manifest）
EXCLUDED_DIRS = {"evals", "__pycache__", "node_modules", ".git"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo")
LICENSE_RE = re.compile(r"^(licen[cs]e|copying)([.-].*)?$", re.I)
ZIP_TIME = (1980, 1, 1, 0, 0, 0)  # ZIP 格式最早可表示的時間；固定下來才每次產出同樣的位元組
# 這些資料夾底下的每個技能各自帶授權（來源不同），找不到時不能拿 repo 根目錄的授權頂替。
OWN_LICENSE_PREFIXES = ("community/skills/",)


def u16len(text):
    """claude.ai 是網頁（JavaScript），字串長度以 UTF-16 計；用同一種算法，emoji 才不會算少。"""
    return len(text.encode("utf-16-le")) // 2


def shorten(desc, limit=DESC_LIMIT):
    """縮到 limit 字元內，盡量停在句子結尾；整句都太長才在逗號或空白處截斷並加「…」。"""
    desc = " ".join(desc.split())
    if u16len(desc) <= limit:
        return desc
    out = ""
    for m in re.finditer(r".+?(?:[.!?](?=\s)|[。！？；]|$)\s*", desc):
        cand = out + m.group(0)
        if u16len(cand.strip()) > limit:
            break
        out = cand
    out = out.strip()
    if out:
        return out
    cut = desc
    while u16len(cut) > limit - 1:
        cut = cut[:-1]
    m = re.search(r".*[\s，、,;；：:]", cut)
    if m and u16len(m.group(0)) >= limit // 2:
        cut = m.group(0)
    return cut.rstrip(" ，、,;；：:") + "…"


def split_frontmatter(text):
    """回傳 (frontmatter 行, 內文起點行號, 換行字元)；沒有 frontmatter 回 None。"""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, min(len(lines), 402)):
        if lines[i].strip() == "---":
            return lines, i, nl
    return None


def replace_description(text, new_desc, original):
    """只改 frontmatter 的 description 那一項（含它的續行），其他位元組不動；原文放在內文第一段。"""
    parts = split_frontmatter(text)
    if not parts:
        raise ValueError("SKILL.md 沒有 frontmatter")
    lines, end, nl = parts
    idx = [i for i in range(1, end) if re.match(r"^description\s*:", lines[i])]
    if len(idx) != 1:
        raise ValueError("SKILL.md 的 description 不是剛好一個")
    i = idx[0]
    j = i + 1
    while j < end and (lines[j][:1] in (" ", "\t") or not lines[j].strip()):
        j += 1
    # JSON 字串剛好也是合法的 YAML 雙引號字串，引號、冒號、中文都不用另外跳脫。
    lines[i:j] = ["description: " + json.dumps(new_desc, ensure_ascii=False)]
    end -= (j - i) - 1
    note = ["", "> **Full description** (shortened in the frontmatter to fit claude.ai's "
            f"{DESC_LIMIT}-character limit): " + " ".join(original.split()), ""]
    lines[end + 1:end + 1] = note
    return nl.join(lines)


def find_license(skill_dir, rel, root):
    """回傳 (授權檔路徑或 None, 說明)。技能資料夾自己有就不另外附。"""
    for name in sorted(os.listdir(skill_dir)):
        if LICENSE_RE.match(name) and os.path.isfile(os.path.join(skill_dir, name)):
            return None, "skill"
    if rel.startswith(OWN_LICENSE_PREFIXES):
        return None, "missing"
    d = os.path.dirname(skill_dir)
    root = os.path.abspath(root)
    while True:
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if LICENSE_RE.match(name) and os.path.isfile(p) and not os.path.islink(p):
                return p, os.path.relpath(p, root).replace(os.sep, "/")
        if os.path.abspath(d) == root:
            return None, "missing"
        d = os.path.dirname(d)


def collect_files(skill_dir):
    """回傳 (要收的 [(ZIP 內相對路徑, 實際路徑)], 不收的 [(相對路徑, 原因)])。"""
    keep, dropped = [], []
    real_root = os.path.realpath(skill_dir)
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        rel_dir = os.path.relpath(dirpath, skill_dir)
        kept_dirs = []
        for d in sorted(dirnames):
            full = os.path.join(dirpath, d)
            if d in EXCLUDED_DIRS or d.startswith("."):
                continue
            if os.path.islink(full):
                dropped.append((os.path.normpath(os.path.join(rel_dir, d)).replace(os.sep, "/"), "符號連結"))
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs
        for f in sorted(filenames):
            full = os.path.join(dirpath, f)
            rel = os.path.normpath(os.path.join(rel_dir, f)).replace(os.sep, "/")
            if f.startswith(".") or f.endswith(EXCLUDED_SUFFIXES):
                continue
            parts = rel.split("/")
            # 跟上游一樣擋掉怪路徑：解壓時 .. 或磁碟代號可能寫到資料夾外面。
            if "\\" in rel or ":" in rel or any(p in ("", ".", "..") for p in parts):
                dropped.append((rel, "不安全的路徑"))
                continue
            if os.path.islink(full):
                dropped.append((rel, "符號連結"))
                continue
            real = os.path.realpath(full)
            if os.path.commonpath([real, real_root]) != real_root or not os.path.isfile(real):
                dropped.append((rel, "不在技能資料夾內"))
                continue
            size = os.path.getsize(full)
            if size > MAX_FILE_BYTES:
                dropped.append((rel, f"檔案太大（{size} bytes）"))
                continue
            keep.append((rel, full))
    keep.sort()
    return keep, dropped


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def pack_skill(root, rel):
    """打包一個技能。成功回傳 (zip 位元組, 說明 dict)；不能打包時丟 ValueError（訊息說明原因）。"""
    skill_dir = os.path.join(root, rel)
    skill_md = os.path.join(skill_dir, "SKILL.md")
    fm = build_catalog.read_frontmatter(skill_md)
    name = fm.get("name", "")
    if not name:
        raise ValueError("SKILL.md 沒有 name")
    if not NAME_RE.fullmatch(name) or len(name) > NAME_LIMIT:
        raise ValueError(f"name「{name}」不符合 claude.ai 規定（小寫英數與連字號、最多 {NAME_LIMIT} 字元）")
    desc = fm.get("description", "").replace('\\"', '"')
    if not desc.strip():
        raise ValueError("SKILL.md 沒有 description")
    files, dropped = collect_files(skill_dir)
    if "SKILL.md" not in {f for f, _ in files}:
        raise ValueError("SKILL.md 本身被排除（太大或是符號連結），無法打包")
    lic_path, lic_src = find_license(skill_dir, rel, root)
    total = sum(os.path.getsize(p) for _, p in files) + (os.path.getsize(lic_path) if lic_path else 0)
    if total > MAX_SKILL_BYTES:
        raise ValueError(f"解壓後 {total} bytes，超過上限 {MAX_SKILL_BYTES}")
    entries = []
    shortened = False
    for zrel, path in files:
        with open(path, "rb") as fh:
            data = fh.read()
        if zrel == "SKILL.md":
            short = shorten(desc)
            if short != " ".join(desc.split()):
                data = replace_description(data.decode("utf-8"), short, desc).encode("utf-8")
                shortened = True
        mode = 0o755 if os.stat(path).st_mode & stat.S_IXUSR else 0o644
        entries.append((zrel, data, mode))
    if lic_path:
        with open(lic_path, "rb") as fh:
            entries.append(("LICENSE", fh.read(), 0o644))
    elif lic_src == "missing":
        notice = ("This skill folder has no LICENSE file of its own. "
                  f"Declared license in SKILL.md: {fm.get('license') or 'not stated'}. "
                  "Source, version and license notes are recorded in the skl repository "
                  "(community/README.md for community skills).\n")
        entries.append(("LICENSE-NOTICE.txt", notice.encode("utf-8"), 0o644))
    entries.sort()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for zrel, data, mode in entries:
            info = zipfile.ZipInfo(f"{name}/{zrel}", date_time=ZIP_TIME)
            info.create_system = 3  # Unix：權限位元才有意義，不同機器打包結果也一樣
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data, compresslevel=9)
    blob = stream.getvalue()
    info = {"path": rel, "name": name, "sha256": sha256(blob), "bytes": len(blob),
            "uncompressed_bytes": sum(len(d) for _, d, _ in entries), "files": len(entries),
            "license": lic_src, "description_shortened": shortened}
    if name != os.path.basename(rel):
        info["folder_renamed_from"] = os.path.basename(rel)
    if dropped:
        info["omitted_files"] = [{"file": f, "reason": why} for f, why in dropped]
    return blob, info


def build(root, out, skill_paths, href_prefix="zips/"):
    """全部打包到暫存資料夾，成功才換成 out；回傳 manifest。一個都打包不了就丟 ValueError。"""
    out = os.path.abspath(out)
    parent = os.path.dirname(out)
    os.makedirs(parent, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=".zips-", dir=parent)
    try:
        bundles, skipped = [], []
        for rel in sorted(skill_paths):
            try:
                blob, info = pack_skill(root, rel)
            except (ValueError, OSError, UnicodeDecodeError) as e:
                skipped.append({"path": rel, "reason": str(e)})
                continue
            zpath = os.path.join(stage, *rel.split("/")) + ".zip"
            os.makedirs(os.path.dirname(zpath), exist_ok=True)
            with open(zpath, "wb") as fh:
                fh.write(blob)
            info["zip"] = href_prefix + rel + ".zip"
            bundles.append(info)
        if not bundles:
            raise ValueError(f"{len(skipped)} 個技能全部打包失敗，不產生空的下載區")
        sums = "".join(f"{b['sha256']}  {b['path']}.zip\n" for b in bundles)
        with open(os.path.join(stage, "SHA256SUMS"), "w", encoding="utf-8") as fh:
            fh.write(sums)
        manifest = {"format_version": 1, "description_limit": DESC_LIMIT,
                    "total_bytes": sum(b["bytes"] for b in bundles),
                    "bundles": bundles, "skipped": skipped}
        with open(os.path.join(stage, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        if os.path.isdir(out):
            shutil.rmtree(out)
        os.replace(stage, out)
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def annotate_catalog(catalog_path, manifest):
    """在 catalog.json 每個打包成功的技能加 "zip"；網頁只對有這欄的技能顯示下載連結，不會連到 404。"""
    with open(catalog_path, encoding="utf-8") as fh:
        cat = json.load(fh)
    by = {b["path"]: b["zip"] for b in manifest["bundles"]}
    for s in cat["skills"]:
        if s["path"] in by:
            s["zip"] = by[s["path"]]
        else:
            s.pop("zip", None)
    with open(catalog_path, "w", encoding="utf-8") as fh:
        json.dump(cat, fh, ensure_ascii=False, separators=(",", ":"))


def human(n):
    return f"{n / 1_000_000:.1f} MB"


def selftest():
    global MAX_FILE_BYTES, MAX_SKILL_BYTES
    ok = True
    # 上限調小，測「太大」的情況時不必真的寫出幾十 MB 的檔案。
    saved = (MAX_FILE_BYTES, MAX_SKILL_BYTES)
    MAX_FILE_BYTES, MAX_SKILL_BYTES = 5000, 12000
    try:
        return _selftest_body()
    finally:
        MAX_FILE_BYTES, MAX_SKILL_BYTES = saved


def _selftest_body():
    ok = True

    def check(cond, name):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    long_en = ("Use this skill when the user wants a detailed report. " * 3
               + "It also covers charts, tables and exports for many formats.")
    long_zh = "這是一段很長的中文說明，" * 30 + "結尾。"
    with tempfile.TemporaryDirectory() as d:
        def w(rel, text="x", mode=None):
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            if mode:
                os.chmod(p, mode)

        w("LICENSE", "ROOT LICENSE\n")
        w("eng/skills/alpha/SKILL.md", "---\nname: alpha\ndescription: Short one.\nlicense: MIT\n---\nBody\n")
        w("eng/skills/alpha/scripts/run.py", "print(1)\n", 0o755)
        w("eng/skills/alpha/references/a.md", "ref\n")
        w("eng/skills/alpha/data/table.csv", "a,b\n")
        w("eng/skills/alpha/evals/evals.json", "{}")
        w("eng/skills/alpha/scripts/__pycache__/run.cpython-311.pyc", "bin")
        w("eng/skills/alpha/node_modules/x/index.js", "x")
        w("eng/skills/alpha/.DS_Store", "x")
        w("eng/skills/alpha/assets/big.bin", "0" * (MAX_FILE_BYTES + 1))
        os.symlink(os.path.join(d, "LICENSE"), os.path.join(d, "eng/skills/alpha/assets/link.txt"))
        w("eng/skills/beta/SKILL.md", f"---\nname: beta\ndescription: >\n  {long_en}\nmetadata:\n  v: 1\n---\n# Beta\n")
        w("eng/skills/zh/SKILL.md", f"---\r\nname: zh-skill\r\ndescription: \"{long_zh}\"\r\n---\r\n內文\r\n")
        w("grp/LICENSE", "GROUP LICENSE\n")
        w("grp/skills/gamma/SKILL.md", "---\nname: gamma\ndescription: g\n---\n")
        w("community/skills/own/SKILL.md", "---\nname: own\ndescription: o\n---\n")
        w("community/skills/own/LICENSE", "OWN LICENSE\n")
        w("community/skills/nolic/SKILL.md", "---\nname: nolic\ndescription: n\nlicense: MIT\n---\n")
        w("eng/skills/Bad_Name/SKILL.md", "---\nname: Bad_Name\ndescription: b\n---\n")
        w("eng/skills/nodesc/SKILL.md", "---\nname: nodesc\n---\n")
        w("eng/skills/bigmd/SKILL.md", "---\nname: bigmd\ndescription: b\n---\n" + "x" * MAX_FILE_BYTES)
        w("eng/skills/huge/SKILL.md", "---\nname: huge\ndescription: h\n---\n")
        for i in range(4):
            w(f"eng/skills/huge/assets/part{i}.bin", "0" * (MAX_FILE_BYTES - 10))
        w("eng/skills/pw/SKILL.md", "---\nname: playwright-pro\ndescription: p\n---\n")

        paths = ["eng/skills/alpha", "eng/skills/beta", "eng/skills/zh", "grp/skills/gamma",
                 "community/skills/own", "community/skills/nolic", "eng/skills/Bad_Name",
                 "eng/skills/nodesc", "eng/skills/huge", "eng/skills/pw", "eng/skills/bigmd"]
        out1, out2 = os.path.join(d, "o1", "zips"), os.path.join(d, "o2", "zips")
        m1 = build(d, out1, paths)
        m2 = build(d, out2, list(reversed(paths)))
        by = {b["path"]: b for b in m1["bundles"]}
        sk = {s["path"]: s["reason"] for s in m1["skipped"]}

        def names(rel):
            with zipfile.ZipFile(os.path.join(out1, *rel.split("/")) + ".zip") as zf:
                return zf.namelist(), zf

        def read(rel, member):
            with zipfile.ZipFile(os.path.join(out1, *rel.split("/")) + ".zip") as zf:
                return zf.read(member).decode("utf-8")

        same = all(open(os.path.join(out1, *p.split("/")) + ".zip", "rb").read()
                   == open(os.path.join(out2, *p.split("/")) + ".zip", "rb").read() for p in by)
        check(same and open(os.path.join(out1, "SHA256SUMS")).read() == open(os.path.join(out2, "SHA256SUMS")).read(),
              "同樣的內容打包兩次，ZIP 與 sha256 清單位元組完全相同（順序不同也一樣）")
        alpha, _ = names("eng/skills/alpha")
        check(set(alpha) == {"alpha/SKILL.md", "alpha/scripts/run.py", "alpha/references/a.md",
                             "alpha/data/table.csv", "alpha/LICENSE"},
              "只收技能內容，排除 evals、__pycache__、node_modules、隱藏檔、符號連結、大檔")
        om = {o["file"]: o["reason"] for o in by["eng/skills/alpha"].get("omitted_files", [])}
        check("assets/big.bin" in om and "assets/link.txt" in om, "被略過的大檔與符號連結記在 manifest，不會默默消失")
        with zipfile.ZipFile(os.path.join(out1, "eng", "skills", "alpha.zip")) as zf:
            infos = {i.filename: i for i in zf.infolist()}
        check(all(i.date_time == ZIP_TIME for i in infos.values()), "每個檔案的時間戳固定")
        check((infos["alpha/scripts/run.py"].external_attr >> 16) & 0o777 == 0o755
              and (infos["alpha/SKILL.md"].external_attr >> 16) & 0o777 == 0o644, "保留可執行權限，其他一律 644")
        check(all(n.startswith("alpha/") and ".." not in n.split("/") for n in alpha), "ZIP 根目錄是技能名稱資料夾，沒有 ..")
        check(read("eng/skills/alpha", "alpha/SKILL.md").startswith("---\nname: alpha\ndescription: Short one.\n"),
              "description 夠短的 SKILL.md 原封不動")
        check(read("eng/skills/alpha", "alpha/LICENSE") == "ROOT LICENSE\n", "上游技能附 repo 根目錄的 LICENSE")
        check(read("grp/skills/gamma", "gamma/LICENSE") == "GROUP LICENSE\n", "群組（ecc、superpowers）附最近一層的 LICENSE")
        own, _ = names("community/skills/own")
        check(own == ["own/LICENSE", "own/SKILL.md"] and read("community/skills/own", "own/LICENSE") == "OWN LICENSE\n",
              "社群技能用自己的 LICENSE，不重複附")
        nolic, _ = names("community/skills/nolic")
        check("nolic/LICENSE" not in nolic and "nolic/LICENSE-NOTICE.txt" in nolic
              and "MIT" in read("community/skills/nolic", "nolic/LICENSE-NOTICE.txt"),
              "社群技能沒有 LICENSE 時附說明，不拿 repo 根目錄的授權頂替")
        beta = read("eng/skills/beta", "beta/SKILL.md")
        fm = build_catalog.read_frontmatter
        tmp = os.path.join(d, "beta_check.md")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(beta)
        bdesc = fm(tmp)["description"]
        check(by["eng/skills/beta"]["description_shortened"] and u16len(bdesc) <= DESC_LIMIT
              and bdesc.endswith(".") and "metadata:\n  v: 1\n---" in beta,
              "description 太長時縮到 200 字元內、停在句尾，其他 frontmatter 不動")
        check(" ".join(long_en.split()) in beta and beta.index("Full description") > beta.index("\n---\n"),
              "原本完整的 description 放在內文第一段")
        zh = read("eng/skills/zh", "zh-skill/SKILL.md")
        check("\r\n" in zh and zh.count("---\r\n") == 2, "CRLF 的檔案維持 CRLF")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(zh)
        zdesc = fm(tmp)["description"]
        check(u16len(zdesc) <= DESC_LIMIT and zdesc.endswith("…") and "，" not in zdesc[-2:],
              "中文長句在逗號處截斷並加「…」")
        check(shorten("😀" * 150) == "😀" * 99 + "…", "emoji 以 UTF-16 長度計算（跟網頁一樣）")
        check(by["eng/skills/pw"]["name"] == "playwright-pro"
              and names("eng/skills/pw")[0][0].startswith("playwright-pro/")
              and by["eng/skills/pw"]["folder_renamed_from"] == "pw",
              "資料夾名稱跟 name 不同時，ZIP 裡的資料夾改用 name（claude.ai 要求一致）並記下")
        check(set(sk) == {"eng/skills/Bad_Name", "eng/skills/nodesc", "eng/skills/huge", "eng/skills/bigmd"}
              and "claude.ai" in sk["eng/skills/Bad_Name"] and "上限" in sk["eng/skills/huge"]
              and "SKILL.md" in sk["eng/skills/bigmd"],
              "不合規定的名稱、沒有描述、太大、SKILL.md 本身太大的技能會略過並說明原因")
        lines = open(os.path.join(out1, "SHA256SUMS")).read().splitlines()
        check(len(lines) == len(by) and all(
            sha256(open(os.path.join(out1, *ln.split("  ", 1)[1].split("/")), "rb").read()) == ln.split("  ", 1)[0]
            for ln in lines), "sha256 清單跟實際檔案一致")
        check(m1["total_bytes"] == sum(b["bytes"] for b in by.values()), "manifest 記總大小")
        check(not [n for n in os.listdir(os.path.join(d, "o1")) if n.startswith(".zips-")], "不留暫存資料夾")

        cat_path = os.path.join(d, "catalog.json")
        with open(cat_path, "w", encoding="utf-8") as fh:
            json.dump({"skills": [{"path": p} for p in paths] + [{"path": "x/old", "zip": "zips/x/old.zip"}]}, fh)
        annotate_catalog(cat_path, m1)
        cat = {s["path"]: s for s in json.load(open(cat_path, encoding="utf-8"))["skills"]}
        check(cat["eng/skills/alpha"]["zip"] == "zips/eng/skills/alpha.zip" and "zip" not in cat["eng/skills/Bad_Name"]
              and "zip" not in cat["x/old"], "catalog.json 只有打包成功的技能有下載路徑（舊的會清掉）")

        out3 = os.path.join(d, "o3", "zips")
        try:
            build(d, out3, ["eng/skills/Bad_Name", "eng/skills/nodesc"])
            failed_all = False
        except ValueError as e:
            failed_all = "全部打包失敗" in str(e)
        check(failed_all and not os.path.exists(out3), "全部失敗時報錯，不產生空的下載區")

    print("\n自我測試" + ("全部通過" if ok else "有失敗"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="把技能打包成 claude.ai 可上傳的 ZIP")
    ap.add_argument("cmd", nargs="?", default="build", choices=["build", "selftest"])
    ap.add_argument("--out", default=os.path.join(ROOT, "_site", "zips"), help="輸出資料夾（會整個換掉）")
    ap.add_argument("--catalog", default=os.path.join(ROOT, "web", "catalog.json"),
                    help="技能清單來源；打包後在每個技能加上 zip 路徑")
    ap.add_argument("--href-prefix", default="zips/", help="網頁上下載連結的前綴（相對於網站根目錄）")
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    if not os.path.isfile(args.catalog):
        print(f"找不到 {args.catalog}：先跑 python3 web/build_catalog.py 產生技能清單。", file=sys.stderr)
        return 1
    with open(args.catalog, encoding="utf-8") as fh:
        paths = [s["path"] for s in json.load(fh)["skills"]]
    try:
        manifest = build(ROOT, args.out, paths, args.href_prefix)
    except ValueError as e:
        print(f"打包失敗：{e}。看上面每個技能的原因，修好 SKILL.md 再重跑。", file=sys.stderr)
        return 1
    annotate_catalog(args.catalog, manifest)
    n_short = sum(1 for b in manifest["bundles"] if b["description_shortened"])
    print(f"已打包 {len(manifest['bundles'])} 個技能，總大小 {human(manifest['total_bytes'])}"
          f"（GitHub Pages 單站上限 1 GB）；description 縮短 {n_short} 個；略過 {len(manifest['skipped'])} 個。")
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for s in manifest["skipped"]:
        # 個別技能不合規定不擋整個網站部署，但要在 Actions 摘要亮黃燈，不能默默少一個下載連結。
        print(("::warning::" if in_actions else "  ! ") + f"略過 {s['path']}：{s['reason']}")
    for b in manifest["bundles"]:
        for o in b.get("omitted_files", []):
            print(("::warning::" if in_actions else "  ! ") + f"{b['path']} 沒收 {o['file']}：{o['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
