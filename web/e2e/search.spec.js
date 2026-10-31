// 超強搜尋（web/search.html）：一次搜技能、提示詞、App 點子、教學、AI 功能（2026-10-31 使用者：「如股票就幫我搜尋相關提示詞」）
// 用的是 repo 裡的真資料（catalog.json 由 serve.py 產生、prompts/index.json 每月同步），所以只斷言不會隨資料變動的東西：
// skl 自己的技能（tw-stock）、自己寫的點子與教學（stock-close、case-stock）。
const { test, expect, expectNoHorizontalScroll } = require('./fixtures');

const grp = (page, k) => page.locator(`section.grp[data-g="${k}"]`);

test.describe('超強搜尋', () => {
  test('首頁搜尋列：好按、送出就到超強搜尋；「股票」一次找到提示詞、技能、點子、教學，還會帶英文同義詞', async ({ page }) => {
    await page.goto('/index.html');
    const box = page.locator('header form.gsearch input[name="q"]');
    expect(await box.evaluate((e) => parseFloat(getComputedStyle(e).fontSize))).toBeGreaterThanOrEqual(16);   // iPhone 小於 16px 會放大整頁
    expect((await page.locator('header form.gsearch button').boundingBox()).height).toBeGreaterThanOrEqual(46);
    await box.fill('股票');
    await box.press('Enter');
    await expect(page).toHaveURL(/search\.html\?q=%E8%82%A1%E7%A5%A8/);
    await expect(page.locator('#exp')).toContainText('stock');                 // 同義詞：中文查英文資料
    await expect(page.locator('#exp')).toContainText('台股');
    for (const k of ['prompts', 'skills', 'ideas', 'guides']) await expect(grp(page, k).locator('a.r').first()).toBeVisible();
    await expect(grp(page, 'prompts').locator('a.r').first()).toHaveAttribute('href', /^prompts\.html\?id=/);
    await expect(grp(page, 'skills').locator('a.r', { hasText: 'tw-stock' })).toHaveAttribute('href', 'index.html?q=tw-stock');
    await expect(grp(page, 'ideas').locator('a.r[href="ideas.html#stock-close"]')).toBeVisible();
    await expect(grp(page, 'prompts').locator('mark').first()).toBeVisible();   // 標出對到的字
    // 每組先顯示 5 筆，可以再看更多
    expect(await grp(page, 'prompts').locator('a.r').count()).toBeLessThanOrEqual(5);
    // GitHub：交給 X-Ray 的模糊搜尋
    await expect(page.locator('a.ghcard')).toHaveAttribute('href', 'xray.html?q=%E8%82%A1%E7%A5%A8&fz=1');
    await expectNoHorizontalScroll(page);
  });

  test('「股票 提示詞」只看提示詞；按「全部」看其他類，不會被拉回去', async ({ page }) => {
    await page.goto('/search.html');
    await page.locator('#q').fill('股票 提示詞');
    await page.locator('#q').press('Enter');
    await expect(page.locator('section.grp')).toHaveCount(1);
    await expect(grp(page, 'prompts')).toBeVisible();
    await expect(page.locator('#exp')).not.toContainText('提示詞');             // 「提示詞」只是指定類別，不拿去比對
    await expect(page.locator('#gtabs [data-view="prompts"]')).toHaveAttribute('aria-pressed', 'true');
    const n = await grp(page, 'prompts').locator('a.r').count();
    expect(n).toBeGreaterThan(5);                                               // 只看一類時一次 20 筆
    await page.locator('#gtabs [data-view=""]').click();
    await expect(grp(page, 'skills')).toBeVisible();
    await expect(page.locator('#gtabs [data-view=""]')).toHaveAttribute('aria-pressed', 'true');
    await page.locator('#gtabs [data-view="skills"]').click();
    await expect(page.locator('section.grp')).toHaveCount(1);
    const more = grp(page, 'skills').locator('[data-more]');
    if (await more.count()) {
      const before = await grp(page, 'skills').locator('a.r').count();
      await more.click();
      expect(await grp(page, 'skills').locator('a.r').count()).toBeGreaterThan(before);
    }
  });

  test('英文打錯字也找得到；「-字」排除；中文沒空格也會拆詞', async ({ page }) => {
    await page.goto('/search.html?q=stokc');
    await expect(page.locator('#exp')).toContainText('應該是 stock');
    await expect(grp(page, 'skills').locator('a.r', { hasText: 'tw-stock' })).toBeVisible();
    await page.locator('#q').fill('記帳 -excel');
    await page.locator('#q').press('Enter');
    await expect(page.locator('#exp')).toContainText('排除：excel');
    await expect(grp(page, 'ideas').locator('a.r').first()).toBeVisible();
    const leaked = await page.evaluate(() => Object.values(S.last.R).flatMap((g) => g.rows).filter((r) => r.it.all.includes('excel')).length);
    expect(leaked).toBe(0);
    await page.locator('#q').fill('影片剪輯');
    await page.locator('#q').press('Enter');
    await expect(page.locator('#exp')).toContainText('影片');
    await expect(page.locator('#exp')).toContainText('剪輯');
    await expect(grp(page, 'skills').locator('a.r', { hasText: 'auto-video-edit' })).toBeVisible();
  });

  test('點結果直接到那一項：技能（瀏覽技能分頁）、點子（打開）、教學（切分頁打開）、範例卡、AI 功能（標出來）', async ({ page }) => {
    // 技能：舊的類別篩選不能把它藏起來
    await page.goto('/index.html');
    await page.evaluate(() => localStorage.setItem('cat', JSON.stringify('marketing')));
    await page.goto('/search.html?q=tw-stock');
    await grp(page, 'skills').locator('a.r', { hasText: 'tw-stock' }).first().click();
    await expect(page).toHaveURL(/index\.html\?q=tw-stock$/);
    await expect(page.locator('#tab-browse')).toBeVisible();
    await expect(page.locator('#q')).toHaveValue('tw-stock');
    await expect(page.locator('#skillList .skill .nm', { hasText: /^tw-stock$/ })).toBeVisible();
    // 點子：篩選停在別的類別也要打開
    await page.goto('/ideas.html');
    await page.evaluate(() => localStorage.setItem('ideas-ui-v1', JSON.stringify({ cat: '工具', level: '大' })));
    await page.goto('/search.html?q=股票');
    await grp(page, 'ideas').locator('a.r[href="ideas.html#stock-close"]').click();
    await expect(page.locator('details.idea[data-id="stock-close"]')).toHaveAttribute('open', '');
    await expect(page.locator('details.idea[data-id="stock-close"] summary')).toBeInViewport();
    // 教學的案例：在別的分頁也要切過去打開
    await page.goto('/dev-guide.html#case-stock');
    await expect(page.locator('#case-stock')).toHaveAttribute('open', '');
    await expect(page.locator('#case-stock')).toBeVisible();
    // 範例庫的卡片（guide.js 畫出來的）
    await page.goto('/dev-guide.html#tpl-stock-watch');
    await expect(page.locator('#tpl-stock-watch')).toBeVisible();
    await expect(page.locator('#tpl-stock-watch')).toBeInViewport();
    // 分頁
    await page.goto('/agent.html#tab-caution');
    await expect(page.locator('#tab-caution')).toBeVisible();
    // AI 功能
    await page.goto('/search.html?q=CLAUDE.md');
    const ai = grp(page, 'ai').locator('a.r', { hasText: 'CLAUDE.md' }).first();
    await expect(ai).toHaveAttribute('href', /^ai\.html\?tab=claude&f=/);
    await ai.click();
    await expect(page.locator('#tab-claude')).toBeVisible();
    await expect(page.locator('#claudeList .feat.hit .nm')).toHaveText('CLAUDE.md');
    await expect(page.locator('#claudeList .feat.hit')).toBeInViewport();
  });

  test('搜尋結果的連結全部指到站內存在的頁面', async ({ page }) => {
    await page.goto('/search.html?q=股票');
    await expect(grp(page, 'guides').locator('a.r').first()).toBeVisible();
    const hrefs = await page.locator('a.r, a.ghcard').evaluateAll((as) => as.map((a) => a.getAttribute('href')));
    for (const h of hrefs) expect(h).toMatch(/^(prompts|index|ideas|dev-guide|agent|ai|xray)\.html[?#]/);
    // 教學連結的 #id 要真的存在（不然跳不過去）
    for (const h of hrefs.filter((x) => /^(dev-guide|agent)\.html#/.test(x))) {
      const [file, id] = h.split('#');
      if (id.startsWith('tpl-') || id.startsWith('tab-')) continue;
      const html = await (await page.request.get('/' + file)).text();
      expect(html, h).toContain(`id="${decodeURIComponent(id)}"`);
    }
  });

  test('最近搜過記在手機、可以清除；空白時列出可以搜什麼', async ({ page }) => {
    await page.goto('/search.html');
    await expect(page.locator('#what')).toContainText('提示詞');
    await expect(page.locator('#what')).toContainText('技能');
    await page.locator('#examples [data-q="爬蟲"]').click();
    await expect(grp(page, 'skills').locator('a.r').first()).toBeVisible();
    await page.locator('#q').fill('');
    await page.locator('#q').dispatchEvent('input');
    await expect(page.locator('#recent [data-q="爬蟲"]')).toBeVisible();
    await page.reload();
    await expect(page.locator('#recent [data-q="爬蟲"]')).toBeVisible();
    await page.locator('#clearRecent').click();
    await expect(page.locator('#recentBox')).toBeHidden();
  });

  test('一份資料載入失敗要說出來，其他類照常搜', async ({ page }) => {
    await page.route('**/prompts/index.json*', (r) => r.fulfill({ status: 404, body: '' }));
    await page.goto('/search.html?q=股票');
    await expect(grp(page, 'prompts')).toContainText('載入失敗');
    await expect(grp(page, 'prompts')).toContainText('不代表沒有');
    await expect(grp(page, 'skills').locator('a.r').first()).toBeVisible();
    await expect(page.locator('#loadErr')).toBeHidden();
  });

  test('X-Ray 收到 fz=1：這次用模糊搜尋，但不改使用者存的設定', async ({ page }) => {
    await page.goto('/xray.html?q=%E8%82%A1%E7%A5%A8&fz=1');
    await expect(page.locator('#fuzzy')).toBeChecked();
    expect(await page.evaluate(() => localStorage.getItem('xray-fuzzy'))).toBeNull();
  });
});
