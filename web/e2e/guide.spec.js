// App 開發全流程（web/dev-guide.html）
const { test, expect, expectNoHorizontalScroll } = require('./fixtures');

const FOLLOW = /執行方案[\s\S]*直接照方案做/;
const clip = (page) => page.evaluate(() => navigator.clipboard.readText());

test('技能庫網頁有連到這一頁', async ({ page }) => {
  await page.goto('/index.html');
  await page.locator('a[href="dev-guide.html"]').click();
  await expect(page).toHaveURL(/dev-guide\.html$/);
  await expect(page.locator('h1')).toHaveText('App 開發全流程');
});

test('五個分頁都能切換，手機寬度不會左右滑（寬表格只在框內滑）', async ({ page }) => {
  await page.goto('/dev-guide.html');
  for (const tab of ['flow', 'platform', 'cost', 'caution', 'start']) {
    await page.locator(`nav.tabs button[data-tab="${tab}"]`).click();
    await expect(page.locator('#tab-' + tab)).toBeVisible();
    await expectNoHorizontalScroll(page);
  }
});

test('外部連結都是 https、開新分頁、帶 noopener', async ({ page }) => {
  await page.goto('/dev-guide.html');
  const links = await page.locator('a[href^="http"]').evaluateAll((as) => as.map((a) => ({ h: a.href, t: a.target, r: a.rel })));
  expect(links.length).toBeGreaterThan(20);
  for (const l of links) {
    expect(l.h, l.h).toMatch(/^https:\/\//);
    expect(l.t, l.h).toBe('_blank');
    expect(l.r, l.h).toContain('noopener');
  }
});

test('費用表每一列都有出處，或明寫「未查證」', async ({ page }) => {
  await page.goto('/dev-guide.html');
  await page.locator('nav.tabs button[data-tab="cost"]').click();
  const rows = page.locator('#tab-cost table tr').filter({ has: page.locator('td') });
  const n = await rows.count();
  expect(n).toBeGreaterThan(8);
  for (let i = 0; i < n; i++) {
    const row = rows.nth(i);
    const hasSrc = await row.locator('a.src').count();
    const text = await row.innerText();
    expect(hasSrc > 0 || text.includes('未查證') || text.includes('AI 指南'), text.slice(0, 40)).toBe(true);
  }
});

test('出處清單會自動整理頁面上所有出處，只靠搜尋確認的標 ※', async ({ page }) => {
  await page.goto('/dev-guide.html');
  const unique = await page.locator('main a.src').evaluateAll((as) => new Set(as.map((a) => a.href)).size);
  await expect(page.locator('#srcs li')).toHaveCount(unique);
  await expect(page.locator('#srcs li').filter({ hasText: '※' }).first()).toBeVisible();
});

test.describe('起手式產生器', () => {
  test('勾選會改變 A／B 兩段，B 段裝完要列方案並接著做', async ({ page }) => {
    await page.goto('/dev-guide.html');
    await page.locator('nav.tabs button[data-tab="start"]').click();
    await page.locator('#gDesc').fill('每天整理我關注的 10 檔股票');
    await page.locator('#pIos').check();
    await page.locator('#nAi').check();
    await page.locator('#nStock').check();

    const chat = await page.locator('#outChat').inputValue();
    expect(chat).toContain('每天整理我關注的 10 檔股票');
    expect(chat).toContain('4.2');
    expect(chat).toContain('金鑰不能放在網頁裡');
    expect(chat).toContain('投顧法規');

    const code = await page.locator('#outCode').inputValue();
    expect(code).toContain('core,pwa,actions,stock,ai');
    expect(code).toMatch(FOLLOW);
    await expect(page.locator('#gPacks')).toHaveText('core, pwa, actions, stock, ai');
  });

  test('新選項會帶出對應的規定、做法與技能組合', async ({ page }) => {
    await page.goto('/dev-guide.html');
    await page.locator('nav.tabs button[data-tab="start"]').click();
    await page.locator('#gMode').selectOption('existing');
    await page.locator('#gType').selectOption('shop');
    await page.locator('#pLine').check();
    await page.locator('#nScrape').check();
    await page.locator('#nAgent').check();
    await page.locator('#nNotify').selectOption('push');
    await page.locator('#dStore').selectOption('sync');

    const chat = await page.locator('#outChat').inputValue();
    expect(chat).toContain('LINE Notify 已在 2025/3/31 停止');
    expect(chat).toContain('iOS 16.4');
    expect(chat).toContain('robots.txt');
    expect(chat).toContain('noindex');
    expect(chat).not.toContain('英文的 repo 名稱');   // 改版不用取名

    const code = await page.locator('#outCode').inputValue();
    expect(code).toContain('這是已經在做的 App repo');
    expect(code).toContain('不要整個重寫');
    expect(code).toContain('Messaging API');
    expect(code).toContain('web-to-markdown');
    expect(code).toMatch(FOLLOW);
    // 固定順序：core, pwa, actions, backend, stock, ai, agent, security, python, video, visual, workflow
    await expect(page.locator('#gPacks')).toHaveText('core, pwa, actions, backend, ai, agent, security, python');
    // 勾了 AI Agent 才出現往 Agent 頁的提示
    await expect(page.locator('#agentHint a[href="agent.html"]')).toBeVisible();
    await page.locator('#nAgent').uncheck();
    await expect(page.locator('#agentHint')).toBeHidden();
  });

  test('全部重設回到預設；舊版存檔（沒有新欄位）照樣讀得進來', async ({ page }) => {
    await page.goto('/dev-guide.html');
    await page.evaluate(() => localStorage.setItem('guide-gen', JSON.stringify({ gDesc: '舊的 App', gWho: '自己', nAi: true, gType: 'removed-type' })));
    await page.reload();
    await page.locator('nav.tabs button[data-tab="start"]').click();
    await expect(page.locator('#gDesc')).toHaveValue('舊的 App');
    await expect(page.locator('#nAi')).toBeChecked();
    await expect(page.locator('#qTest')).toBeChecked();          // 舊存檔沒有這欄 → 預設勾選
    await expect(page.locator('#gType')).toHaveValue('tool');    // 改名或不存在的選項 → 回預設

    await page.locator('#nVideo').check();
    await expect(page.locator('#gPacks')).toContainText('video');
    await page.locator('#genReset').click();
    await expect(page.locator('#gDesc')).toHaveValue('');
    await expect(page.locator('#nAi')).not.toBeChecked();
    await expect(page.locator('#nVideo')).not.toBeChecked();
    await expect(page.locator('#qTest')).toBeChecked();
    await expect(page.locator('#gPacks')).toHaveText('core, pwa');
  });

  test('複製的內容跟畫面一致，重新整理後還記得填過的內容與分頁', async ({ page }) => {
    await page.goto('/dev-guide.html');
    await page.locator('nav.tabs button[data-tab="start"]').click();
    await page.locator('#gDesc').fill('家庭記帳');
    await page.locator('#nLogin').check();
    await page.locator('button[data-copy="outChat"]').click();
    expect(await clip(page)).toBe(await page.locator('#outChat').inputValue());
    await page.locator('button[data-copy="outCode"]').click();
    expect(await clip(page)).toBe(await page.locator('#outCode').inputValue());
    expect(await clip(page)).toContain('security');

    await page.reload();
    await expect(page.locator('#tab-start')).toBeVisible();
    await expect(page.locator('#gDesc')).toHaveValue('家庭記帳');
    await expect(page.locator('#nLogin')).toBeChecked();
  });
});

test('「功能的眉角」把起手式選項用到的規定都附了出處', async ({ page }) => {
  await page.goto('/dev-guide.html');
  await page.locator('nav.tabs button[data-tab="caution"]').click();
  const card = page.locator('#tab-caution .card').filter({ hasText: '功能的眉角' });
  for (const word of ['LINE Notify', 'Telegram', '捷徑', 'OpenStreetMap', '開放資料', '44×44']) {
    const li = card.locator('li').filter({ hasText: word });
    await expect(li, word).toHaveCount(1);
    expect(await li.locator('a.src').count(), word + ' 要有出處').toBeGreaterThan(0);
  }
});

test('「裝完接著做」規則跟技能庫網頁一字不差（兩邊要一致）', async ({ page }) => {
  const tail = (s) => s.slice(s.indexOf('裝好後不要停'));
  await page.goto('/index.html');
  const fromIndex = tail(await page.locator('#autoPrompt').inputValue());
  await page.goto('/dev-guide.html');
  await page.locator('nav.tabs button[data-tab="start"]').click();
  const fromGuide = tail(await page.locator('#outCode').inputValue());
  expect(fromIndex.length).toBeGreaterThan(100);
  expect(fromGuide).toBe(fromIndex);
});
