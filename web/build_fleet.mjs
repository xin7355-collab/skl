#!/usr/bin/env node
/**
 * 每天自動健檢「我的 App」→ fleet.json（Repo X-Ray「我的 App」打開就看得到，不用按、不花手機的額度）。
 *
 * 為什麼用無頭瀏覽器跑 xray.html：健檢規則與判斷（analyzeLite、advisor_rules.json）只有 xray.html 一份，
 * 這裡直接呼叫同一支函式，⛔ 不另寫一份 Python 版（兩份一定會不一致）。
 *
 * ⛔ 只輸出數量（分數、幾項待補強、建議裝幾個技能、已裝幾個），不輸出問題內容：
 *    網站是公開的，「哪個 App 有什麼弱點」不能放在公開的地方（CLAUDE.md：公開的通知只寫數量）。
 *    要看內容，在 X-Ray 點那個 App，用自己的額度即時查。
 *
 * 用法：node web/build_fleet.mjs --site _site --out _site/fleet.json [--prev URL]
 *       環境變數 GITHUB_TOKEN（必要）、SKL_READ_TOKEN（選用：有它才掃私人 App，沒設定就標「略過」，不算失敗）
 *       node web/build_fleet.mjs selftest
 * 結束碼：有掃到至少一個 App → 0（部分失敗會列出來、印 ::warning::）；全部失敗 → 1。
 */
import fs from 'fs';
import http from 'http';
import path from 'path';
import { fileURLToPath } from 'url';

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > -1 ? process.argv[i + 1] : d; };

/** 合併上一次的結果算增減（純函式，selftest 測） */
export function withPrev(cur, prev) {
  const pm = new Map(((prev && prev.apps) || []).map((a) => [a.repo, a]));
  return cur.map((a) => {
    const p = pm.get(a.repo);
    return p ? { ...a, prev_score: p.score, prev_issues: p.issues, prev_at: prev.at } : a;
  });
}

/** 只留數量：就算 analyzeLite 回傳了問題內容，也不會寫進公開的檔案 */
export function countsOnly(r) {
  return { repo: r.full, score: +r.score || 0, issues: (r.issues || []).length, want: (r.want || []).length, installed: +r.installed || 0 };
}

function serve(dir) {
  const types = { '.html': 'text/html; charset=utf-8', '.json': 'application/json', '.js': 'text/javascript', '.css': 'text/css' };
  const srv = http.createServer((req, res) => {
    const p = path.join(dir, decodeURIComponent(req.url.split('?')[0]).replace(/^\/+/, '') || 'index.html');
    if (!p.startsWith(path.resolve(dir)) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'content-type': types[path.extname(p)] || 'application/octet-stream' });
    fs.createReadStream(p).pipe(res);
  });
  return new Promise((ok) => srv.listen(0, '127.0.0.1', () => ok(srv)));
}

async function fetchPrev(url) {
  if (!url) return null;
  try { const r = await fetch(url, { signal: AbortSignal.timeout(15000) }); return r.ok ? await r.json() : null; } catch (e) { return null; }
}

async function run() {
  const site = path.resolve(arg('--site', '_site')), out = arg('--out', path.join(site, 'fleet.json'));
  const token = process.env.SKL_READ_TOKEN || process.env.GITHUB_TOKEN || '';
  if (!token) { console.error('::error::沒有 GITHUB_TOKEN：未登入每小時只有 60 次，不跑'); return 1; }
  const privateOk = !!process.env.SKL_READ_TOKEN;
  const { chromium } = await import(process.env.PW_MODULE || '@playwright/test');
  const srv = await serve(site);
  const base = `http://127.0.0.1:${srv.address().port}/`;
  const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
  let result;
  try {
    const page = await browser.newPage();
    // Token 只放進這台機器上的瀏覽器（localStorage），X-Ray 照平常一樣用標頭送給 api.github.com
    await page.addInitScript((t) => { try { localStorage.setItem('xray-token-v1', t); } catch (e) {} }, token);
    await page.goto(base + 'xray.html?tab=mine');
    await page.waitForFunction(() => Array.isArray(S.apps) && S.apps.length > 0, null, { timeout: 30000 });
    result = await page.evaluate(async (privOk) => {
      ADV = ADV || await loadStatic('advisor_rules.json');
      if (!ADV) return { fatal: 'advisor_rules.json 載入失敗' };
      const ok = [], failed = [], skipped = [], empty = [];
      for (const a of S.apps) {
        if (a.private && !privOk) { skipped.push(a.repo); continue; }
        try { ok.push(await analyzeLite(a.repo)); } catch (e) {
          const m = String((e && e.message) || e);
          // GitHub 對「還沒有任何 commit 的空 repo」回 409：還沒開始寫，不是壞掉
          if (/409/.test(m)) empty.push(a.repo); else failed.push([a.repo, m.slice(0, 80)]);
        }
      }
      return { ok, failed, skipped, empty };
    }, privateOk);
  } finally { await browser.close(); srv.close(); }
  if (result.fatal) { console.error('::error::' + result.fatal); return 1; }
  const prev = await fetchPrev(arg('--prev'));
  const apps = withPrev(result.ok.map(countsOnly), prev).sort((x, y) => y.score - x.score);
  const data = { at: new Date().toISOString(), apps, skipped: result.skipped, empty: result.empty, failed: result.failed };
  fs.writeFileSync(out, JSON.stringify(data, null, 1));
  console.log(`🩺 自動健檢：${apps.length} 個 App、略過 ${result.skipped.length} 個（私人、沒設 SKL_READ_TOKEN）、空 repo ${result.empty.length} 個、失敗 ${result.failed.length} 個 → ${out}`);
  if (result.failed.length) console.log(`::warning::${result.failed.length} 個 App 健檢失敗：${result.failed.map((f) => f[0].split('/').pop()).join('、')}`);
  return apps.length ? 0 : 1;
}

function selftest() {
  const fails = [];
  const ok = (n, c) => { console.log((c ? '✅ ' : '❌ ') + n); if (!c) fails.push(n); };
  const c = countsOnly({ full: 'a/b', score: 7, issues: [{ title: '金鑰外洩風險' }, { title: 'x' }], want: ['p1'], installed: 3 });
  ok('只留數量：問題內容不會寫進公開檔案', JSON.stringify(c) === JSON.stringify({ repo: 'a/b', score: 7, issues: 2, want: 1, installed: 3 })
    && !JSON.stringify(c).includes('金鑰'));
  const w = withPrev([{ repo: 'a/b', score: 7, issues: 2 }, { repo: 'a/new', score: 1, issues: 0 }],
    { at: '2026-10-30T00:00:00Z', apps: [{ repo: 'a/b', score: 9, issues: 3 }, { repo: 'a/gone', score: 1, issues: 1 }] });
  ok('有上一次 → 帶上一次的分數與問題數', w[0].prev_score === 9 && w[0].prev_issues === 3 && w[0].prev_at === '2026-10-30T00:00:00Z');
  ok('新加的 App 沒有上一次 → 不亂補', w[1].prev_score === undefined);
  ok('上一次沒有資料 → 原樣', withPrev([{ repo: 'x', score: 1 }], null)[0].prev_score === undefined);
  console.log(fails.length ? `❌ ${fails.length} 條沒過` : '✅ build_fleet selftest 通過');
  return fails.length ? 1 : 0;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (process.argv[2] === 'selftest') process.exit(selftest());
  else if (process.argv[2] === '--help' || process.argv[2] === '-h') { console.log('用法：node web/build_fleet.mjs --site _site --out _site/fleet.json [--prev URL]；node web/build_fleet.mjs selftest'); process.exit(0); }
  else run().then((c) => process.exit(c), (e) => { console.error('::error::自動健檢失敗：' + (e && e.message)); process.exit(1); });
}
