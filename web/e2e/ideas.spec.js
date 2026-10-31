// 技能庫首頁的工具卡（2026-10-31 使用者：不要一行一行的文字連結）與 App 點子頁（web/ideas.html）
const { test, expect, expectNoHorizontalScroll } = require('./fixtures');
const IDEAS = require('../ideas.json');

test.describe('首頁工具卡', () => {
  test('六張卡兩欄排、整張可點、夠大好按，連到各頁', async ({ page }) => {
    await page.goto('/index.html');
    const cards = page.locator('header .tools a.tool');
    await expect(cards).toHaveCount(6);
    const hrefs = await cards.evaluateAll((as) => as.map((a) => a.getAttribute('href')));
    expect(hrefs.sort()).toEqual(['agent.html', 'ai.html', 'dev-guide.html', 'ideas.html', 'prompts.html', 'xray.html']);
    const boxes = [];
    for (let i = 0; i < 6; i++) boxes.push(await cards.nth(i).boundingBox());
    for (const b of boxes) expect(b.height).toBeGreaterThanOrEqual(56);
    expect(new Set(boxes.map((b) => Math.round(b.x))).size).toBe(2);   // 兩欄
    expect(Math.abs(boxes[0].y - boxes[1].y)).toBeLessThan(2);          // 同一列
    // 舊的一行一行文字連結不能再出現
    await expect(page.locator('header p.sub a')).toHaveCount(0);
    await expectNoHorizontalScroll(page);
    await cards.filter({ hasText: 'App 點子' }).click();
    await expect(page).toHaveURL(/ideas\.html$/);
    await expect(page.locator('h1')).toHaveText('App 點子');
  });
});

test.describe('App 點子頁', () => {
  const rows = (page) => page.locator('#list details.idea');

  test('全部點子都列出來；類別與難度可以篩、數量對、重新整理後記得', async ({ page }) => {
    await page.goto('/ideas.html');
    await expect(rows(page)).toHaveCount(IDEAS.ideas.length);
    const nInvest = IDEAS.ideas.filter((i) => i.cat === '投資').length;
    await expect(page.locator('#cats [data-cat="投資"] i')).toHaveText(String(nInvest));
    await page.locator('#cats [data-cat="投資"]').click();
    await expect(rows(page)).toHaveCount(nInvest);
    await page.locator('#cats [data-cat="全部"]').click();
    const nSmall = IDEAS.ideas.filter((i) => i.level === '小').length;
    await page.locator('#levels [data-level="小"]').click();
    await expect(rows(page)).toHaveCount(nSmall);
    await page.reload();
    await expect(page.locator('#levels [data-level="小"]')).toHaveAttribute('aria-pressed', 'true');
    await expect(rows(page)).toHaveCount(nSmall);
    await expectNoHorizontalScroll(page);
  });

  test('打開一個點子：第一版、之後加、要注意、技能組合、費用附官方出處；開工指令可以直接貼給 Claude', async ({ page }) => {
    const it = IDEAS.ideas.find((i) => i.id === 'stock-close');
    await page.goto('/ideas.html');
    const card = page.locator('details.idea[data-id="stock-close"]');
    await card.locator('summary').click();
    for (const t of ['第一版只做', '之後再加', '要注意', '會裝的 skl 技能組合', '費用']) await expect(card).toContainText(t);
    for (const m of it.mvp) await expect(card).toContainText(m);
    const links = card.locator('[data-cost] a.src');
    await expect(links).toHaveCount(it.cost.src.length);
    for (const href of await links.evaluateAll((as) => as.map((a) => a.href))) expect(href).toMatch(/^https:\/\//);
    await expect(card.locator('[data-xray]')).toHaveAttribute('href', 'xray.html?q=' + encodeURIComponent(it.xq));
    await card.locator('[data-copy]').click();
    await expect(page.locator('#toast')).toContainText('已複製');
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip).toContain('台股收盤小幫手');
    expect(clip).toContain('--packs ' + it.packs.join(','));     // 用 app-bootstrap 裝組合，跟首頁同一個指令
    expect(clip).toContain('--dry-run');
    for (const m of it.mvp) expect(clip).toContain(m);
    expect(clip).toContain('繁體中文');
  });

  test('開工步驟有四步、附 GitHub 與 Claude Code 的網址', async ({ page }) => {
    await page.goto('/ideas.html');
    await page.locator('#how summary').click();
    await expect(page.locator('#steps li')).toHaveCount(4);
    await expect(page.locator('#steps a[href="https://github.com/new"]')).toBeVisible();
    await expect(page.locator('#steps a[href="https://claude.ai/code"]')).toBeVisible();
    await expect(page.locator('#checked')).toHaveText(IDEAS.checked_at);
  });

  test('清單載入失敗要說出來（不是一片空白）', async ({ page }) => {
    await page.route('**/ideas.json*', (r) => r.fulfill({ status: 404, body: '' }));
    await page.goto('/ideas.html');
    await expect(page.locator('#list')).toContainText('載入失敗');
  });
});
