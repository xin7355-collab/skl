// 假的 GitHub／翻譯服務：每個測試的所有對外連線都在這裡回應，沒列到的一律擋掉並記下來。
// 為什麼不直接連真的 GitHub：未登入每小時只有 60 次額度，CI 一跑就用完；而且結果會隨別人的 repo 變動。
const { test: base, expect } = require('@playwright/test');

const now = Date.now();
const iso = (days) => new Date(now - days * 864e5).toISOString();

// 一個典型的自家 PWA：React＋Vite、manifest、CLAUDE.md 寫部署網址、
// 還用 app-bootstrap 裝過技能（.claude/skills 裡有自己的 README 與 SKILL.md）
const PWA_FILES = {
  'README.md': '# Demo PWA\n\nA small stock watchlist PWA.\n\n```\nnpm install\nnpm run dev\n```\n',
  'CLAUDE.md': '# 工作守則\n\n部署網址：https://watchlist-app.xinapp.workers.dev\n',
  'package.json': JSON.stringify({
    name: 'demo-pwa',
    dependencies: { react: '18.3.1', 'react-dom': '18.3.1' },
    devDependencies: { vite: '5.4.0' },
    scripts: { dev: 'vite', build: 'vite build' },
  }),
  'index.html': '<!doctype html><html><head><link rel="manifest" href="manifest.json"></head><body><div id="root"></div></body></html>',
  'public/manifest.json': JSON.stringify({
    name: 'Demo', short_name: 'Demo', display: 'standalone', start_url: '/',
    icons: [{ src: 'i192.png', sizes: '192x192' }, { src: 'i512.png', sizes: '512x512' }],
  }),
  'src/App.jsx': 'export default function App() { return null }\n',
  'src/api.js': "export const load = () => fetch('https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL')\n",
  'tests/app.test.js': "test('ok', () => {})\n",
  'docs/guide.md': '# Guide\n\nHow to deploy this app to GitHub Pages.\n',
  'CODE_OF_CONDUCT.md': '# Code of Conduct\n\nBe kind.\n',
  'LICENSE': 'MIT License\n\nCopyright (c) 2026 demo\n',
  '.github/workflows/deploy.yml': 'name: deploy\n',
  '.claude/skills/helper/README.md': '# Helper skill README SHOULD-NOT-BE-ROOT\n\nInstalled skill docs.\n',
  '.claude/skills/helper/SKILL.md': '---\nname: helper\ndescription: An installed helper skill.\n---\n',
};

const PY_FILES = {
  'README.md': '# PyPkg\n\nA fast math library.\n',
  // FastAPI 只在開發群組：不能因此判成「需要後端伺服器」
  'pyproject.toml': "[project]\nname = 'pypkg'\ndependencies = ['numpy>=1.26']\n\n[dependency-groups]\ndev = ['fastapi', 'pytest']\n",
  'src/pypkg/__init__.py': 'VERSION = "0.1"\n',
  'docs/index.html': '<!doctype html><title>docs</title>',
  'tests/test_basic.py': 'def test_ok():\n    assert True\n',
};

const SKILL_FILES = {
  'README.md': '# Skill pack\n\nTwo agent skills.\n',
  'skills/alpha/SKILL.md': '---\nname: alpha\ndescription: Alpha skill does A.\n---\n',
  'skills/beta/SKILL.md': '---\nname: beta\ndescription: Beta skill does B.\n---\n',
};

// 會踩到敏感檔名判斷的各種情況：該抓的（.npmrc、.git-credentials…）和不該抓的（.env.example、tokenizer.py、token-economics.md）
const SECRET_FILES = {
  'README.md': '# Secrets demo\n\nA small Node tool.\n',
  'package.json': JSON.stringify({ name: 'secrets-demo', dependencies: {} }),
  '.env.example': 'API_KEY=\nDB_URL=\n',
  '.env.production': 'API_KEY=should-not-be-here\n',
  '.npmrc': '//registry.npmjs.org/:_authToken=xxx\n',
  '.pypirc': '[pypi]\nusername = __token__\n',
  '.git-credentials': 'https://user:pass@github.com\n',
  '.netrc': 'machine api.example.com login me password x\n',
  '.htpasswd': 'admin:$apr1$abc\n',
  '.ssh/config': 'Host *\n',
  '.aws/credentials': '[default]\n',
  '.gnupg/pubring.kbx': '',
  'src/tokenizer.py': 'def tokenize(s):\n    return s.split()\n',
  'src/index.js': 'console.log(1)\n',
  'docs/token-economics.md': '# Token economics\n\nWhy prompts cost money.\n',
};

// 有一堆自動產生資料夾的專案：node_modules 檔案最多，照檔案數排會把主程式擠下去
const GEN_FILES = {
  'README.md': '# Gen demo\n\nA web app with build output committed by mistake.\n',
  'package.json': JSON.stringify({ name: 'gen-demo', dependencies: { react: '18.3.1' } }),
  'src/main.js': 'export const main = 1\n',
  'src/lib/util.js': 'export const u = 1\n',
  'src/__snapshots__/main.test.js.snap': '// Jest Snapshot v1\n',
  'dist/index.html': '<!doctype html>',
  'dist/assets/index-4f2a9c.js': '',
  'build/asset-manifest.json': '{}',
  '.next/cache/webpack/client.pack': '',
  '.next/server/app.js': '',
  'storybook-static/index.html': '<!doctype html>',
  'mypkg.egg-info/PKG-INFO': 'Name: mypkg\n',
  'coverage/lcov-report/index.html': '<!doctype html>',
  'coverage/lcov.info': '',
  'venv/pyvenv.cfg': 'home = /usr/bin\n',
  'venv/bin/python': '',
  'venv/lib/python3.12/site-packages/six.py': '',
  // 叫 env 但沒有 pyvenv.cfg：是真的程式碼，不能標成自動產生
  'env/settings.py': 'DEBUG = False\n',
  'backend/app.py': 'app = None\n',
  'backend/.venv/pyvenv.cfg': 'home = /usr/bin\n',
  'backend/.venv/lib/python3.12/site-packages/flask.py': '',
  'certs/app.p12': '', 'certs/ios-push.p8': '', 'certs/win.pfx': '', 'certs/id_ed25519': '', 'certs/id_ed25519.pub': '',
  'certs/service-account-prod.json': '{}',
};
for (let i = 0; i < 30; i++) GEN_FILES[`node_modules/pkg${String(i).padStart(2, '0')}/index.js`] = '';

// 自己的 App（xin7355-collab 帳號），同時命中好幾條同樣急的顧問規則：
// 沒 CLAUDE.md、沒 Actions、有畫面沒測試、爬蟲、呼叫 AI。用來確認每條規則都分得到推薦名額
const BUSY_FILES = {
  'README.md': '# Busy app\n\nA news scraper that summarizes articles with OpenAI.\n',
  'package.json': JSON.stringify({ name: 'busy-app', dependencies: { cheerio: '1.0.0', openai: '4.0.0' } }),
  'index.html': '<!doctype html><html><head><link rel="manifest" href="manifest.json"></head><body></body></html>',
  'manifest.json': JSON.stringify({ name: 'Busy', short_name: 'Busy', display: 'standalone', icons: [{ src: 'i.png', sizes: '192x192' }] }),
  'src/scrape.js': "export const go = () => fetch('/feed')\n",
};

let repoId = 700000;
const meta = (full, extra = {}) => Object.assign({
  id: ++repoId,
  name: full.split('/')[1], full_name: full, description: 'Demo project ' + full,
  default_branch: 'main', stargazers_count: 1234, topics: [], language: 'JavaScript',
  license: { spdx_id: 'MIT' }, homepage: null, has_pages: false, private: false,
  pushed_at: iso(2), created_at: iso(400), size: 2048, html_url: 'https://github.com/' + full,
}, extra);

// commits：總筆數與第一個 commit 是幾天前（「首次 commit」要用）。
// pwa-app 是從別處搬來的（第一個 commit 比 GitHub 建立日早很多），skillpack 只有 1 個 commit（Link 沒有 last）
const REPOS = {
  'demo/pwa-app': { meta: meta('demo/pwa-app'), files: PWA_FILES, commits: { n: 57, firstDays: 1500 } },
  'demo/private-app': { meta: meta('demo/private-app', { private: true }), files: PWA_FILES },
  'demo/pypkg': { meta: meta('demo/pypkg', { language: 'Python' }), files: PY_FILES },
  'demo/skillpack': { meta: meta('demo/skillpack', { language: null }), files: SKILL_FILES, commits: { n: 1, firstDays: 10 } },
  'demo/secrets-app': { meta: meta('demo/secrets-app'), files: SECRET_FILES },
  'demo/gen-app': { meta: meta('demo/gen-app'), files: GEN_FILES },
  'xin7355-collab/busy-app': { meta: meta('xin7355-collab/busy-app'), files: BUSY_FILES },
};

// 照 GitHub 的格式：最新的在前面；作者日期從 firstDays 天前平均排到 2 天前
function commitsOf(repo) {
  const c = repo.commits || { n: 3, firstDays: 420 };
  const out = [];
  for (let k = 0; k < c.n; k++) {
    const days = c.n === 1 ? c.firstDays : 2 + (c.firstDays - 2) * (k / (c.n - 1));
    const date = new Date(now - days * 864e5).toISOString().replace(/\.\d{3}Z$/, 'Z');
    const sha = (repo.meta.id.toString(16) + k.toString(16).padStart(6, '0')).padEnd(40, 'a');
    out.push({
      sha, html_url: repo.meta.html_url + '/commit/' + sha,
      commit: { author: { name: 'dev', email: 'dev@example.com', date }, committer: { name: 'GitHub', email: 'noreply@github.com', date }, message: 'commit ' + k },
    });
  }
  return out;
}

// GitHub 的 Link 標頭指向 /repositories/{數字 id}/…，不是 /repos/owner/name/…：
// 網頁要是直接照著網址走，就會打到下面沒有的路徑、被判 404
function linkHeader(id, params, page, last) {
  const u = (p) => { const q = new URLSearchParams(params); q.set('page', String(p)); return `<https://api.github.com/repositories/${id}/commits?${q}>`; };
  const parts = [];
  if (page > 1) parts.push(`${u(page - 1)}; rel="prev"`);
  if (page < last) parts.push(`${u(page + 1)}; rel="next"`, `${u(last)}; rel="last"`);
  if (page > 1) parts.push(`${u(1)}; rel="first"`);
  return parts.join(', ');
}

const searchItem = (full, stars, extra = {}) => Object.assign(meta(full, { stargazers_count: stars }), extra);
const SEARCH_ITEMS = [
  searchItem('demo/pwa-app', 553821, { description: 'Build your own stock app' }),
  searchItem('demo/pypkg', 8421, { description: 'Math library', language: 'Python' }),
  searchItem('demo/skillpack', 1234567, { description: 'Agent skills pack', language: null }),
];

// 真的 api.github.com 每個回應都帶額度標頭，並用 CORS 開放網頁讀 Link、Retry-After 這些標頭
const RATE = {
  'x-ratelimit-limit': '60', 'x-ratelimit-remaining': '59', 'x-ratelimit-reset': String(Math.floor(now / 1000) + 3600),
  'access-control-allow-origin': '*',
  'access-control-expose-headers': 'ETag, Link, Location, Retry-After, X-GitHub-OTP, X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Used, X-RateLimit-Resource, X-RateLimit-Reset, X-OAuth-Scopes, X-Accepted-OAuth-Scopes, X-Poll-Interval, X-GitHub-Media-Type, X-GitHub-SSO, X-GitHub-Request-Id, Deprecation, Sunset',
};
const json = (body, status = 200, extra = {}) => ({ status, headers: Object.assign({}, RATE, extra), contentType: 'application/json', body: JSON.stringify(body) });

function apiResponse(url) {
  const p = url.pathname;
  if (p === '/search/repositories') return json({ total_count: SEARCH_ITEMS.length, incomplete_results: false, items: SEARCH_ITEMS });
  const m = p.match(/^\/repos\/([^/]+\/[^/]+)(\/.*)?$/);
  const repo = m && REPOS[m[1]];
  if (!repo) return json({ message: 'Not Found' }, 404);
  const rest = m[2] || '';
  if (!rest) return json(repo.meta);
  if (rest.startsWith('/git/trees/')) {
    // 跟 GitHub 一樣照路徑字母排序：.claude/… 會排在 README.md 前面（「取第一個 README」的 bug 就是這樣出現的）
    const paths = Object.keys(repo.files).sort();
    return json({ tree: paths.map((path) => ({ path, type: 'blob', size: String(repo.files[path] || '').length })), truncated: false });
  }
  if (rest === '/languages') return json({});
  if (rest === '/commits') {
    const all = commitsOf(repo);
    const per = Math.min(100, Number(url.searchParams.get('per_page')) || 30);
    const page = Math.max(1, Number(url.searchParams.get('page')) || 1);
    const last = Math.max(1, Math.ceil(all.length / per));
    const params = {}; url.searchParams.forEach((v, k) => { if (k !== 'page') params[k] = v; });
    const link = linkHeader(repo.meta.id, params, page, last);
    return json(all.slice((page - 1) * per, page * per), 200, link ? { link } : {});
  }
  return json({ message: 'Not Found' }, 404);
}

function rawResponse(url) {
  // /owner/repo/ref/path/to/file
  const parts = decodeURIComponent(url.pathname).split('/').filter(Boolean);
  const repo = REPOS[parts[0] + '/' + parts[1]];
  const path = parts.slice(3).join('/');
  if (!repo || !(path in repo.files)) return { status: 404, body: '' };
  if (repo.files[path] === null) return { status: 500, body: '' };
  return { status: 200, contentType: 'text/plain; charset=utf-8', body: repo.files[path] };
}

// 假翻譯：每一行前面加「中：」，看得出哪些字有送去翻、哪些沒送
const zhLines = (q) => q.split('\n').map((l) => '中：' + l).join('\n');
// 翻成英文（X-Ray 模糊搜尋把字典沒有的中文詞換成英文）：像真的 Google 一樣，認得的回英文、不認得的原樣回來
const EN_DICT = { '選舉': 'election', '週報': 'weekly report', '漫畫': 'comics' };

async function mockNetwork(context, opts) {
  // apiOverride：個別測試可以塞一個 (url) => 回應｜undefined，用來模擬限流等狀況；回 undefined 就照平常回應
  const stats = { api: [], searchQ: [], raw: 0, translate: 0, blocked: [], apiOverride: null };
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === '127.0.0.1' || url.hostname === 'localhost') {
      if (url.pathname === '/trending.json') {
        // 只在部署時產生：預設 404（跟本機開啟一樣）；測試用 test.use({ trendingJson }) 給一份假的
        if (!opts.trendingJson) return route.fulfill({ status: 404, body: '' });
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(opts.trendingJson) });
      }
      if (url.pathname === '/apps.json' && opts.appsJson === 'fail') return route.fulfill({ status: 404, body: '' });
      if (url.pathname === '/fleet.json') {
        // 每天自動健檢的結果也只在部署時產生：預設 404；測試用 test.use({ fleetJson }) 給一份假的
        if (!opts.fleetJson) return route.fulfill({ status: 404, body: '' });
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(opts.fleetJson) });
      }
      return route.continue();
    }
    if (url.hostname === 'api.github.com') {
      stats.api.push(url.pathname);
      if (url.pathname === '/search/repositories') stats.searchQ.push(url.searchParams.get('q'));
      // 可以回傳 Promise：用來模擬「這個請求比較慢回來」
      const o = stats.apiOverride && await stats.apiOverride(url);
      return route.fulfill(o || apiResponse(url));
    }
    if (url.hostname === 'raw.githubusercontent.com') { stats.raw++; return route.fulfill(rawResponse(url)); }
    if (url.hostname === 'translate.googleapis.com') {
      stats.translate++;
      const q = url.searchParams.get('q') || '';
      const out = url.searchParams.get('tl') === 'en' ? (EN_DICT[q] || q) : zhLines(q);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([[[out, q]]]) });
    }
    if (url.hostname.endsWith('.workers.dev')) return route.fulfill({ status: 200, body: 'ok' });
    stats.blocked.push(url.href);
    return route.abort();
  });
  return stats;
}

const test = base.extend({
  appsJson: ['ok', { option: true }],
  trendingJson: [null, { option: true }],
  fleetJson: [null, { option: true }],
  net: [async ({ context, appsJson, trendingJson, fleetJson }, use) => {
    const stats = await mockNetwork(context, { appsJson, trendingJson, fleetJson });
    await use(stats);
    // 網頁不該偷偷連到沒預期的網站（隱私，也代表有新的外部相依沒被測到）
    expect(stats.blocked, '連到了沒列在 fixtures.js 的外部網站').toEqual([]);
  }, { auto: true }],
  // 每個測試結束都檢查：頁面不能有 JavaScript 錯誤
  pageErrors: [async ({ page }, use) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await use(errors);
    expect(errors, '頁面出現 JavaScript 錯誤').toEqual([]);
  }, { auto: true }],
});

// 手機上最常見的版面壞法：某個元素太寬，整頁可以左右滑
async function expectNoHorizontalScroll(page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, '頁面不應該能左右滑').toBeLessThanOrEqual(0);
}

// 教學頁（dev-guide、agent）的起手式精靈：從頭一題一題回答到結果頁。
// answers 是 {欄位 id: 值}：文字就填、單選點那個選項、多選勾那些選項；沒列到的照預設。
// 每題跑兩輪，因為有些欄位要先選了別的選項才會出現（例如選「已經有的 App」才問 repo）。
async function runWizard(page, answers = {}) {
  await page.locator('#wizard [data-wiz="begin"]').click();
  for (let i = 0; i < 30; i++) {
    for (let pass = 0; pass < 2; pass++) {
      for (const [id, v] of Object.entries(answers)) {
        if (!(await page.locator(`#wizard [data-field="${id}"]`).count())) continue;
        if (Array.isArray(v)) for (const x of v) await page.locator(`#w-${id}-${x}`).check();
        else if (await page.locator(`#w-${id}`).count()) await page.locator(`#w-${id}`).fill(v);
        else await page.locator(`#w-${id}-${v}`).check();
      }
    }
    const next = page.locator('#wizard [data-wiz="next"]');
    const last = (await next.innerText()).includes('看結果');
    await next.click();
    if (last) break;
  }
  await expect(page.locator('#wizard h2')).toHaveText('結果：照下面做');
}

module.exports = { test, expect, REPOS, SEARCH_ITEMS, RATE, commitsOf, expectNoHorizontalScroll, runWizard };
