// Repo X-Ray（web/xray.html）：掃描判讀、專案地圖、打包給 AI、搜尋結果、勾選、說明文件、返回、
// 複製失敗的手動面板、App 顧問推薦、只問這段、敏感檔名、自動產生資料夾、首次 commit、限流、熱門清單、有線上成品篩選
const { test, expect, expectNoHorizontalScroll, REPOS, RATE, commitsOf } = require('./fixtures');
const ADV_RULES = require('../advisor_rules.json');

async function scan(page, repo) {
  await page.goto('/xray.html?repo=' + repo);
  await expect(page.locator('#out')).toHaveClass(/\bon\b/);
  await expect(page.locator('#v-title')).not.toHaveText('—');
}

test.describe('掃描自家 PWA', () => {
  test('專案地圖替每個資料夾標中文用途', async ({ page }) => {
    await scan(page, 'demo/pwa-app');
    const rows = page.locator('#map summary');
    await expect(rows.filter({ hasText: 'src/' })).toContainText('主程式');
    await expect(rows.filter({ hasText: 'docs/' })).toContainText('說明文件');
    await expect(rows.filter({ hasText: 'tests/' })).toContainText('自動測試');
    await expect(rows.filter({ hasText: '.github/' })).toContainText('GitHub 自動化設定');
    await expect(rows.filter({ hasText: '.claude/' })).toContainText('AI 技能');
    await rows.filter({ hasText: '.claude/' }).click();
    await expect(page.locator('#map details[open] .ms')).toContainText('skills/');
    await expectNoHorizontalScroll(page);
  });

  test('裝進來的技能（.claude/skills）不會讓 App 被判成技能包', async ({ page }) => {
    await scan(page, 'demo/pwa-app');
    await expect(page.locator('#v-title')).not.toContainText('技能包');
  });

  test('從 CLAUDE.md 找到 workers.dev 網址並實際連一次', async ({ page }) => {
    await scan(page, 'demo/pwa-app');
    const live = page.locator('#v-live a').first();
    await expect(live).toContainText('watchlist-app.xinapp.workers.dev');
    await expect(live).toContainText('CLAUDE.md');
    await expect(live).toContainText('連得上');
  });

  test('外部專案有「參考它做我的 App」，沒有 App 顧問', async ({ page }) => {
    await scan(page, 'demo/pwa-app');
    await expect(page.locator('#refclaude')).toBeVisible();
    await expect(page.locator('#sec-advisor')).toBeHidden();
  });

  test('公開 repo 才顯示 DeepWiki 等外部深入看', async ({ page }) => {
    await scan(page, 'demo/pwa-app');
    await expect(page.locator('#v-quick a[href="https://deepwiki.com/demo/pwa-app"]')).toHaveCount(1);
    await scan(page, 'demo/private-app');
    await expect(page.locator('#v-quick a[href*="deepwiki.com"]')).toHaveCount(0);
  });

  test('複製整包重點給 AI：用根目錄 README，不被技能的 README 搶走', async ({ page }) => {
    await scan(page, 'demo/pwa-app');
    await page.locator('#packbtn').click();
    await expect(page.locator('#toast')).toContainText('已複製');
    const pack = await page.evaluate(() => navigator.clipboard.readText());
    expect(pack).toContain('https://github.com/demo/pwa-app');
    expect(pack).toContain('## 目錄地圖');
    expect(pack).toContain('# Demo PWA');
    expect(pack).not.toContain('SHOULD-NOT-BE-ROOT');
    expect(pack.length).toBeLessThan(31000);
  });

  test('說明文件：制式文件直接標中文、一般文件翻成中文', async ({ page, net }) => {
    await scan(page, 'demo/pwa-app');
    await page.locator('#doc-list').scrollIntoViewIfNeeded();
    const row = (name) => page.locator('#doc-list .dr').filter({ hasText: name }).locator('.dsum');
    await expect(row('CODE_OF_CONDUCT.md')).toHaveText(/社群行為準則/);
    await expect(row('guide.md')).toHaveText('中：How to deploy this app to GitHub Pages.');
    expect(net.translate).toBeGreaterThan(0);
  });
});

test('Python 套件：只看主要依賴，判成函式庫，不叫人放 GitHub Pages', async ({ page }) => {
  await scan(page, 'demo/pypkg');
  await expect(page.locator('#v-type')).toContainText('函式庫');
  await expect(page.locator('#plain')).toContainText('Python 套件');
});

test('根目錄 skills/*/SKILL.md 判成 AI 技能包', async ({ page }) => {
  await scan(page, 'demo/skillpack');
  await expect(page.locator('#v-type')).toContainText('AI 技能包');
});

test.describe('搜尋結果', () => {
  async function search(page) {
    await page.goto('/xray.html');
    await page.locator('#url').fill('stock app');
    await page.locator('#go').click();
    await expect(page.locator('#rrows button[data-i]')).toHaveCount(3);
  }

  test('右側大字星星數，1 萬以上用「萬」', async ({ page }) => {
    await search(page);
    const stars = page.locator('#rrows .rz b');
    await expect(stars).toHaveText(['★ 55.4 萬', '★ 8,421', '★ 123 萬']);
    const meta = await page.locator('#rrows button[data-i] .rd:last-child').allInnerTexts();
    for (const m of meta) expect(m, '資訊行不能有連續分隔點').not.toMatch(/·\s*·/);
    await expectNoHorizontalScroll(page);
  });

  test('勾選會記在手機，重新整理後還在', async ({ page }) => {
    await search(page);
    await page.locator('#rrows .pkb').first().click();
    await expect(page.locator('#pickbar')).toBeVisible();
    await expect(page.locator('#pickn')).toHaveText('1');
    await page.reload();
    await expect(page.locator('#pickn')).toHaveText('1');
  });

  test('點進專案再返回，清單還在、不重新搜尋', async ({ page, net }) => {
    await search(page);
    const searches = () => net.api.filter((p) => p === '/search/repositories').length;
    const before = searches();
    await page.locator('#rrows button[data-i]').first().click({ position: { x: 120, y: 16 } });
    await expect(page.locator('#out')).toHaveClass(/\bon\b/);
    await page.goBack();
    await expect(page.locator('#rrows button[data-i]')).toHaveCount(3);
    expect(searches()).toBe(before);
  });
});

test.describe('複製', () => {
  // iPhone Safari 常在 await 之後擋剪貼簿：模擬兩種複製方法都失敗
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator.clipboard, 'writeText', { value: () => Promise.reject(new DOMException('Write permission denied.', 'NotAllowedError')) });
      document.execCommand = () => false;
    });
  });

  test('自動複製被擋時，顯示已全選的文字框可以手動拷貝', async ({ page }) => {
    await scan(page, 'demo/pwa-app');
    await page.locator('#packbtn').click();
    const panel = page.locator('#copyfb');
    await expect(panel).toBeVisible();
    await expect(panel).toContainText('長按');
    const ta = page.locator('#copyfb-ta');
    const v = await ta.inputValue();
    expect(v).toContain('https://github.com/demo/pwa-app');
    expect(v).toContain('## 目錄地圖');
    const sel = await ta.evaluate((el) => ({ s: el.selectionStart, e: el.selectionEnd, n: el.value.length, focus: document.activeElement === el }));
    expect(sel).toEqual({ s: 0, e: sel.n, n: sel.n, focus: true });
    // 沒真的複製到，就不能說「已複製」，也不能記成複製過
    await expect(page.locator('#toast')).not.toContainText('已複製');
    const hist = await page.evaluate(() => JSON.parse(localStorage.getItem('xray-hist-v1') || '{}')['demo/pwa-app']);
    expect(hist && hist.cp).toBeFalsy();
    await expectNoHorizontalScroll(page);
    await page.locator('#copyfb-close').click();
    await expect(panel).toBeHidden();
  });

  test('只問這段在複製失敗時也走同一個面板', async ({ page }) => {
    await scan(page, 'demo/pwa-app');
    await page.locator('#map summary').filter({ hasText: 'src/' }).click();
    await page.locator('#map details[open] .askseg').click();
    await expect(page.locator('#copyfb')).toBeVisible();
    expect(await page.locator('#copyfb-ta').inputValue()).toContain('資料夾：src/');
  });
});

test('專案地圖「只問這段」：只複製這個資料夾＋repo 網址，比整包短很多', async ({ page }) => {
  await scan(page, 'demo/pwa-app');
  await page.locator('#map summary').filter({ hasText: 'src/' }).click();
  await page.locator('#map details[open] .askseg').click();
  await expect(page.locator('#toast')).toContainText('已複製');
  const t = await page.evaluate(() => navigator.clipboard.readText());
  expect(t).toContain('https://github.com/demo/pwa-app');
  expect(t).toContain('資料夾：src/（主程式');
  expect(t).toContain('只針對這個資料夾');
  expect(t).not.toContain('docs/');
  expect(t.length).toBeLessThan(600);
  await expectNoHorizontalScroll(page);
});

test.describe('App 顧問（自己的 App）', () => {
  const title = (id) => ADV_RULES.rules.find((r) => r.id === id).title;
  // busy-app 同時命中：沒 CLAUDE.md（weight 3）＋四條 weight 2（沒 Actions、有畫面沒測試、爬蟲、呼叫 AI）
  const URGENT = ['no-claude', 'no-ci', 'no-tests-ui', 'scrape', 'llm'];

  test('同樣急的規則輪流推薦：每條命中的規則至少一個技能進名單', async ({ page }) => {
    await scan(page, 'xin7355-collab/busy-app');
    await expect(page.locator('#sec-advisor')).toBeVisible();
    for (const id of URGENT) await expect(page.locator('#advisor .rule').filter({ hasText: title(id) })).toHaveCount(1);
    await page.locator('#adv-install').click();
    await expect(page.locator('#toast')).toContainText('已複製');
    const t = await page.evaluate(() => navigator.clipboard.readText());
    const lines = t.split('\n').filter((l) => l.startsWith('- '));
    expect(lines).toHaveLength(12);
    // 以前整條攤平：爬蟲 5 個技能先吃光名額，「呼叫 AI 模型」一個都進不去
    for (const id of URGENT) expect(lines.some((l) => l.endsWith('（' + title(id) + '）')), id + ' 要有技能進名單').toBe(true);
  });

  test('pickWant：weight 高的組先輪完；同組每條規則輪流出一個；已裝與重複的跳過', async ({ page }) => {
    await scan(page, 'demo/pypkg');
    const r = await page.evaluate(() => pickWant([
      { title: 'A', weight: 2, skills: ['x/a1', 'x/a2', 'x/a3', 'x/a4', 'x/a5'] },
      { title: 'B', weight: 2, skills: ['x/b1', 'x/b2', 'x/b3', 'x/b4', 'x/b5'] },
      { title: 'C', weight: 2, skills: ['x/a1', 'x/c2', 'x/c3', 'x/c4', 'x/c5'] },
      { title: 'D', weight: 5, skills: ['x/d1'] },
      { title: 'E', weight: 0, skills: ['x/e1'] },
    ], new Set(['b1'])));
    expect(r.want).toEqual(['x/d1', 'x/a1', 'x/b2', 'x/c2', 'x/a2', 'x/b3', 'x/c3', 'x/a3', 'x/b4', 'x/c4', 'x/a4', 'x/b5']);
    expect(r.more).toBe(3);
    expect(r.why['x/a1']).toBe('A');
  });

  test('顧問每一項都有「只問這段」，複製那一項的標題、原因與建議技能', async ({ page }) => {
    await scan(page, 'xin7355-collab/busy-app');
    const rule = page.locator('#advisor .rule').filter({ hasText: title('llm') });
    await rule.locator('.askseg').click();
    const t = await page.evaluate(() => navigator.clipboard.readText());
    expect(t).toContain('https://github.com/xin7355-collab/busy-app');
    expect(t).toContain(title('llm'));
    expect(t).toContain('ai-model-router');
    expect(t).not.toContain(title('scrape'));
    expect(t.length).toBeLessThan(1500);
    await expectNoHorizontalScroll(page);
  });
});

test.describe('敏感檔名警示', () => {
  const risks = async (page) => page.locator('#risk .risk div i').allInnerTexts();

  test('抓登入設定檔與金鑰資料夾，放過 .env 範本與「講 token 的檔」', async ({ page }) => {
    await scan(page, 'demo/secrets-app');
    const r = await risks(page);
    for (const f of ['.env.production', '.npmrc', '.pypirc', '.git-credentials', '.netrc', '.htpasswd', '.ssh/config', '.aws/credentials', '.gnupg/pubring.kbx'])
      expect(r, f).toContain(f);
    for (const f of ['.env.example', 'src/tokenizer.py', 'docs/token-economics.md', 'README.md']) expect(r, f).not.toContain(f);
  });

  test('抓 id_ed25519、.p12／.p8／.pfx、service-account*.json；公鑰 .pub 不算', async ({ page }) => {
    await scan(page, 'demo/gen-app');
    const r = await risks(page);
    for (const f of ['certs/app.p12', 'certs/ios-push.p8', 'certs/win.pfx', 'certs/id_ed25519', 'certs/service-account-prod.json']) expect(r, f).toContain(f);
    expect(r).not.toContain('certs/id_ed25519.pub');
  });
});

test('專案地圖：自動產生的資料夾標「不用看」並排在最後；要有證據才算', async ({ page }) => {
  await scan(page, 'demo/gen-app');
  const rows = page.locator('#map summary');
  const texts = await rows.allInnerTexts();
  const at = (name) => texts.findIndex((t) => t.includes(' ' + name));
  const GEN = ['node_modules/', 'dist/', 'build/', '.next/', 'storybook-static/', 'mypkg.egg-info/', 'coverage/', 'venv/'];
  for (const g of GEN) {
    expect(at(g), g + ' 要出現在地圖上').toBeGreaterThan(-1);
    expect(texts[at(g)], g).toContain('自動產生，不用看');
    expect(at(g), g + ' 要排在根目錄後面').toBeGreaterThan(at('（根目錄）'));
  }
  // node_modules 檔案最多（30 個），但真正的資料夾全部排在它前面
  expect(texts[0]).not.toContain('自動產生');
  for (const real of ['src/', 'certs/', 'backend/', 'env/']) expect(at(real), real).toBeLessThan(at('（根目錄）'));
  // 叫 env 但沒有 pyvenv.cfg：不能標成自動產生
  expect(texts[at('env/')]).not.toContain('自動產生');
  const body = (name) => page.locator('#map details', { has: page.locator('summary', { hasText: name }) }).locator('.ms');
  await rows.filter({ hasText: 'src/' }).click();
  await expect(body('src/')).toContainText('__snapshots__/ ♻️ 自動產生，不用看');
  await rows.filter({ hasText: 'backend/' }).click();
  await expect(body('backend/')).toContainText('.venv/ ♻️ 自動產生，不用看');
  await expectNoHorizontalScroll(page);
});

test.describe('首次 commit', () => {
  const day = (full) => { const c = commitsOf(REPOS[full]); return c[c.length - 1].commit.author.date.slice(0, 10); };
  const commitCalls = (net, full) => net.api.filter((p) => p === '/repos/' + full + '/commits').length;

  test('照 Link 的 last 頁碼抓最後一頁的最後一筆；比建立日早很多照實寫', async ({ page, net }) => {
    await scan(page, 'demo/pwa-app');
    const chip = page.locator('#v-first');
    await expect(chip).toContainText('首次 commit：' + day('demo/pwa-app'));
    await expect(chip).toContainText(/約 1,?500 天前/);
    await expect(chip).toContainText(/比 GitHub 建立日早 1,?100 天/);
    expect(commitCalls(net, 'demo/pwa-app')).toBe(2);
    expect(net.api.filter((p) => p.startsWith('/repositories/'))).toEqual([]);
    // 存在手機的快取：重新整理不再花額度
    await page.reload();
    await expect(page.locator('#v-first')).toContainText('首次 commit：' + day('demo/pwa-app'));
    expect(commitCalls(net, 'demo/pwa-app')).toBe(2);
  });

  test('只有一頁（Link 沒有 last）就用第一頁那筆；比建立日晚也照實寫', async ({ page, net }) => {
    await scan(page, 'demo/skillpack');
    const chip = page.locator('#v-first');
    await expect(chip).toContainText('首次 commit：' + day('demo/skillpack') + '（約 10 天前）');
    await expect(chip).toContainText('比 GitHub 建立日晚 390 天');
    expect(commitCalls(net, 'demo/skillpack')).toBe(1);
  });

  test('搜尋列表不查首次 commit', async ({ page, net }) => {
    await page.goto('/xray.html');
    await page.locator('#url').fill('stock app');
    await page.locator('#go').click();
    await expect(page.locator('#rrows button[data-i]')).toHaveCount(3);
    expect(net.api.filter((p) => p.endsWith('/commits'))).toEqual([]);
  });
});

test.describe('GitHub 限流', () => {
  const searchOnce = async (page) => {
    await page.goto('/xray.html');
    await page.locator('#url').fill('stock app');
    await page.locator('#go').click();
  };
  const limited = (extra) => ({ status: 429, headers: Object.assign({}, RATE, extra), contentType: 'application/json',
    body: JSON.stringify({ message: 'You have exceeded a secondary rate limit. Please wait a few minutes before you try again.' }) });

  test('Retry-After 太久：不在背景空等，直接說要等幾秒', async ({ page, net }) => {
    net.apiOverride = (u) => (u.pathname === '/search/repositories' ? limited({ 'retry-after': '120' }) : undefined);
    await searchOnce(page);
    await expect(page.locator('#results')).toContainText('GitHub 限流，約 120 秒後再試');
    expect(net.api.filter((p) => p === '/search/repositories')).toHaveLength(1);
  });

  test('Retry-After 很短：照 GitHub 給的時間等完自動重試', async ({ page, net }) => {
    let n = 0;
    net.apiOverride = (u) => (u.pathname === '/search/repositories' && n++ === 0 ? limited({ 'retry-after': '1' }) : undefined);
    await searchOnce(page);
    await expect(page.locator('#rrows button[data-i]')).toHaveCount(3);
    expect(net.api.filter((p) => p === '/search/repositories')).toHaveLength(2);
  });

  test('額度用完：照 x-ratelimit-reset（時間戳）算還要幾秒，並提醒可以加 Token', async ({ page, net }) => {
    net.apiOverride = (u) => (u.pathname === '/repos/demo/pwa-app'
      ? limited({ 'x-ratelimit-remaining': '0', 'x-ratelimit-reset': String(Math.floor(Date.now() / 1000) + 600) }) : undefined);
    await page.goto('/xray.html?repo=demo/pwa-app');
    await expect(page.locator('#status')).toContainText(/GitHub 限流，約 (599|600) 秒後再試（約 10 分鐘）/);
    await expect(page.locator('#status')).toContainText('Token');
  });
});

test.describe('熱門清單（trending.json）', () => {
  const day = (n) => new Date(Date.now() - n * 864e5).toISOString();
  const item = (full, stars, extra = {}) => Object.assign({
    full_name: full, description: 'Trending project ' + full, stargazers_count: stars, language: 'TypeScript',
    pushed_at: day(1), created_at: day(20), size: 1200, html_url: 'https://github.com/' + full, topics: [], license: 'MIT',
  }, extra);
  test.use({
    trendingJson: {
      generated_at: new Date().toISOString().slice(0, 19), stale: false, failed: [],
      lists: [
        { id: 'rising', title: '竄升最快', desc: '跟上一次收集比，平均每天多最多星', q: '', stale: false, items: [item('demo/fast', 9000, { gain_per_day: 120.5 })] },
        { id: 'top', title: '歷來星數最高', desc: '全 GitHub 最多人收藏的專案', q: 'stars:>50000', stale: false, items: [item('demo/old-giant', 400000)] },
        // build_trending.py 新加的清單：沒有 desc 也要能顯示
        { id: 'breakout', title: '爆紅新星', q: '', stale: false, items: [
          item('demo/rocket', 4321, { new: true, first_seen: '2026-10-17' }),
          item('demo/steady', 2100, { new: false }),
        ] },
      ],
      snapshot: {},
    },
  });

  test('照 trending.json 列出所有清單（含新的爆紅新星），第一次上榜的加 🆕', async ({ page }) => {
    await page.goto('/xray.html');
    const links = page.locator('#trend a[data-t]');
    await expect(links).toHaveText(['🚀 竄升最快', '歷來星數最高', '💥 爆紅新星']);
    await links.filter({ hasText: '爆紅新星' }).click();
    await expect(page.locator('#rrows button[data-i]')).toHaveCount(2);
    await expect(page.locator('#status')).not.toContainText('undefined');
    const row = (n) => page.locator('#rrows button[data-i]').filter({ hasText: n });
    await expect(row('demo/rocket').locator('.newin')).toHaveText('🆕 新上榜');
    await expect(row('demo/rocket').locator('.newin')).toHaveAttribute('title', /2026-10-17/);
    await expect(row('demo/steady').locator('.newin')).toHaveCount(0);
    await expectNoHorizontalScroll(page);
  });

  test('沒有 new 欄位的清單跟以前一樣，不加 🆕', async ({ page }) => {
    await page.goto('/xray.html');
    await page.locator('#trend a[data-t="top"]').click();
    await expect(page.locator('#rrows button[data-i]')).toHaveCount(1);
    await expect(page.locator('#rrows .newin')).toHaveCount(0);
    await expect(page.locator('#rrows')).not.toContainText('🆕');
  });
});

test.describe('🌐 有線上成品（篩選＋開成品）', () => {
  const item = (full, extra = {}) => Object.assign({
    id: full.length, name: full.split('/')[1], full_name: full, description: 'Project ' + full, stargazers_count: 100,
    language: 'JavaScript', pushed_at: new Date().toISOString(), created_at: new Date().toISOString(), size: 100,
    html_url: 'https://github.com/' + full, topics: [], homepage: null, has_pages: false, private: false,
  }, extra);
  // GitHub 搜尋結果本來就帶 homepage、has_pages；篩選只看這兩個欄位，不另外打 API
  const ITEMS = [
    item('demo/live-one', { homepage: 'https://live-one.workers.dev' }),
    item('demo/pages-site', { has_pages: true }),
    item('demo/demo.github.io', { has_pages: true }),            // 個人網站 repo：Pages 網址沒有路徑
    item('demo/gh-home', { homepage: 'https://github.com/demo/gh-home' }),  // 指回 GitHub 自己，不算成品
    item('demo/plain'),
  ];
  async function search(page, net) {
    net.apiOverride = (url) => url.pathname === '/search/repositories'
      ? { status: 200, headers: RATE, contentType: 'application/json', body: JSON.stringify({ total_count: ITEMS.length, incomplete_results: false, items: ITEMS }) }
      : undefined;
    await page.goto('/xray.html');
    await page.locator('#url').fill('demo site');
    await page.locator('#go').click();
    await expect(page.locator('#rrows button[data-i]').first()).toBeVisible();
  }
  const rows = (page) => page.locator('#rrows button[data-i]');
  const live = (page) => page.locator('#rcats [data-liveonly]');

  test('每列有成品網址就顯示「開成品」，篩選只留這些，重新整理後記得', async ({ page, net }) => {
    await search(page, net);
    await expect(rows(page)).toHaveCount(5);
    await expect(live(page)).toContainText('3');
    // 跟其他分類籤同一列、同高度（曾經撞到既有的 .live 樣式變成直排，整列被撐高）
    const hLive = (await live(page).boundingBox()).height;
    const hAll = (await page.locator('#rcats [data-cat="all"]').boundingBox()).height;
    expect(Math.abs(hLive - hAll)).toBeLessThan(2);
    const row = (n) => rows(page).filter({ hasText: n });
    await expect(row('demo/live-one').locator('[data-live]')).toHaveAttribute('data-live', 'https://live-one.workers.dev');
    await expect(row('demo/pages-site').locator('[data-live]')).toHaveAttribute('data-live', 'https://demo.github.io/pages-site/');
    await expect(row('demo/demo.github.io').locator('[data-live]')).toHaveAttribute('data-live', 'https://demo.github.io/');
    await expect(row('demo/gh-home').locator('[data-live]')).toHaveCount(0);
    await expect(row('demo/plain').locator('[data-live]')).toHaveCount(0);

    await live(page).click();
    await expect(live(page)).toHaveClass(/\bon\b/);
    await expect(rows(page)).toHaveCount(3);
    await expectNoHorizontalScroll(page);

    await page.reload();
    await search(page, net);
    await expect(live(page)).toHaveClass(/\bon\b/);
    await expect(rows(page)).toHaveCount(3);
    await live(page).click();
    await expect(rows(page)).toHaveCount(5);
  });

  test('點「開成品」只開網址、不進專案詳細頁、不多打 API', async ({ page, net }) => {
    await search(page, net);
    // 不真的連出去（測試環境擋外部網站）：記下 window.open 被叫的參數
    await page.evaluate(() => { window.__opened = []; window.open = (...a) => { window.__opened.push(a); return null; }; });
    const before = net.api.length;
    await rows(page).filter({ hasText: 'demo/pages-site' }).locator('[data-live]').click();
    const opened = await page.evaluate(() => window.__opened);
    expect(opened).toEqual([['https://demo.github.io/pages-site/', '_blank', 'noopener']]);
    await expect(page.locator('#out')).not.toHaveClass(/\bon\b/);
    expect(net.api.length).toBe(before);
  });

  test('篩選後一筆都沒有：說明原因和怎麼辦', async ({ page, net }) => {
    await search(page, net);
    await live(page).click();
    await page.locator('#rfilter').fill('plain');
    await expect(page.locator('#rrows')).toContainText('又有線上成品');
    await page.locator('#rfilter').fill('');
    await expect(rows(page)).toHaveCount(3);
  });
});

test('連續搜尋兩次：先送出但比較慢回來的舊結果，不能混進新的清單', async ({ page, net }) => {
  const item = (full) => ({ id: full.length, name: full.split('/')[1], full_name: full, description: 'x', stargazers_count: 10,
    language: 'Go', pushed_at: new Date().toISOString(), created_at: new Date().toISOString(), size: 10,
    html_url: 'https://github.com/' + full, topics: [], homepage: null, has_pages: false, private: false });
  const reply = (items) => ({ status: 200, headers: RATE, contentType: 'application/json',
    body: JSON.stringify({ total_count: items.length, incomplete_results: false, items }) });
  net.apiOverride = async (url) => {
    if (url.pathname !== '/search/repositories') return undefined;
    const q = url.searchParams.get('q') || '';
    if (q.includes('slowword')) { await new Promise((r) => setTimeout(r, 1500)); return reply([item('old/a'), item('old/b')]); }
    return reply([item('new/c')]);
  };
  await page.goto('/xray.html');
  await page.locator('#url').fill('slowword');
  await page.locator('#go').click();
  await page.locator('#url').fill('fastword');
  await page.locator('#go').click();
  await expect(page.locator('#rrows button[data-i]')).toHaveCount(1);
  await page.waitForTimeout(2000);   // 等舊的那個慢慢回來
  await expect(page.locator('#rrows button[data-i]')).toHaveCount(1);
  await expect(page.locator('#rrows')).toContainText('new/c');
  await expect(page.locator('#rrows')).not.toContainText('old/a');
  await expect(page.locator('#results .rhead')).toContainText('fastword');
});

// ── 2026-10-31 使用者回報四件事：①「開始」不好按 ②模糊搜尋找不到 ③有「-」模糊搜尋就壞 ④只看繁中 ──
test.describe('搜尋列與模糊搜尋（2026-10-31）', () => {
  const ITEM = (full, extra = {}) => Object.assign({
    id: full.length * 7 + full.charCodeAt(full.length - 1), name: full.split('/')[1], full_name: full, description: 'x',
    stargazers_count: 100, language: 'JavaScript', pushed_at: new Date().toISOString(), created_at: new Date(Date.now() - 400 * 864e5).toISOString(),
    size: 100, html_url: 'https://github.com/' + full, topics: [], license: { spdx_id: 'MIT' }, homepage: null, has_pages: false,
  }, extra);
  const reply = (items, total) => ({ status: 200, headers: RATE, contentType: 'application/json',
    body: JSON.stringify({ total_count: total == null ? items.length : total, incomplete_results: false, items }) });
  async function go(page, q, fuzzy) {
    await page.goto('/xray.html');
    if (fuzzy) await page.locator('#fuzzy').check(); else await page.locator('#fuzzy').uncheck();
    await page.locator('#url').fill(q);
    await page.locator('#go').click();
    await expect(page.locator('#rrows button[data-i]').first()).toBeVisible();
  }

  test('①「開始」夠大；點輸入框被捲到最上面時不會躲在分頁列底下；輸入框 16px（iPhone 不自動放大）', async ({ page }) => {
    await page.goto('/xray.html');
    const box = await page.locator('#go').boundingBox();
    expect(box.height).toBeGreaterThanOrEqual(44);
    expect(box.width).toBeGreaterThanOrEqual(64);
    expect(await page.locator('#url').evaluate((e) => parseFloat(getComputedStyle(e).fontSize))).toBeGreaterThanOrEqual(16);
    // 手機點輸入框會把它捲到最上面；以前「開始」上緣 4 成在置頂分頁列底下，點上半部是點到分頁列
    await page.locator('#url').evaluate((e) => e.scrollIntoView({ block: 'start' }));
    const hit = await page.evaluate(() => {
      const b = document.getElementById('go').getBoundingClientRect(); let n = 0, all = 0;
      for (const fy of [0.1, 0.3, 0.5, 0.7, 0.9]) for (const fx of [0.2, 0.5, 0.8]) {
        all++; const el = document.elementFromPoint(b.left + b.width * fx, b.top + b.height * fy);
        if (el && el.closest('#go')) n++;
      }
      return n + '/' + all;
    });
    expect(hit).toBe('15/15');
    // 就算瀏覽器沒照 scroll-margin 捲（iPhone 鍵盤推上來時），focus 之後也會自己挪開
    await page.evaluate(() => { const b = document.querySelector('.bar'); window.scrollTo(0, b.getBoundingClientRect().top + scrollY - 20); });
    await page.locator('#url').focus();
    await page.waitForTimeout(500);
    const gap = await page.evaluate(() => document.querySelector('.bar').getBoundingClientRect().top - document.getElementById('tabs').getBoundingClientRect().bottom);
    expect(gap).toBeGreaterThanOrEqual(0);
  });

  test('②好幾個詞：先查「全部都要」，字典沒有的中文自動換英文（選舉 → election），夠多就只花 1 次', async ({ page, net }) => {
    net.apiOverride = (u) => (u.pathname === '/search/repositories'
      ? reply([ITEM('a/election-2026', { description: '2026 election results tracker' }), ITEM('b/other', { description: 'misc 2026 notes' })], 1814)
      : undefined);
    await go(page, '2026 選舉', true);
    expect(net.searchQ).toEqual(['2026 election']);   // 舊版：「2026 OR 選舉」（實查 110 萬筆）
    // 全部都有對到的排第一
    await expect(page.locator('#rrows button[data-i]').first()).toContainText('a/election-2026');
    await expect(page.locator('#status')).toContainText('2026 election');
  });

  test('②「全部都要」太少（< 30）才放寬成 OR 再查一次，合併時全部都要的排前面、不重複', async ({ page, net }) => {
    net.apiOverride = (u) => {
      if (u.pathname !== '/search/repositories') return undefined;
      const q = u.searchParams.get('q');
      if (!q.includes(' OR ')) return reply([ITEM('and/novel-download', { description: 'novel download tool' })], 1);
      return reply([ITEM('and/novel-download', { description: 'novel download tool' }), ITEM('or/fiction-only', { description: 'fiction' }),
        ITEM('or/download-only', { description: 'downloader' })], 500);
    };
    await go(page, '小說 下載', true);
    expect(net.searchQ.length).toBe(2);
    expect(net.searchQ[0]).toBe('novel download');
    expect(net.searchQ[1]).toContain(' OR ');
    expect((net.searchQ[1].match(/ OR /g) || []).length).toBeLessThanOrEqual(5);   // 實查：6 個 OR 就 422
    await expect(page.locator('#rrows button[data-i]')).toHaveCount(3);
    await expect(page.locator('#rrows button[data-i]').first()).toContainText('and/novel-download');
  });

  test('③「-字」在模糊模式：換成 NOT、排除的字不加同義詞、回來的結果在手機上再擋一次', async ({ page, net }) => {
    net.apiOverride = (u) => (u.pathname === '/search/repositories'
      ? reply([ITEM('ok/novel-reader', { description: 'novel reader' }), ITEM('bad/manhua', { description: '漫畫 小說 reader' })], 2)
      : undefined);
    await go(page, '小說 -漫畫', true);
    expect(net.searchQ.length).toBe(1);
    const q = net.searchQ[0];
    expect(q).toContain('NOT 漫畫');
    expect(q).not.toMatch(/(^|\s)-漫畫/);               // GitHub 不吃「-字」（實查：novel -manga 前 30 筆全有 manga）
    expect(q).not.toMatch(/manga|comic|OR 漫畫|漫畫 OR/);   // 舊版把排除的字也加了同義詞 → 反而去找漫畫
    await expect(page.locator('#rrows button[data-i]')).toHaveCount(1);
    await expect(page.locator('#rrows')).not.toContainText('bad/manhua');
  });

  test('③「-字」在一般模式也換成 NOT；GitHub 條件（-language:x）照原樣', async ({ page, net }) => {
    net.apiOverride = (u) => (u.pathname === '/search/repositories'
      ? reply([ITEM('ok/novel', { description: 'novel app' }), ITEM('bad/manga-novel', { description: 'manga novel' })], 2)
      : undefined);
    await go(page, 'novel -manga -language:python', false);
    expect(net.searchQ[0]).toBe('novel NOT manga -language:python');
    await expect(page.locator('#rrows button[data-i]')).toHaveCount(1);
  });

  test('④描述語言篩選：繁中／簡中／英文／日文／韓文分得出來、數量寫在選項上、記住選擇', async ({ page, net }) => {
    const items = [
      ITEM('tw/stock', { description: '股票看盤工具，台灣上市櫃都能看' }),
      ITEM('cn/novel', { description: '小说下载器，支持多个网站' }),
      ITEM('en/novel', { description: 'A novel downloader' }),
      ITEM('jp/novel', { description: 'ノベルをダウンロードするツール' }),
      ITEM('kr/novel', { description: '소설 다운로더' }),
      ITEM('no/desc', { description: null }),
    ];
    net.apiOverride = (u) => (u.pathname === '/search/repositories' ? reply(items, 6) : undefined);
    await go(page, 'novel tool', false);
    const rows = page.locator('#rrows button[data-i]');
    await expect(rows).toHaveCount(6);
    const pick = async (v, n, name) => {
      await page.locator('#rlang').selectOption(v);
      await expect(rows).toHaveCount(n);
      if (name) await expect(rows.first()).toContainText(name);
    };
    await expect(page.locator('#rlang option[value="zht"]')).toContainText('（1）');
    await pick('zht', 1, 'tw/stock');
    await pick('zhs', 1, 'cn/novel');
    await pick('zh', 2);
    await pick('en', 1, 'en/novel');
    await pick('ja', 1, 'jp/novel');
    await pick('ko', 1, 'kr/novel');
    await pick('zht', 1, 'tw/stock');
    await expectNoHorizontalScroll(page);
    // 重新整理再搜一次：記得上次選的
    await go(page, 'novel tool 2', false);
    await expect(page.locator('#rlang')).toHaveValue('zht');
    await expect(rows).toHaveCount(1);
  });
});

// ── 2026-10-31：A 每天自動健檢（fleet.json，只有數量）、C 追蹤搜尋（存手機、新專案標 🆕）──
// ── 2026-10-31：我的 App 清單自動從 GitHub 帳號找（ops/discover_apps.py 部署時產生 apps.json）──
test.describe('我的 App：清單自動找', () => {
  test.describe('自動找成功', () => {
    test.use({ appsJson: { source: 'auto', at: '2026-10-31T22:00:00+00:00', owner: 'xin7355-collab',
      new: ['xin7355-collab/Fruit'], hidden_private: 1,
      apps: [{ repo: 'xin7355-collab/Fruit', auto: true }, { repo: 'xin7355-collab/StockAI-DB' }] } });
    test('新開的 repo 自動出現在我的 App；說明清單會自己更新；自動找到的私人 repo 只寫數量', async ({ page }) => {
      await page.goto('/xray.html?tab=mine');
      await expect(page.locator('#myapps a[data-app="xin7355-collab/Fruit"]')).toBeVisible();
      const note = page.locator('#appsnote');
      await expect(note).toContainText('每天自動從 GitHub 帳號找（2026-10-31）');
      await expect(note).toContainText('新找到：Fruit');
      await expect(note).toContainText('另有 1 個私人 repo 沒列出');
      await expectNoHorizontalScroll(page);
    });
  });
  test.describe('自動找失敗', () => {
    test.use({ appsJson: { source: 'manual', error: '自動找 App 失敗：HTTP 403，先用手寫清單',
      apps: [{ repo: 'xin7355-collab/StockAI-DB' }] } });
    test('說出失敗原因，照樣列手寫清單（不是一片空白）', async ({ page }) => {
      await page.goto('/xray.html?tab=mine');
      await expect(page.locator('#myapps a[data-app="xin7355-collab/StockAI-DB"]')).toBeVisible();
      await expect(page.locator('#appsnote')).toContainText('⚠️ 自動找 App 失敗：HTTP 403');
    });
  });
});

test.describe('我的 App：每天自動健檢', () => {
  const FLEET = (daysAgo) => ({
    at: new Date(Date.now() - daysAgo * 864e5).toISOString(),
    apps: [
      { repo: 'xin7355-collab/Camera', score: 11, issues: 4, want: 12, installed: 0, prev_score: 9, prev_issues: 3 },
      { repo: 'xin7355-collab/StockAI-DB', score: 5, issues: 1, want: 9, installed: 8, prev_score: 7, prev_issues: 3 },
      { repo: 'xin7355-collab/k7m2q', score: 0, issues: 0, want: 2, installed: 15 },
    ],
    skipped: ['xin7355-collab/twns'], empty: ['xin7355-collab/argus-pwa'], failed: [],
  });
  test.describe('有今天的結果', () => {
    test.use({ fleetJson: FLEET(0.2) });
    test('打開就看得到分數、比上次多／少幾項、略過與空 repo 的原因；點「看內容」才即時查', async ({ page }) => {
      await page.goto('/xray.html?tab=mine');
      const box = page.locator('#autofleet');
      await expect(box.locator('tr')).toHaveCount(3);
      await expect(box).toContainText('每天自動健檢');
      await expect(box.locator('tr').nth(0)).toContainText('比上次多 1 項');
      await expect(box.locator('tr').nth(1)).toContainText('比上次少 2 項');
      await expect(box.locator('tr').nth(2)).toContainText('沒有明顯缺口');
      await expect(box).toContainText('SKL_READ_TOKEN');
      await expect(box).toContainText('argus-pwa');
      await expect(box).not.toContainText('天沒更新');
      await expectNoHorizontalScroll(page);
      await box.locator('[data-autoapp="xin7355-collab/Camera"]').click();
      await expect(page.locator('#fleetbar')).toBeVisible();
      await expect(box).toBeHidden();                   // 點進單一 App 時一起收起來
    });
  });
  test.describe('結果太舊', () => {
    test.use({ fleetJson: FLEET(5) });
    test('超過 3 天沒更新要說出來', async ({ page }) => {
      await page.goto('/xray.html?tab=mine');
      await expect(page.locator('#autofleet')).toContainText('5 天沒更新');
    });
  });
  test('沒有 fleet.json（本機、還沒部署過）就不顯示，不報錯', async ({ page }) => {
    await page.goto('/xray.html?tab=mine');
    await expect(page.locator('#myapps')).toContainText('我的 App');
    await expect(page.locator('#autofleet')).toBeEmpty();
  });
});

test.describe('追蹤搜尋', () => {
  const ITEM = (full) => ({ id: full.length * 13 + full.charCodeAt(0), name: full.split('/')[1], full_name: full, description: 'novel thing',
    stargazers_count: 10, language: 'JavaScript', pushed_at: new Date().toISOString(), created_at: new Date().toISOString(),
    size: 10, html_url: 'https://github.com/' + full, topics: [], license: null, homepage: null, has_pages: false });
  const reply = (items) => ({ status: 200, headers: RATE, contentType: 'application/json',
    body: JSON.stringify({ total_count: items.length, incomplete_results: false, items }) });

  test('追蹤 → 隔天打開只查一次（加 created:>=）→ 新專案在追蹤列標 🆕 → 點開只列新的、看過就歸零', async ({ page, net }) => {
    net.apiOverride = (u) => {
      if (u.pathname !== '/search/repositories') return undefined;
      const q = u.searchParams.get('q');
      if (q.includes('created:>=')) return reply([ITEM('old/seen-novel'), ITEM('new/fresh-novel')]);
      return reply([ITEM('old/seen-novel'), ITEM('old/other-novel')]);
    };
    await page.goto('/xray.html');
    await page.locator('#fuzzy').uncheck();
    await page.locator('#url').fill('novel');
    await page.locator('#go').click();
    await expect(page.locator('#rrows button[data-i]')).toHaveCount(2);
    await page.locator('#watchbtn').click();
    await expect(page.locator('#watchbtn')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#watches a[data-w]')).toHaveCount(1);
    // 剛追蹤：重新打開不會馬上再查
    const before = net.searchQ.length;
    await page.goto('/xray.html');
    await page.waitForTimeout(800);
    expect(net.searchQ.length).toBe(before);
    // 假裝過了兩天
    await page.evaluate(() => { const a = JSON.parse(localStorage.getItem('xray-watch-v1')); a[0].last = Date.now() - 2 * 864e5; localStorage.setItem('xray-watch-v1', JSON.stringify(a)); });
    await page.goto('/xray.html');
    await expect(page.locator('#watches .wnew')).toHaveText('🆕1');
    const checkQ = net.searchQ[net.searchQ.length - 1];
    expect(checkQ).toMatch(/^novel created:>=\d{4}-\d{2}-\d{2}$/);
    // 點開：只列追蹤後新建立的、標 🆕；追蹤列歸零；追蹤鈕仍顯示「已追蹤」
    await page.locator('#watches a[data-w]').click();
    await expect(page.locator('#rrows')).toContainText('new/fresh-novel');
    await expect(page.locator('#rrows button[data-i]', { hasText: 'new/fresh-novel' })).toContainText('追蹤後新出現');
    await expect(page.locator('#rrows button[data-i]', { hasText: 'old/seen-novel' })).not.toContainText('追蹤後新出現');
    await expect(page.locator('#watches .wnew')).toHaveCount(0);
    await expect(page.locator('#watchbtn')).toHaveAttribute('aria-pressed', 'true');
    // 取消追蹤
    await page.locator('#watchbtn').click();
    await expect(page.locator('#watches a[data-w]')).toHaveCount(0);
  });
});

// ── 2026-10-31：⛏️ 網址挖礦（只讀 repo 自己的檔案，⛔ 不探測網站；金鑰先遮）──
test.describe('網址挖礦', () => {
  test('挖出線上成品、API、下載、文件、社群並分類；雜訊收起來；金鑰遮住；不讀 node_modules／敏感檔／lock 檔', async ({ page, net }) => {
    await scan(page, 'demo/url-app');
    const rawBefore = net.raw;
    await page.locator('#mine-go').click();
    const box = page.locator('#mine');
    await expect(box.locator('details[data-mcat="live"]')).toContainText('https://url-app.vercel.app');
    await expect(box.locator('details[data-mcat="live"]')).toContainText('https://app.urlapp.dev');        // 從 CNAME 來的
    const api = box.locator('details[data-mcat="api"]');
    await expect(api).toContainText('https://api.urlapp.dev/v1/items');
    await expect(api).toContainText('wss://rt.urlapp.dev/socket');
    await expect(api.locator('li', { hasText: 'api.urlapp.dev/v1/items' })).toContainText('共 2 處');      // 同一個網址合併
    await expect(box.locator('details[data-mcat="data"]')).toContainText('huggingface.co/datasets/demo/set');
    await expect(box.locator('details[data-mcat="data"]')).toContainText('releases/download/v1/app.apk');
    await expect(box.locator('details[data-mcat="docs"]')).toContainText('docs.urlapp.dev/guide');
    await expect(box.locator('details[data-mcat="social"]')).toContainText('discord.gg/abc123');
    // 雜訊：徽章、授權、自己 repo 的 issues —— 收在預設摺起來的那一組
    const noise = box.locator('details[data-mcat="noise"]');
    await expect(noise).not.toHaveAttribute('open', '');
    await expect(noise).toContainText('img.shields.io');
    await expect(noise).toContainText('github.com/demo/url-app/issues');
    // 金鑰：webhook 與 key= 都遮住、不給連結；整頁看不到原本的金鑰
    const html = await box.innerHTML();
    expect(html).not.toContain('XXXXSECRETXXXX');
    expect(html).not.toContain('AIzaSyFAKEFAKE123456');
    await expect(box).toContainText('hooks.slack.com/services/•••');
    await expect(box).toContainText('key=•••');
    await expect(box).toContainText('像金鑰的東西，已經遮住');
    // 不該讀的：node_modules、被判為敏感的 .env.production、lock 檔；樣板變數網址不算
    for (const bad of ['should-not-read', 'secret-internal', 'lockfile-only', 'cdn.urlapp.dev/$']) expect(html).not.toContain(bad);
    // 出處：檔案:行號，連到 GitHub
    await expect(api.locator('a.msrc').first()).toHaveAttribute('href', /\/blob\/main\/src\/api\.js#L1$/);
    expect(net.raw - rawBefore).toBeGreaterThan(0);
    // 複製：金鑰已遮、不含雜訊
    await box.locator('#mine-copy').click();
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip).toContain('https://url-app.vercel.app');
    expect(clip).not.toContain('AIzaSyFAKEFAKE123456');
    expect(clip).not.toContain('img.shields.io');
    await expectNoHorizontalScroll(page);
  });

  test('換一個專案，上一個的挖礦結果不會留著', async ({ page }) => {
    await scan(page, 'demo/url-app');
    await page.locator('#mine-go').click();
    await expect(page.locator('#mine details').first()).toBeVisible();
    await scan(page, 'demo/pypkg');
    await expect(page.locator('#mine')).toBeEmpty();
    await expect(page.locator('#mine-go')).toBeEnabled();
  });
});
