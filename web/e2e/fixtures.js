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
// 網址挖礦用：線上成品、API、webhook／金鑰、下載、文件、社群、雜訊都有；還有不該讀的資料夾、敏感檔、lock 檔
const URL_FILES = {
  'README.md': '# URL app\n\nDemo: https://url-app.vercel.app.\nDocs: https://docs.urlapp.dev/guide\nChat: https://discord.gg/abc123\n' +
    '![build](https://img.shields.io/badge/build-ok-green) License: https://opensource.org/licenses/MIT\nIssues: https://github.com/demo/url-app/issues\n',
  'CNAME': 'app.urlapp.dev\n',
  'src/api.js': "const API = 'https://api.urlapp.dev/v1/items';\nconst WS = 'wss://rt.urlapp.dev/socket';\n" +
    "fetch('https://hooks.slack.com/services/T000/B000/XXXXSECRETXXXX');\nconst MAP = 'https://maps.googleapis.com/maps/api/js?key=AIzaSyFAKEFAKE123456&v=3';\n" +
    "const tpl = `https://cdn.urlapp.dev/${name}`;\nconst again = 'https://api.urlapp.dev/v1/items';\n",
  'data/links.md': 'Dataset https://huggingface.co/datasets/demo/set and https://github.com/demo/url-app/releases/download/v1/app.apk\n',
  'node_modules/x/index.js': "module.exports = 'https://should-not-read.urlapp.dev';\n",
  '.env.production': 'API=https://secret-internal.urlapp.dev?token=abcdefghijk\n',
  'package-lock.json': '{"resolved": "https://lockfile-only.urlapp.dev/x.tgz"}\n',
};

// ── 🌐 反推網站用的假資料（照 GitHub 真的回應格式）──
// 帳號物件：list repos、code search 的 repository.owner 都長這樣
const OWNER = (login, id, type = 'User') => ({
  login, id, node_id: 'MDQ6VXNlcj' + id, avatar_url: `https://avatars.githubusercontent.com/u/${id}?v=4`, gravatar_id: '',
  url: 'https://api.github.com/users/' + login, html_url: 'https://github.com/' + login, type, site_admin: false,
});
// GET /users/{username}/repos 回的是 minimal-repository：比 /repos/{owner}/{repo} 少一些欄位，但有 homepage、has_pages、fork、archived
const minimalRepo = (owner, name, extra = {}) => meta(owner.login + '/' + name, Object.assign({
  owner, fork: false, archived: false, disabled: false, visibility: 'public', is_template: false,
  description: null, stargazers_count: 0, language: null, homepage: null, has_pages: false,
}, extra));
// 0xspeter：2026-11-13 實查（search/repositories?q=user:0xspeter）有 8 個公開 repo，沒有一個叫 radar、homepage 全是 null。
// 描述、語言、星數沒查，照實給 null／0，不編造。
const SPETER = OWNER('0xspeter', 91000001);
const LUMEN = OWNER('lumen', 91000002);
const SITE_USERS = {
  '0xspeter': ['SpaceTrial', 'BitcoinvsGold', 'DEJA', 'DEJAB', 'hualien-locksmith-typing', 'agent-coordinator', 'RONIN', 'yijibang']
    .map((n, i) => minimalRepo(SPETER, n, { pushed_at: iso(3 + i * 11) })),
  // 網站欄位（homepage）就填著線上網址的那種：反推要 ✅；同帳號另一個叫 notes 的只是名稱對得上 → 🟡
  lumen: [
    minimalRepo(LUMEN, 'notes-pwa', { homepage: 'https://lumen-notes.netlify.app', description: 'Offline notes PWA', stargazers_count: 42, language: 'JavaScript', pushed_at: iso(1) }),
    minimalRepo(LUMEN, 'notes', { description: 'Old notes prototype', stargazers_count: 3, pushed_at: iso(200) }),
    minimalRepo(LUMEN, 'lumen', { description: 'Profile README', pushed_at: iso(30) }),   // 個人首頁說明，不是網站
    minimalRepo(LUMEN, 'dotfiles', { pushed_at: iso(60) }),
  ],
};
const HARBOR = OWNER('harbor-lab', 91000003, 'Organization');
// 程式碼搜尋的語料：只有登入（帶 Token）才查得到。同一個 repo 兩個檔都寫著 Firebase 專案代號
const CODE_FILES = [
  { repo: 'harbor-lab/price-watch', path: '.firebaserc', text: '{\n  "projects": {\n    "default": "pricewatch-7f3a1"\n  }\n}\n' },
  { repo: 'harbor-lab/price-watch', path: '.github/workflows/deploy.yml', text: 'jobs:\n  deploy:\n    steps:\n      - uses: FirebaseExtended/action-hosting-deploy@v0\n        with:\n          projectId: pricewatch-7f3a1\n' },
];

const REPOS = {
  'demo/pwa-app': { meta: meta('demo/pwa-app'), files: PWA_FILES, commits: { n: 57, firstDays: 1500 } },
  // 反推網站找到之後點「掃描」要掃得起來
  'lumen/notes-pwa': { meta: meta('lumen/notes-pwa', { owner: LUMEN, homepage: 'https://lumen-notes.netlify.app', description: 'Offline notes PWA', stargazers_count: 42 }),
    files: { 'README.md': '# Notes PWA\n\nLive: https://lumen-notes.netlify.app\n', 'index.html': '<!doctype html><title>notes</title>', 'netlify.toml': '[build]\n  publish = "."\n' } },
  'octo-site/handbook': { meta: meta('octo-site/handbook', { owner: OWNER('octo-site', 91000004, 'Organization'), has_pages: true, homepage: null, description: 'Team handbook' }),
    files: { 'README.md': '# Handbook\n', 'index.md': '# Welcome\n', '_config.yml': 'title: Handbook\n' } },
  'octo-site/octo-site.github.io': { meta: meta('octo-site/octo-site.github.io', { owner: OWNER('octo-site', 91000004, 'Organization'), has_pages: true, description: 'Org homepage' }),
    files: { 'README.md': '# octo-site.github.io\n', 'index.html': '<!doctype html><title>octo</title>' } },
  'harbor-lab/price-watch': { meta: meta('harbor-lab/price-watch', { owner: HARBOR, description: 'Grocery price tracker' }),
    files: Object.assign({ 'README.md': '# Price watch\n\nTrack grocery prices.\n', 'index.html': '<!doctype html><title>price</title>' },
      Object.fromEntries(CODE_FILES.map((f) => [f.path, f.text]))) },
  'demo/private-app': { meta: meta('demo/private-app', { private: true }), files: PWA_FILES },
  'demo/pypkg': { meta: meta('demo/pypkg', { language: 'Python' }), files: PY_FILES },
  'demo/skillpack': { meta: meta('demo/skillpack', { language: null }), files: SKILL_FILES, commits: { n: 1, firstDays: 10 } },
  'demo/secrets-app': { meta: meta('demo/secrets-app'), files: SECRET_FILES },
  'demo/gen-app': { meta: meta('demo/gen-app'), files: GEN_FILES },
  'xin7355-collab/busy-app': { meta: meta('xin7355-collab/busy-app'), files: BUSY_FILES },
  'demo/url-app': { meta: meta('demo/url-app'), files: URL_FILES },
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
// 搜尋 API 的額度另外算：未登入每分鐘 10 次、登入 30 次；程式碼搜尋一定要登入、每分鐘 10 次（GitHub REST 文件）
const searchRate = (resource, limit) => ({
  'x-ratelimit-limit': String(limit), 'x-ratelimit-remaining': String(limit - 1), 'x-ratelimit-used': '1',
  'x-ratelimit-reset': String(Math.floor(Date.now() / 1000) + 60), 'x-ratelimit-resource': resource,
});

// 反推網站的搜尋（有引號片語＋in: 條件）：在假資料裡照欄位找片語；對不上就是 0 筆，不能亂回 SEARCH_ITEMS
// （不然「找不到」的情況永遠測不到）。user:帳號 → 那個帳號的 repo，帳號不存在時跟 GitHub 一樣回 422
function searchRepos(q, rate) {
  const um = q.match(/(?:^|\s)user:([\w-]+)/);
  if (um) {
    const list = SITE_USERS[um[1].toLowerCase()];
    if (!list) return json({ message: 'Validation Failed', errors: [{ message: 'The listed users and repositories cannot be searched either because the resources do not exist or you do not have permission to view them.', resource: 'Search', field: 'q', code: 'invalid' }], documentation_url: 'https://docs.github.com/v3/search/' }, 422, rate);
    return json({ total_count: list.length, incomplete_results: false, items: list.map((r) => Object.assign({ score: 1 }, r)) }, 200, rate);
  }
  const phrases = [...q.matchAll(/"([^"]+)"/g)].map((m) => m[1].toLowerCase());
  const inq = (q.match(/\bin:([\w,]+)/) || [])[1];
  if (!phrases.length || !inq) return json({ total_count: SEARCH_ITEMS.length, incomplete_results: false, items: SEARCH_ITEMS }, 200, rate);
  const fields = inq.split(','), seen = new Set();
  const corpus = Object.values(REPOS).map((r) => Object.assign({}, r.meta, { _readme: r.files['README.md'] || '' }))
    .concat(...Object.values(SITE_USERS));
  const items = corpus.filter((r) => {
    if (seen.has(r.full_name)) return false;
    const hay = fields.map((f) => String((f === 'readme' ? r._readme : r[f]) || '')).join('\n').toLowerCase();
    if (!phrases.some((ph) => hay.includes(ph))) return false;
    seen.add(r.full_name); return true;
  }).map(({ _readme, ...r }) => Object.assign({ score: 1 }, r));
  return json({ total_count: items.length, incomplete_results: false, items }, 200, rate);
}

// 程式碼搜尋：沒帶 Token 跟 GitHub 一樣回 401；有就在 CODE_FILES 找（片語、filename: 條件）
function searchCode(url, auth) {
  const rate = searchRate('code_search', 10);
  if (!auth) return json({ message: 'Requires authentication', errors: [{ message: 'Must be authenticated to access the code search API', resource: 'Search', field: 'q', code: 'invalid' }], documentation_url: 'https://docs.github.com/v3/search/#search-code', status: '401' }, 401, rate);
  const q = url.searchParams.get('q') || '';
  const fname = (q.match(/\bfilename:(\S+)/) || [])[1];
  const phrase = ((q.match(/"([^"]+)"/) || [])[1] || q.replace(/\S+:\S+/g, '').trim()).toLowerCase();
  const items = CODE_FILES.filter((f) => f.text.toLowerCase().includes(phrase) && (!fname || f.path.split('/').pop() === fname)).map((f) => {
    const repo = REPOS[f.repo].meta;
    const commit = (repo.id.toString(16) + 'c0de').padEnd(40, '0');
    const blob = (repo.id.toString(16) + Buffer.from(f.path).toString('hex')).slice(0, 40).padEnd(40, 'b');
    return {
      name: f.path.split('/').pop(), path: f.path, sha: blob,
      url: `https://api.github.com/repositories/${repo.id}/contents/${f.path}?ref=${commit}`,
      git_url: `https://api.github.com/repositories/${repo.id}/git/blobs/${blob}`,
      html_url: `https://github.com/${f.repo}/blob/${commit}/${f.path}`,
      // 程式碼搜尋附的 repository 是精簡版：沒有星數、homepage
      repository: { id: repo.id, node_id: 'MDEwOlJlcG9zaXRvcnk' + repo.id, name: repo.name, full_name: repo.full_name, owner: repo.owner,
        private: false, html_url: repo.html_url, description: repo.description, fork: false, url: 'https://api.github.com/repos/' + repo.full_name },
      score: 1,
    };
  });
  return json({ total_count: items.length, incomplete_results: false, items }, 200, rate);
}

function apiResponse(url, req) {
  const p = url.pathname;
  const auth = req ? req.headers()['authorization'] : '';
  if (p === '/search/repositories') return searchRepos(url.searchParams.get('q') || '', searchRate('search', auth ? 30 : 10));
  if (p === '/search/code') return searchCode(url, auth);
  const um = p.match(/^\/users\/([^/]+)\/repos$/);
  if (um) {
    const list = SITE_USERS[decodeURIComponent(um[1]).toLowerCase()];
    if (!list) return json({ message: 'Not Found', documentation_url: 'https://docs.github.com/rest/repos/repos#list-repositories-for-a-user', status: '404' }, 404);
    const per = Math.min(100, Number(url.searchParams.get('per_page')) || 30);
    const sorted = list.slice().sort((a, b) => (url.searchParams.get('sort') === 'pushed'
      ? String(b.pushed_at).localeCompare(String(a.pushed_at)) : a.full_name.localeCompare(b.full_name)));
    return json(sorted.slice(0, per));
  }
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
// 反推網站找到的 repo 點「掃描」後，詳細頁也會連一次它的網址；反推本身 ⛔ 不連（測試看 stats.live 確認）
const FAKE_LIVE = ['url-app.vercel.app', 'app.urlapp.dev', 'lumen-notes.netlify.app', 'octo-site.github.io'];
const EN_DICT = { '選舉': 'election', '週報': 'weekly report', '漫畫': 'comics' };

async function mockNetwork(context, opts) {
  // apiOverride：個別測試可以塞一個 (url) => 回應｜undefined，用來模擬限流等狀況；回 undefined 就照平常回應
  const stats = { api: [], searchQ: [], codeQ: [], raw: 0, translate: 0, live: [], blocked: [], apiOverride: null };
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === '127.0.0.1' || url.hostname === 'localhost') {
      if (url.pathname === '/trending.json') {
        // 只在部署時產生：預設 404（跟本機開啟一樣）；測試用 test.use({ trendingJson }) 給一份假的
        if (!opts.trendingJson) return route.fulfill({ status: 404, body: '' });
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(opts.trendingJson) });
      }
      if (url.pathname === '/apps.json' && opts.appsJson === 'fail') return route.fulfill({ status: 404, body: '' });
      // 部署時 ops/discover_apps.py 產生的那份（有 source／new／error）：測試用 test.use({ appsJson: {...} }) 給
      if (url.pathname === '/apps.json' && opts.appsJson && typeof opts.appsJson === 'object') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(opts.appsJson) });
      }
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
      if (url.pathname === '/search/code') stats.codeQ.push(url.searchParams.get('q'));
      // 可以回傳 Promise：用來模擬「這個請求比較慢回來」
      const o = stats.apiOverride && await stats.apiOverride(url);
      return route.fulfill(o || apiResponse(url, route.request()));
    }
    if (url.hostname === 'raw.githubusercontent.com') { stats.raw++; return route.fulfill(rawResponse(url)); }
    if (url.hostname === 'translate.googleapis.com') {
      stats.translate++;
      const q = url.searchParams.get('q') || '';
      const out = url.searchParams.get('tl') === 'en' ? (EN_DICT[q] || q) : zhLines(q);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([[[out, q]]]) });
    }
    if (url.hostname.endsWith('.workers.dev')) return route.fulfill({ status: 200, body: 'ok' });
    // 假專案的線上網址：詳細頁會實際連一次看活著沒（probeLive），回 ok 就好
    if (FAKE_LIVE.includes(url.hostname)) { stats.live.push(url.hostname); return route.fulfill({ status: 200, body: 'ok' }); }
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

module.exports = { test, expect, REPOS, SEARCH_ITEMS, SITE_USERS, RATE, commitsOf, expectNoHorizontalScroll, runWizard };
