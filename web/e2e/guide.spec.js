// App 開發全流程（web/dev-guide.html）：路線圖、教學、起手式精靈、範例、比較、注意、技術、參考
const fs = require('fs');
const path = require('path');
const { test, expect, expectNoHorizontalScroll, runWizard } = require('./fixtures');

const FOLLOW = /執行方案[\s\S]*直接照方案做/;
const TABS = ['map', 'learn', 'start', 'examples', 'compare', 'caution', 'tech', 'refs'];
const clip = (page) => page.evaluate(() => navigator.clipboard.readText());
const tab = (page, id) => page.locator(`nav.tabs button[data-tab="${id}"]`).click();
const APPS = JSON.parse(fs.readFileSync(path.join(__dirname, '../../.github/apps.json'), 'utf8')).apps;

async function openStart(page) {
  await page.goto('/dev-guide.html');
  await tab(page, 'start');
  await expect(page.locator('#tab-start')).toBeVisible();
}

test('技能庫網頁有連到這一頁', async ({ page }) => {
  await page.goto('/index.html');
  await page.locator('a[href="dev-guide.html"]').click();
  await expect(page).toHaveURL(/dev-guide\.html$/);
  await expect(page.locator('h1')).toHaveText('App 開發全流程');
});

test('八個分頁都能切換，手機寬度不會左右滑（寬表格只在框內滑）', async ({ page }) => {
  await page.goto('/dev-guide.html');
  for (const id of TABS) {
    await tab(page, id);
    await expect(page.locator('#tab-' + id)).toBeVisible();
    await expectNoHorizontalScroll(page);
  }
  // 範例和參考是程式畫出來的：也要真的有內容
  await tab(page, 'examples');
  expect(await page.locator('#tplGallery .tcard').count()).toBeGreaterThanOrEqual(25);
  await tab(page, 'refs');
  expect(await page.locator('#refList .refcard').count()).toBeGreaterThanOrEqual(15);
});

test('外部連結都是 https、開新分頁、帶 noopener（含程式畫出來的範例與參考）', async ({ page }) => {
  await page.goto('/dev-guide.html');
  const links = await page.locator('a[href^="http"]').evaluateAll((as) => as.map((a) => ({ h: a.href, t: a.target, r: a.rel })));
  expect(links.length).toBeGreaterThan(40);
  for (const l of links) {
    expect(l.h, l.h).toMatch(/^https:\/\//);
    expect(l.t, l.h).toBe('_blank');
    expect(l.r, l.h).toContain('noopener');
  }
});

test('費用表每一列都有出處，或明寫「未查證」', async ({ page }) => {
  await page.goto('/dev-guide.html');
  await tab(page, 'compare');
  const card = page.locator('#tab-compare .card').filter({ hasText: '要花多少錢' });
  const rows = card.locator('table tr').filter({ has: page.locator('td') });
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

test('「功能的眉角」把起手式選項用到的規定都附了出處', async ({ page }) => {
  await page.goto('/dev-guide.html');
  await tab(page, 'caution');
  const card = page.locator('#tab-caution .card').filter({ hasText: '功能的眉角' });
  for (const word of ['LINE Notify', 'Telegram', '捷徑', 'OpenStreetMap', '開放資料', '44×44']) {
    const li = card.locator('li').filter({ hasText: word });
    await expect(li, word).toHaveCount(1);
    expect(await li.locator('a.src').count(), word + ' 要有出處').toBeGreaterThan(0);
  }
});

test.describe('路線圖與進度清單', () => {
  test('「下一步」打開那一步；按「做完了」路線圖變色、清單打勾，重新整理後還記得', async ({ page }) => {
    await page.goto('/dev-guide.html');
    const prog = page.locator('#tab-map [data-progress]');
    // 可略過的步驟（雛形、上架）沒做不算落後
    await expect(prog.locator('b')).toHaveText('進度 0／9');
    await prog.locator('[data-next]').click();
    await expect(page.locator('#tab-learn')).toBeVisible();
    const first = page.locator('#st-idea');
    await expect(first).toHaveAttribute('open', '');
    await first.locator('.donebtn').click();
    await expect(first.locator('.donebtn')).toContainText('已完成');
    await expect(page.locator('#tab-learn [data-progress] b')).toHaveText('進度 1／9');

    await page.reload();
    await tab(page, 'map');
    await expect(page.locator('[data-node="st-idea"]')).toHaveClass(/done/);
    await expect(page.locator('[data-node="st-spec"]')).toHaveClass(/next/);
    await expect(page.locator('#chk-st-idea')).toBeChecked();
    // 清單也能直接打勾
    await page.locator('#tab-map label[for="chk-st-spec"]').click();
    await expect(prog.locator('b')).toHaveText('進度 2／9');
    // 可略過的步驟做了才算進度
    await page.locator('[data-node="st-proto"]').click();
    await page.locator('#st-proto .donebtn').click();
    await tab(page, 'map');
    await expect(prog.locator('b')).toHaveText('進度 3／10');

    await page.locator('[data-reset-progress]').click();
    await expect(prog.locator('b')).toHaveText('進度 0／9');
    await expect(page.locator('[data-node="st-idea"]')).not.toHaveClass(/done/);
  });

  test('路線圖的節點與「我卡住了」會切到教學分頁並捲到那裡', async ({ page }) => {
    await page.goto('/dev-guide.html');
    await page.locator('[data-node="st-pages"]').click();
    await expect(page.locator('#tab-learn')).toBeVisible();
    await expect(page.locator('#st-pages')).toHaveAttribute('open', '');
    await tab(page, 'map');
    await page.locator('#tab-map a[href="#faq"]').click();
    await expect(page.locator('#faq')).toBeInViewport();
  });

  test('跳過去的步驟和精靈的題目不會被上面固定的分頁列蓋住', async ({ page }) => {
    await page.goto('/dev-guide.html');
    const below = (sel) => page.evaluate((s) => document.querySelector(s).getBoundingClientRect().top -
      document.querySelector('nav.tabs').getBoundingClientRect().bottom, sel);
    await page.locator('[data-node="st-feature"]').click();
    await expect.poll(() => below('#st-feature')).toBeGreaterThanOrEqual(0);
    await tab(page, 'start');
    await page.locator('#wizard [data-wiz="begin"]').click();
    await page.locator('#wizard [data-wiz="next"]').click();
    await expect.poll(() => below('#wizard')).toBeGreaterThanOrEqual(0);
  });

  test('教學裡「要貼的」都有複製按鈕，複製的就是框裡的字', async ({ page }) => {
    await page.goto('/dev-guide.html');
    await tab(page, 'learn');
    const boxes = page.locator('#tab-learn .copybox');
    expect(await boxes.count()).toBeGreaterThan(3);
    const box = boxes.first();
    await box.locator('xpath=ancestor::details[1]').evaluate((d) => { d.open = true; });
    await box.locator('button').click();
    expect(await clip(page)).toBe(await box.locator('pre').textContent());
  });
});

test.describe('起手式精靈', () => {
  test('一題一題回答：A／B 兩段跟著答案變，B 段裝完要列方案並接著做', async ({ page }) => {
    await openStart(page);
    await expect(page.locator('#wizard [data-wiz="begin"]')).toContainText('12 題');
    await runWizard(page, { gDesc: '每天整理我關注的 10 檔股票', plats: ['pIos'], data: ['nStock'], ai: 'ai' });

    const chat = await page.locator('#outChat').inputValue();
    expect(chat).toContain('每天整理我關注的 10 檔股票');
    expect(chat).toContain('4.2');
    expect(chat).toContain('金鑰不能放在網頁裡');
    expect(chat).toContain('投顧法規');
    expect(chat).toContain('幫我取 3 個英文的 repo 名稱');

    const code = await page.locator('#outCode').inputValue();
    expect(code).toContain('core,pwa,actions,stock,ai');
    expect(code).toMatch(FOLLOW);
    await expect(page.locator('#wizPacks')).toHaveText('core, pwa, actions, stock, ai');
    // 預估費用：勾了 iOS 上架與 AI 就列出那兩項
    await expect(page.locator('#wizard .rec')).toContainText('US$99／年');
    await expect(page.locator('#wizard .rec')).toContainText('AI API');
    // 全部免費又要 AI、又要上 App Store：兩個衝突都要講
    await expect(page.locator('#wizard .warns li')).toHaveCount(2);
    // 「接下來」連到教學的步驟
    await page.locator('#wizard ol a[href="#st-repo"]').click();
    await expect(page.locator('#tab-learn')).toBeVisible();
    await expect(page.locator('#st-repo')).toHaveAttribute('open', '');
  });

  test('改版、LINE、抓網頁、Agent、推播、同步：帶出對應的規定、做法與技能組合，並自動選好 repo', async ({ page }) => {
    await openStart(page);
    await runWizard(page, {
      gMode: 'existing', gRepo: 'https://github.com/xin7355-collab/PriceHub', gType: 'shop', plats: ['pLine'],
      data: ['nScrape'], ai: 'agent', nNotify: 'push', dStore: 'sync',
    });
    const chat = await page.locator('#outChat').inputValue();
    expect(chat).toContain('LINE Notify 已在 2025/3/31 停止');
    expect(chat).toContain('iOS 16.4');
    expect(chat).toContain('robots.txt');
    expect(chat).toContain('noindex');
    expect(chat).not.toContain('英文的 repo 名稱'); // 改版不用取名

    const code = await page.locator('#outCode').inputValue();
    expect(code).toContain('這是已經在做的 App repo（xin7355-collab/PriceHub）');
    expect(code).toContain('不要整個重寫');
    expect(code).toContain('Messaging API');
    expect(code).toContain('web-to-markdown');
    expect(code).toMatch(FOLLOW);
    // 固定順序：core, pwa, actions, backend, stock, ai, agent, security, python, video, visual, workflow
    await expect(page.locator('#wizPacks')).toHaveText('core, pwa, actions, backend, ai, agent, security, python');

    const href = await page.locator('#wizard a[data-open="outCode"]').getAttribute('href');
    const u = new URL(href);
    expect(u.origin + u.pathname).toBe('https://claude.ai/code');
    expect(u.searchParams.get('repositories')).toBe('xin7355-collab/PriceHub');
    expect(u.searchParams.get('prompt')).toBe(code);
  });

  test('選了「已經有的 App」卻沒填 repo 會提醒；指令太長時改成只開 Claude Code 並請你先複製', async ({ page }) => {
    await openStart(page);
    await runWizard(page, { gMode: 'existing', gDesc: '很長的需求'.repeat(900) });
    await expect(page.locator('#wizard .warns')).toContainText('填 repo');
    const open = page.locator('#wizard a[data-open="outCode"]');
    await expect(open).toHaveAttribute('href', 'https://claude.ai/code');
    await expect(page.locator('#wizard')).toContainText('指令太長');
  });

  test('從結果頁按「改」只改那一題，改好直接回到結果', async ({ page }) => {
    await openStart(page);
    await runWizard(page, { gDesc: '家庭記帳' });
    await page.locator('#wizard [data-edit="q-feat"]').click();
    await page.locator('#w-feats-nLogin').check();
    await expect(page.locator('#wizard [data-wiz="next"]')).toHaveText('改好了，看結果');
    await page.locator('#wizard [data-wiz="next"]').click();
    await expect(page.locator('#wizPacks')).toContainText('security');
    await expect(page.locator('#wizard .sum').filter({ has: page.locator('[data-edit="q-feat"]') })).toContainText('登入');
  });

  test('複製的內容跟畫面一致；重新整理後還在結果頁、分頁也記得', async ({ page }) => {
    await openStart(page);
    await runWizard(page, { gDesc: '家庭記帳', feats: ['nLogin'] });
    await page.locator('button[data-copy="outChat"]').click();
    expect(await clip(page)).toBe(await page.locator('#outChat').inputValue());
    await page.locator('button[data-copy="outCode"]').click();
    expect(await clip(page)).toBe(await page.locator('#outCode').inputValue());
    expect(await clip(page)).toContain('security');

    await page.reload();
    await expect(page.locator('#tab-start')).toBeVisible();
    await expect(page.locator('#outChat')).toHaveValue(/家庭記帳/);
  });

  test('全部重來回到預設；舊版存檔（攤平的勾選框）照樣讀得進來', async ({ page }) => {
    await page.goto('/dev-guide.html');
    await page.evaluate(() => localStorage.setItem('guide-gen', JSON.stringify({ gDesc: '舊的 App', gWho: '自己', nAi: true, nVideo: true, gType: 'removed-type' })));
    await page.reload();
    await tab(page, 'start');
    await expect(page.locator('#outChat')).toHaveValue(/舊的 App/);
    await expect(page.locator('#wizPacks')).toHaveText('core, pwa, ai, video');
    await expect(page.locator('#wizard .sum').filter({ hasText: '一般工具' })).toHaveCount(1); // 不存在的選項 → 回預設
    await expect(page.locator('#outCode')).toHaveValue(/Playwright E2E/); // 舊存檔沒有 qTest → 預設勾選

    await page.locator('#wizard [data-wiz="restart"]').click();
    await expect(page.locator('#wizard [data-wiz="begin"]')).toBeVisible();
    await expect(page.locator('#wizard [data-wiz="resume"]')).toHaveCount(0);
    await runWizard(page, {});
    await expect(page.locator('#wizPacks')).toHaveText('core, pwa');
    expect(await page.locator('#outChat').inputValue()).toContain('（請在這裡寫：這個 App 要做什麼）');
  });
});

test.describe('範例', () => {
  test('套用範例會跳到起手式結果頁；分類按鈕只留那一類', async ({ page }) => {
    await page.goto('/dev-guide.html');
    await tab(page, 'examples');
    await page.locator('#tplFilters [data-cat="遊戲"]').click();
    const cards = page.locator('#tplGallery .tcard');
    expect(await cards.count()).toBeGreaterThan(1);
    for (const t of await cards.locator('.meta').allInnerTexts()) expect(t).toContain('遊戲');
    await page.locator('#tplFilters [data-cat="全部"]').click();
    await page.locator('#tplGallery [data-apply="stock-watch"]').click();
    await expect(page.locator('#tab-start')).toBeVisible();
    await expect(page.locator('#wizPacks')).toContainText('stock');
    // 參考專案用 Repo X-Ray 打開
    await tab(page, 'examples');
    const ref = page.locator('#tplGallery [data-tpl="stock-watch"] .refs a').first();
    await expect(ref).toHaveAttribute('href', /^xray\.html\?repo=[\w.-]+%2F[\w.-]+$/);
  });

  test('三個完整案例都在，每個都有一步一步的做法', async ({ page }) => {
    await page.goto('/dev-guide.html');
    await tab(page, 'examples');
    for (const id of ['case-stock', 'case-budget', 'case-price']) {
      const c = page.locator('#' + id);
      await c.locator('summary').click();
      expect(await c.locator('.cstep').count(), id).toBeGreaterThanOrEqual(4);
    }
  });

  test('我的 App 清單來自 apps.json；按「加功能」帶入那個 repo', async ({ page }) => {
    await page.goto('/dev-guide.html');
    await tab(page, 'examples');
    const rows = page.locator('#myApps [data-app]');
    await expect(rows).toHaveCount(APPS.length);
    const first = APPS[0].repo;
    await rows.first().getByRole('button', { name: '加功能' }).click();
    await expect(page.locator('#tab-start')).toBeVisible();
    await expect(page.locator('#outCode')).toHaveValue(new RegExp(`這是已經在做的 App repo（${first}）`));
    const href = await page.locator('#wizard a[data-open="outCode"]').getAttribute('href');
    expect(new URL(href).searchParams.get('repositories')).toBe(first);
  });

  test.describe('apps.json 讀不到時', () => {
    test.use({ appsJson: 'fail' });
    test('顯示說明，教你改在起手式貼 repo', async ({ page }) => {
      await page.goto('/dev-guide.html');
      await tab(page, 'examples');
      await expect(page.locator('#myApps .note')).toContainText('已經有的 App');
    });
  });
});

test('參考資源可以依種類篩選，GitHub 專案用 Repo X-Ray 打開', async ({ page }) => {
  await page.goto('/dev-guide.html');
  await tab(page, 'refs');
  const all = await page.locator('#refList .refcard').count();
  await page.locator('#refFilters [data-kind="台灣在地"]').click();
  const some = await page.locator('#refList .refcard').count();
  expect(some).toBeGreaterThan(0);
  expect(some).toBeLessThan(all);
  await page.locator('#refFilters [data-kind="全部"]').click();
  await expect(page.locator('#refList a[href^="xray.html?repo="]').first()).toBeVisible();
  await expect(page.locator('#refList .refcard h3').filter({ hasText: '⭐' }).first()).toBeVisible();
});

test('「裝完接著做」規則跟技能庫網頁一字不差（兩邊要一致）', async ({ page }) => {
  const tail = (s) => s.slice(s.indexOf('裝好後不要停'));
  await page.goto('/index.html');
  const fromIndex = tail(await page.locator('#autoPrompt').inputValue());
  await openStart(page);
  await runWizard(page, {});
  const fromGuide = tail(await page.locator('#outCode').inputValue());
  expect(fromIndex.length).toBeGreaterThan(100);
  expect(fromGuide).toBe(fromIndex);
});
