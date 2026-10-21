// 提示詞庫（web/prompts.html）：分類兩層、模糊搜尋、填空、送到 AI 的網址、先複製再開、收藏、返回關面板、中文翻譯保護空格
const { test, expect, expectNoHorizontalScroll } = require('./fixtures');

const shardOf = (i) => parseInt(i.slice(0, 2), 16) % 32;
const CATS = [
  { id: 'dev', name: '程式開發', color: '#2563eb', subs: [{ id: 'cli', name: '終端機與系統' }, { id: 'code', name: '程式碼與重構' }] },
  { id: 'lang', name: '語言翻譯', color: '#16a34a', subs: [{ id: 'trans', name: '翻譯' }] },
  { id: 'write', name: '寫作創作', color: '#ea580c', subs: [] },
  { id: 'other', name: '其他', color: '#64748b', subs: [] },
];
const FULL = {
  a1000001: 'I want you to act as a linux terminal. Reply only with terminal output.',
  b2000002: 'I want you to act as an English translator and improver for ${Language:Chinese} text.\nKeep the meaning.',
  c3000003: 'I want you to act as a poet. Write about ${Topic}.',
  d4000004: 'Long spec. ' + 'x'.repeat(7000),
};
const ITEMS = [
  { i: 'a1000001', t: 'Linux Terminal', z: 'Linux 終端機', c: 'dev', s: 'cli', k: 'T', d: 1, v: 0, n: FULL.a1000001.length, p: FULL.a1000001.slice(0, 60), a: 'f' },
  { i: 'b2000002', t: 'English Translator and Improver', z: '英語翻譯和改進者', c: 'lang', s: 'trans', k: 'T', d: 0, v: 1, n: 120, p: 'I want you to act as an English translator…', a: 'f' },
  { i: 'c3000003', t: 'Poet', z: '詩人', c: 'write', s: '', k: 'T', d: 0, v: 1, n: 60, p: 'I want you to act as a poet.', a: '' },
  { i: 'd4000004', t: 'Huge Spec Writer', z: '超長規格', c: 'dev', s: 'code', k: 'S', d: 1, v: 0, n: FULL.d4000004.length, p: 'Long spec.', a: '' },
];

async function mockPrompts(page, { fail = false, items = ITEMS } = {}) {
  await page.route('**/prompts/*.json', (route) => {
    const name = new URL(route.request().url()).pathname.split('/').pop();
    if (fail) return route.fulfill({ status: 500, body: '' });
    if (name === 'index.json') {
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
        source: { repo: 'https://github.com/f/awesome-chatgpt-prompts', commit: 'abc123', license: 'CC0-1.0', count: items.length, built: '2026-10-21' },
        cats: CATS, items }) });
    }
    const n = +name.match(/p-(\d+)\.json/)[1];
    const sh = {};
    for (const [i, t] of Object.entries(FULL)) if (shardOf(i) === n) sh[i] = t;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(sh) });
  });
}
const cards = (page) => page.locator('#list .pc[data-i]');
// 測試不真的連到 ChatGPT 等網站（fixtures 會擋外部連線）：攔下連結的預設動作，但讓頁面自己的 click 處理照跑
async function noNavigate(page) {
  await page.evaluate(() => document.addEventListener('click', (e) => { if (e.target.closest('#d-ai a')) e.preventDefault(); }, true));
}

test('載入後列出全部、分類兩層、模糊搜尋（中文同義詞、拼錯）', async ({ page }) => {
  await mockPrompts(page);
  await page.goto('/prompts.html');
  await expect(cards(page)).toHaveCount(4);
  await expect(page.locator('#count')).toHaveText('共 4 則');
  await expect(page.locator('#foot')).toContainText('CC0');

  await page.locator('#cats [data-cat="dev"]').click();
  await expect(cards(page)).toHaveCount(2);
  await expect(page.locator('#subs')).toBeVisible();
  await page.locator('#subs [data-sub="cli"]').click();
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page).first()).toContainText('Linux 終端機');
  await page.locator('#cats [data-cat="all"]').click();
  await expect(page.locator('#subs')).toBeHidden();

  await page.locator('#q').fill('翻譯');
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page).first()).toContainText('英語翻譯和改進者');
  await page.locator('#q').fill('transltor');      // 拼錯一個字
  await expect(cards(page)).toHaveCount(1);
  await page.locator('#q').fill('zzzzqqq');
  await expect(page.locator('#list .empty')).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test('有空格的提示詞：先填空，送到 AI 的網址帶著填好的內容；不支援網址帶入的先複製再開', async ({ page }) => {
  await mockPrompts(page);
  await page.goto('/prompts.html');
  await cards(page).filter({ hasText: '英語翻譯' }).locator('[data-act="send"]').click();
  await expect(page.locator('#sheet')).toBeVisible();
  const inp = page.locator('#d-vars input[data-var="Language"]');
  await expect(inp).toHaveAttribute('placeholder', 'Chinese');
  await expect(page.locator('#d-text')).toContainText('for Chinese text');   // 留白用預設值
  await inp.fill('Japanese');
  await expect(page.locator('#d-text')).toContainText('for Japanese text');

  const gpt = page.locator('#d-ai a', { hasText: 'ChatGPT' });
  const href = await gpt.getAttribute('href');
  expect(href.startsWith('https://chatgpt.com/?q=')).toBe(true);
  expect(decodeURIComponent(href.split('?q=')[1])).toBe(
    'I want you to act as an English translator and improver for Japanese text.\nKeep the meaning.');
  await expect(page.locator('#d-ai a', { hasText: 'Claude' })).toHaveAttribute('href', /^https:\/\/claude\.ai\/new\?q=/);
  await expect(page.locator('#d-ai a', { hasText: 'Gemini' })).toHaveAttribute('href', 'https://gemini.google.com/app');

  await noNavigate(page);
  await page.locator('#d-ai a', { hasText: 'Gemini' }).click();
  // 先複製：成功出現提示，被瀏覽器擋就開手動複製面板（兩種都算有處理）
  await expect(page.locator('#toast.show, #manual:not([hidden])').first()).toBeVisible();
  if (await page.locator('#manual').isVisible()) {
    await expect(page.locator('#m-text')).toHaveValue(/for Japanese text/);
    await page.locator('#m-close').click();
  }
  await page.locator('#d-close').click();
  await expect(page.locator('#sheet')).toBeHidden();
  // 用過一次 → 卡片顯示次數，「用過的」篩選找得到
  await expect(cards(page).filter({ hasText: '英語翻譯' }).locator('.use')).toHaveText('🔥 1');
  await page.locator('#flt [data-flt="used"]').click();
  await expect(cards(page)).toHaveCount(1);
});

test('太長的提示詞：網址放不下，全部改成先複製再開並說明', async ({ page }) => {
  await mockPrompts(page);
  await page.goto('/prompts.html');
  await cards(page).filter({ hasText: '超長規格' }).click();
  await expect(page.locator('#d-text')).toContainText('Long spec.');
  await expect(page.locator('#d-ai a', { hasText: 'ChatGPT' })).toHaveAttribute('href', 'https://chatgpt.com/');
  await expect(page.locator('#d-note')).toContainText('太長');
});

test('收藏記在手機、重新整理還在；手機的返回先關面板；?id= 直接打開那則', async ({ page }) => {
  await mockPrompts(page);
  await page.goto('/prompts.html');
  await cards(page).filter({ hasText: '詩人' }).locator('[data-act="fav"]').click();
  await expect(cards(page).filter({ hasText: '詩人' }).locator('[data-act="fav"]')).toHaveText('★ 已收藏');
  await page.reload();
  await page.locator('#flt [data-flt="fav"]').click();
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page).first()).toContainText('詩人');

  await cards(page).first().click();
  await expect(page.locator('#sheet')).toBeVisible();
  await page.goBack();
  await expect(page.locator('#sheet')).toBeHidden();
  await expect(page).toHaveURL(/prompts\.html/);

  await page.goto('/prompts.html?id=a1000001');
  await expect(page.locator('#sheet')).toBeVisible();
  await expect(page.locator('#d-title')).toHaveText('Linux 終端機');
  await expect(page.locator('#d-text')).toContainText('linux terminal');
});

test('中文翻譯：點了才翻，空格 ${...} 翻完還在、照樣能填', async ({ page, net }) => {
  await mockPrompts(page);
  await page.goto('/prompts.html');
  const before = net.translate;
  await cards(page).filter({ hasText: '詩人' }).click();
  await expect(page.locator('#d-text')).toContainText('act as a poet');
  expect(net.translate).toBe(before);                 // 打開不翻
  await page.locator('#d-lang [data-lang="zh"]').click();
  await expect(page.locator('#d-text')).toContainText('中：');
  await expect(page.locator('#d-text')).toContainText('${Topic}');
  await page.locator('#d-vars input[data-var="Topic"]').fill('海');
  await expect(page.locator('#d-text')).toContainText('Write about 海');
  expect(net.translate).toBeGreaterThan(before);
});

test('資料載入失敗：說明怎麼辦，不是空白', async ({ page }) => {
  await mockPrompts(page, { fail: true });
  await page.goto('/prompts.html');
  await expect(page.locator('#list .err')).toContainText('重新整理');
});

test('標題還沒有中文：手機只翻這一頁的、翻好記住，重新整理不再送', async ({ page, net }) => {
  const items = ITEMS.map((it) => (it.i === 'c3000003' ? Object.assign({}, it, { z: undefined }) : it));
  await mockPrompts(page, { items });
  await page.goto('/prompts.html');
  await expect(cards(page).filter({ hasText: 'Poet' }).locator('h3')).toHaveText('中：Poet');
  const sent = net.translate;
  await page.reload();
  await expect(cards(page).filter({ hasText: 'Poet' }).locator('h3')).toHaveText('中：Poet');
  expect(net.translate).toBe(sent);
  await page.locator('#q').fill('中：Poet');          // 翻好的標題也搜得到
  await expect(cards(page)).toHaveCount(1);
});
