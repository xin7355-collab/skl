// AI Agent 製作流程（web/agent.html）
const { test, expect, expectNoHorizontalScroll } = require('./fixtures');

const FOLLOW = /執行方案[\s\S]*直接照方案做/;
const clip = (page) => page.evaluate(() => navigator.clipboard.readText());
const rec = (page) => page.locator('#aRec');

async function openStart(page) {
  await page.goto('/agent.html');
  await page.locator('nav.tabs button[data-tab="start"]').click();
  await expect(page.locator('#tab-start')).toBeVisible();
}

test('技能庫網頁與 App 開發全流程都連得到這一頁', async ({ page }) => {
  await page.goto('/index.html');
  await page.locator('a[href="agent.html"]').click();
  await expect(page).toHaveURL(/agent\.html$/);
  await expect(page.locator('h1')).toHaveText('AI Agent 製作流程');
  await page.goto('/dev-guide.html');
  await expect(page.locator('header a[href="agent.html"]')).toBeVisible();
});

test('六個分頁都能切換，手機寬度不會左右滑', async ({ page }) => {
  await page.goto('/agent.html');
  for (const tab of ['concept', 'ways', 'steps', 'cost', 'caution', 'start']) {
    await page.locator(`nav.tabs button[data-tab="${tab}"]`).click();
    await expect(page.locator('#tab-' + tab)).toBeVisible();
    await expectNoHorizontalScroll(page);
  }
});

test('外部連結都是 https、開新分頁、帶 noopener', async ({ page }) => {
  await page.goto('/agent.html');
  const links = await page.locator('a[href^="http"]').evaluateAll((as) => as.map((a) => ({ h: a.href, t: a.target, r: a.rel })));
  expect(links.length).toBeGreaterThan(15);
  for (const l of links) {
    expect(l.h, l.h).toMatch(/^https:\/\//);
    expect(l.t, l.h).toBe('_blank');
    expect(l.r, l.h).toContain('noopener');
  }
});

test('費用表每一列都有出處，或明寫「未查證」；出處清單不重複', async ({ page }) => {
  await page.goto('/agent.html');
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
  const unique = await page.locator('main a.src').evaluateAll((as) => new Set(as.map((a) => a.href)).size);
  await expect(page.locator('#srcs li')).toHaveCount(unique);
});

test.describe('Agent 起手式', () => {
  test('預設（只有自己看結果）建議 Cowork：B 段是交辦任務，不裝技能', async ({ page }) => {
    await openStart(page);
    await page.locator('#aTask').fill('整理這週的新聞重點');
    await expect(rec(page)).toHaveAttribute('data-way', 'cowork');
    await expect(page.locator('#aPacksRow')).toBeHidden();
    const b = await page.locator('#aOutB').inputValue();
    expect(b).toContain('【任務】整理這週的新聞重點');
    expect(b).not.toMatch(FOLLOW);
    expect(b).not.toContain('app-bootstrap');
  });

  test('四個範例對應四種做法，指令內容跟著換', async ({ page }) => {
    await openStart(page);
    const cases = [
      ['stock', 'routine', ['agent/README.md', 'tw-stock', 'Routine']],
      ['mail', 'cowork', ['【排程】', '只讀不寫']],
      ['fix', 'action', ['--max-turns', 'timeout-minutes', 'ANTHROPIC_API_KEY', 'allowed_non_write_users']],
      ['helper', 'api', ['ai-model-router', '花費上限', '不放網址']],
    ];
    for (const [ex, way, words] of cases) {
      await page.locator(`[data-example="${ex}"]`).click();
      await expect(rec(page), ex).toHaveAttribute('data-way', way);
      const b = await page.locator('#aOutB').inputValue();
      for (const w of words) expect(b, `${ex} 的 B 段要有「${w}」`).toContain(w);
      if (way !== 'cowork') {
        expect(b).toMatch(FOLLOW);
        await expect(page.locator('#aPacks')).toContainText('agent');
      }
    }
  });

  test('範例會把上一個範例勾的工具清掉', async ({ page }) => {
    await openStart(page);
    await page.locator('[data-example="stock"]').click();
    await expect(page.locator('#tStock')).toBeChecked();
    await page.locator('[data-example="mail"]').click();
    await expect(page.locator('#tStock')).not.toBeChecked();
    await expect(page.locator('#tGoogle')).toBeChecked();
    await expect(page.locator('#aWhen')).toHaveValue('daily');
  });

  test('危險組合會跳警告：讀外部網頁＋對外發送＋全部自動', async ({ page }) => {
    await openStart(page);
    await expect(page.locator('#aWarns li')).toHaveCount(0);
    await page.locator('#tWeb').check();
    await page.locator('#tWrite').check();
    await page.locator('#aAsk').selectOption('never');
    const warns = page.locator('#aWarns li');
    await expect(warns.filter({ hasText: '提示注入' })).toHaveCount(1);
    await expect(warns.filter({ hasText: '沒有人攔得住' })).toHaveCount(1);
    // A 段也要把防護寫進去
    await expect(page.locator('#aOutA')).toHaveValue(/只當資料、不當指令/);
  });

  test('要 API 金鑰的做法配「只用訂閱」會提醒衝突；選了預算就不再提醒', async ({ page }) => {
    await openStart(page);
    await page.locator('#aWhen').selectOption('comment');
    await expect(rec(page)).toHaveAttribute('data-way', 'action');
    await expect(page.locator('#aWarns li').filter({ hasText: '衝突' })).toHaveCount(1);
    await page.locator('#aBudget').selectOption('5');
    await expect(page.locator('#aWarns li').filter({ hasText: '衝突' })).toHaveCount(0);
    await expect(page.locator('#aOutB')).toHaveValue(/每月 US\$5/);
  });

  test('複製的內容跟畫面一致，重新整理後還記得填過的內容與分頁', async ({ page }) => {
    await openStart(page);
    await page.locator('[data-example="stock"]').click();
    await page.locator('#aTime').fill('每天 06:40');
    await page.locator('button[data-copy="aOutA"]').click();
    expect(await clip(page)).toBe(await page.locator('#aOutA').inputValue());
    await page.locator('button[data-copy="aOutB"]').click();
    expect(await clip(page)).toBe(await page.locator('#aOutB').inputValue());
    expect(await clip(page)).toContain('每天 06:40');

    await page.reload();
    await expect(page.locator('#tab-start')).toBeVisible();
    await expect(page.locator('#aTime')).toHaveValue('每天 06:40');
    await expect(page.locator('#tStock')).toBeChecked();
    await expect(rec(page)).toHaveAttribute('data-way', 'routine');
  });

  test('舊版存檔的選項不在清單裡時，回到預設而不是空白', async ({ page }) => {
    await page.goto('/agent.html');
    await page.evaluate(() => localStorage.setItem('agent-gen', JSON.stringify({ aWhen: 'hourly', aTask: '舊的任務' })));
    await page.reload();
    await page.locator('nav.tabs button[data-tab="start"]').click();
    await expect(page.locator('#aWhen')).toHaveValue('ask');
    await expect(page.locator('#aTask')).toHaveValue('舊的任務');
  });
});

test('「裝完接著做」規則跟技能庫網頁一字不差（三頁要一致）', async ({ page }) => {
  const tail = (s) => s.slice(s.indexOf('裝好後不要停'));
  await page.goto('/index.html');
  const fromIndex = tail(await page.locator('#autoPrompt').inputValue());
  await openStart(page);
  await page.locator('[data-example="stock"]').click();
  const fromAgent = tail(await page.locator('#aOutB').inputValue());
  expect(fromIndex.length).toBeGreaterThan(100);
  expect(fromAgent).toBe(fromIndex);
});
