// Repo X-Ray（web/xray.html）：掃描判讀、專案地圖、打包給 AI、搜尋結果、勾選、說明文件、返回
const { test, expect, expectNoHorizontalScroll } = require('./fixtures');

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
