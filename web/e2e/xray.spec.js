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
