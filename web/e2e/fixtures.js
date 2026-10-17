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

const meta = (full, extra = {}) => Object.assign({
  name: full.split('/')[1], full_name: full, description: 'Demo project ' + full,
  default_branch: 'main', stargazers_count: 1234, topics: [], language: 'JavaScript',
  license: { spdx_id: 'MIT' }, homepage: null, has_pages: false, private: false,
  pushed_at: iso(2), created_at: iso(400), size: 2048, html_url: 'https://github.com/' + full,
}, extra);

const REPOS = {
  'demo/pwa-app': { meta: meta('demo/pwa-app'), files: PWA_FILES },
  'demo/private-app': { meta: meta('demo/private-app', { private: true }), files: PWA_FILES },
  'demo/pypkg': { meta: meta('demo/pypkg', { language: 'Python' }), files: PY_FILES },
  'demo/skillpack': { meta: meta('demo/skillpack', { language: null }), files: SKILL_FILES },
};

const searchItem = (full, stars, extra = {}) => Object.assign(meta(full, { stargazers_count: stars }), extra);
const SEARCH_ITEMS = [
  searchItem('demo/pwa-app', 553821, { description: 'Build your own stock app' }),
  searchItem('demo/pypkg', 8421, { description: 'Math library', language: 'Python' }),
  searchItem('demo/skillpack', 1234567, { description: 'Agent skills pack', language: null }),
];

const RATE = { 'x-ratelimit-limit': '60', 'x-ratelimit-remaining': '59', 'x-ratelimit-reset': String(Math.floor(now / 1000) + 3600) };
const json = (body, status = 200) => ({ status, headers: RATE, contentType: 'application/json', body: JSON.stringify(body) });

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

async function mockNetwork(context, opts) {
  const stats = { api: [], raw: 0, translate: 0, blocked: [] };
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === '127.0.0.1' || url.hostname === 'localhost') {
      if (url.pathname === '/trending.json') return route.fulfill({ status: 404, body: '' }); // 只在部署時產生
      if (url.pathname === '/apps.json' && opts.appsJson === 'fail') return route.fulfill({ status: 404, body: '' });
      return route.continue();
    }
    if (url.hostname === 'api.github.com') { stats.api.push(url.pathname); return route.fulfill(apiResponse(url)); }
    if (url.hostname === 'raw.githubusercontent.com') { stats.raw++; return route.fulfill(rawResponse(url)); }
    if (url.hostname === 'translate.googleapis.com') {
      stats.translate++;
      const q = url.searchParams.get('q') || '';
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([[[zhLines(q), q]]]) });
    }
    if (url.hostname.endsWith('.workers.dev')) return route.fulfill({ status: 200, body: 'ok' });
    stats.blocked.push(url.href);
    return route.abort();
  });
  return stats;
}

const test = base.extend({
  appsJson: ['ok', { option: true }],
  net: [async ({ context, appsJson }, use) => {
    const stats = await mockNetwork(context, { appsJson });
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

module.exports = { test, expect, REPOS, SEARCH_ITEMS, expectNoHorizontalScroll };
