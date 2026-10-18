// AI Agent 製作流程（web/agent.html）：路線圖（四種做法分流）、教學、起手式精靈、範例、比較、技術、注意、參考
const fs = require('fs');
const path = require('path');
const { test, expect, expectNoHorizontalScroll, runWizard } = require('./fixtures');

const FOLLOW = /執行方案[\s\S]*直接照方案做/;
const TABS = ['map', 'learn', 'start', 'examples', 'compare', 'tech', 'caution', 'refs'];
const clip = (page) => page.evaluate(() => navigator.clipboard.readText());
const tab = (page, id) => page.locator(`nav.tabs button[data-tab="${id}"]`).click();
const rec = (page) => page.locator('#wizard .rec');
const APPS = JSON.parse(fs.readFileSync(path.join(__dirname, '../../.github/apps.json'), 'utf8')).apps;

async function openStart(page) {
  await page.goto('/agent.html');
  await tab(page, 'start');
  await expect(page.locator('#tab-start')).toBeVisible();
}
async function applyTemplate(page, id) {
  await tab(page, 'examples');
  await page.locator(`#tplGallery [data-apply="${id}"]`).click();
  await expect(page.locator('#tab-start')).toBeVisible();
  await expect(page.locator('#wizard h2')).toHaveText('結果：照下面做');
}

test('技能庫網頁與 App 開發全流程都連得到這一頁', async ({ page }) => {
  await page.goto('/index.html');
  await page.locator('a[href="agent.html"]').click();
  await expect(page).toHaveURL(/agent\.html$/);
  await expect(page.locator('h1')).toHaveText('AI Agent 製作流程');
  await page.goto('/dev-guide.html');
  await expect(page.locator('header a[href="agent.html"]')).toBeVisible();
});

test('八個分頁都能切換，手機寬度不會左右滑', async ({ page }) => {
  await page.goto('/agent.html');
  for (const id of TABS) {
    await tab(page, id);
    await expect(page.locator('#tab-' + id)).toBeVisible();
    await expectNoHorizontalScroll(page);
  }
  await tab(page, 'examples');
  expect(await page.locator('#tplGallery .tcard').count()).toBeGreaterThanOrEqual(25);
  await tab(page, 'refs');
  expect(await page.locator('#refList .refcard').count()).toBeGreaterThanOrEqual(15);
});

test('外部連結都是 https、開新分頁、帶 noopener', async ({ page }) => {
  await page.goto('/agent.html');
  const links = await page.locator('a[href^="http"]').evaluateAll((as) => as.map((a) => ({ h: a.href, t: a.target, r: a.rel })));
  expect(links.length).toBeGreaterThan(40);
  for (const l of links) {
    expect(l.h, l.h).toMatch(/^https:\/\//);
    expect(l.t, l.h).toBe('_blank');
    expect(l.r, l.h).toContain('noopener');
  }
});

test('費用表每一列都有出處，或明寫「未查證」；出處清單不重複、只靠搜尋的標 ※', async ({ page }) => {
  await page.goto('/agent.html');
  await tab(page, 'compare');
  for (const title of ['四種做法比較', '要花多少錢', '其他家的做法']) {
    const rows = page.locator('#tab-compare .card').filter({ hasText: title }).locator('table tr').filter({ has: page.locator('td') });
    const n = await rows.count();
    expect(n, title).toBeGreaterThan(3);
    for (let i = 0; i < n; i++) {
      const row = rows.nth(i);
      const hasSrc = await row.locator('a.src').count();
      const text = await row.innerText();
      expect(hasSrc > 0 || text.includes('未查證') || text.includes('AI 指南'), text.slice(0, 40)).toBe(true);
    }
  }
  const unique = await page.locator('main a.src').evaluateAll((as) => new Set(as.map((a) => a.href)).size);
  await expect(page.locator('#srcs li')).toHaveCount(unique);
  await expect(page.locator('#srcs li').filter({ hasText: '※' }).first()).toBeVisible();
});

test('「技能、MCP、CLAUDE.md 差在哪」每一列都附出處，研究結果標 ※', async ({ page }) => {
  await page.goto('/agent.html');
  await tab(page, 'tech');
  const card = page.locator('#tech-layers');
  await expect(card).toBeVisible();
  const rows = card.locator('table tr').filter({ has: page.locator('td') });
  await expect(rows).toHaveCount(5);
  for (let i = 0; i < 5; i++) expect(await rows.nth(i).locator('a.src').count(), await rows.nth(i).innerText()).toBeGreaterThan(0);
  for (const name of ['arXiv※', 'Vercel※']) await expect(card.locator('a.src', { hasText: name })).toHaveCount(1);
  await expect(page.locator('#srcs li').filter({ hasText: 'arxiv.org' })).toContainText('※');
  await expectNoHorizontalScroll(page);
});

test.describe('選做法與進度', () => {
  test('教學只顯示選的做法的步驟，進度也只算那些；預設是 Routine', async ({ page }) => {
    await page.goto('/agent.html');
    await tab(page, 'learn');
    await expect(page.locator('#ways [data-way="routine"]')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#st-rt-prep')).toBeVisible();
    await expect(page.locator('#st-cw-task')).toBeHidden();
    await expect(page.locator('#tab-learn [data-progress] b')).toHaveText('進度 0／9');

    await page.locator('#ways [data-way="cowork"]').click();
    await expect(page.locator('#st-cw-task')).toBeVisible();
    await expect(page.locator('#st-rt-prep')).toBeHidden();
    await expect(page.locator('#st-goal')).toBeVisible(); // 共同步驟一直都在
    await page.locator('#st-cw-task > summary').click();
    await page.locator('#st-cw-task .donebtn').click();
    await expect(page.locator('#tab-learn [data-progress] b')).toHaveText('進度 1／9');

    // 進度清單只列這個做法的步驟；換做法後，別的做法打的勾不算
    await tab(page, 'map');
    await expect(page.locator('#tab-map [data-checklist] input')).toHaveCount(9);
    await expect(page.locator('#chk-st-cw-task')).toBeChecked();
    await expect(page.locator('#chk-st-rt-prep')).toHaveCount(0);
    await page.locator('#tab-map [data-setway="api"]').click();
    await expect(page.locator('#tab-learn')).toBeVisible();
    await expect(page.locator('#st-api-design')).toHaveAttribute('open', '');
    await expect(page.locator('#tab-learn [data-progress] b')).toHaveText('進度 0／9');

    // 重新整理後還記得選的做法
    await page.reload();
    await tab(page, 'learn');
    await expect(page.locator('#ways [data-way="api"]')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#st-api-design')).toBeVisible();
  });

  test('從路線圖點別的做法的步驟，會先切到那個做法再打開', async ({ page }) => {
    await page.goto('/agent.html');
    await page.locator('[data-node="st-ga-app"]').click();
    await expect(page.locator('#tab-learn')).toBeVisible();
    await expect(page.locator('#st-ga-app')).toBeVisible();
    await expect(page.locator('#st-ga-app')).toHaveAttribute('open', '');
    await expect(page.locator('#ways [data-way="action"]')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#st-rt-prep')).toBeHidden();
  });

  test('「下一步」帶你到還沒做的那一步；「我卡住了」切到常見卡關', async ({ page }) => {
    await page.goto('/agent.html');
    await page.locator('#tab-map [data-progress] [data-next]').click();
    await expect(page.locator('#st-goal')).toHaveAttribute('open', '');
    await page.locator('#st-goal .donebtn').click();
    await tab(page, 'map');
    await expect(page.locator('[data-node="st-goal"]')).toHaveClass(/done/);
    await expect(page.locator('[data-node="st-manual"]')).toHaveClass(/next/);
    await page.locator('#tab-map a[href="#faq"]').click();
    await expect(page.locator('#faq')).toBeInViewport();
    await tab(page, 'map');
    await page.locator('[data-reset-progress]').click();
    await expect(page.locator('#tab-map [data-progress] b')).toHaveText('進度 0／9');
  });
});

test.describe('Agent 起手式', () => {
  test('8 題答完；預設（只有自己看結果）建議 Cowork：B 段是交辦任務，不裝技能、不給 Claude Code 連結', async ({ page }) => {
    await openStart(page);
    await expect(page.locator('#wizard [data-wiz="begin"]')).toContainText('8 題');
    await runWizard(page, { aTask: '整理這週的新聞重點' });
    await expect(rec(page)).toHaveAttribute('data-way', 'cowork');
    await expect(page.locator('#wizPacks')).toHaveCount(0);
    await expect(page.locator('#wizard a[data-open]')).toHaveCount(0);
    const b = await page.locator('#aOutB').inputValue();
    expect(b).toContain('【任務】整理這週的新聞重點');
    expect(b).not.toMatch(FOLLOW);
    expect(b).not.toContain('app-bootstrap');
    expect(await page.locator('#aOutA').inputValue()).toContain('我初步選「Cowork」');
  });

  test('四個範例對應四種做法，指令內容跟著換', async ({ page }) => {
    await page.goto('/agent.html');
    const cases = [
      ['stock-close', 'routine', ['agent/README.md', 'tw-stock', 'Routine', 'core,pwa,stock,ai,agent,python']],
      ['morning-brief', 'cowork', ['【排程】', '只讀不寫']],
      ['fix-bug', 'action', ['--max-turns', 'timeout-minutes', 'ANTHROPIC_API_KEY', 'allowed_non_write_users', '每月 US$5']],
      ['stock-helper', 'api', ['ai-model-router', '花費上限', '不放網址', 'US$20']],
    ];
    for (const [id, way, words] of cases) {
      await applyTemplate(page, id);
      await expect(rec(page), id).toHaveAttribute('data-way', way);
      const b = await page.locator('#aOutB').inputValue();
      for (const w of words) expect(b, `${id} 要有 ${w}`).toContain(w);
      if (way === 'cowork') expect(b).not.toMatch(FOLLOW);
      else expect(b, id).toMatch(FOLLOW);
    }
  });

  test('衝突與危險組合會跳警告', async ({ page }) => {
    await page.goto('/agent.html');
    // GitHub Actions 要 API 金鑰，卻選「只用訂閱」
    await applyTemplate(page, 'fix-bug');
    await page.locator('#wizard [data-edit="q-budget"]').click();
    await page.locator('#w-aBudget-sub').check();
    await page.locator('#wizard [data-wiz="next"]').click();
    await expect(page.locator('#wizard .warns')).toContainText('衝突');
    expect(await page.locator('#aOutB').inputValue()).toContain('我還沒決定預算，先問我');

    // 讀外部網頁＋能對外發送＋全部自動：提示注入
    await page.locator('#wizard [data-wiz="restart"]').click();
    await runWizard(page, { aTask: '回覆粉專留言', tools: ['tWeb', 'tWrite'], aAsk: 'never' });
    const warns = page.locator('#wizard .warns');
    await expect(warns).toContainText('提示注入');
    await expect(warns).toContainText('沒有人攔得住');
  });

  test('要改 repo 卻沒填是哪個 repo 會提醒；填了之後「在 Claude Code 打開」帶入 repo 與指令', async ({ page }) => {
    await openStart(page);
    await runWizard(page, { aTask: '每天更新網站資料', tools: ['tRepo'], aOut: 'app' });
    await expect(rec(page)).toHaveAttribute('data-way', 'routine');
    await expect(page.locator('#wizard .warns')).toContainText('還沒填要加在哪個 repo');

    await page.locator('#wizard [data-edit="q-more"]').click();
    await page.locator('#w-aRepo').fill('https://github.com/xin7355-collab/PriceHub.git');
    await page.locator('#wizard [data-wiz="next"]').click();
    await expect(page.locator('#wizard .warns')).toHaveCount(0);
    const href = await page.locator('#wizard a[data-open="aOutB"]').getAttribute('href');
    const u = new URL(href);
    expect(u.origin + u.pathname).toBe('https://claude.ai/code');
    expect(u.searchParams.get('repositories')).toBe('xin7355-collab/PriceHub');
    expect(u.searchParams.get('prompt')).toBe(await page.locator('#aOutB').inputValue());
  });

  test('定時任務才問時間；結果頁的「接下來」會切到建議的做法', async ({ page }) => {
    await openStart(page);
    await page.locator('#wizard [data-wiz="begin"]').click();
    await page.locator('#wizard [data-wiz="next"]').click();
    await page.locator('#wizard [data-wiz="next"]').click();
    await expect(page.locator('#w-aTime')).toHaveCount(0);
    await page.locator('#w-aWhen-comment').check();
    await expect(page.locator('#w-aTime')).toHaveCount(0);
    await page.locator('#w-aWhen-daily').check();
    await expect(page.locator('#w-aTime')).toBeVisible();
    await page.locator('#w-aWhen-comment').check();
    await page.locator('#wizard .wtop .linkbtn').click(); // 直接看結果
    await expect(rec(page)).toHaveAttribute('data-way', 'action');
    await page.locator('#wizard ol a[href="#st-ga-app"]').click();
    await expect(page.locator('#tab-learn')).toBeVisible();
    await expect(page.locator('#st-ga-app')).toBeVisible();
    await expect(page.locator('#ways [data-way="action"]')).toHaveAttribute('aria-pressed', 'true');
  });

  test('複製的內容跟畫面一致；重新整理後還在結果頁', async ({ page }) => {
    await page.goto('/agent.html');
    await applyTemplate(page, 'stock-close');
    await page.locator('button[data-copy="aOutA"]').click();
    expect(await clip(page)).toBe(await page.locator('#aOutA').inputValue());
    await page.locator('button[data-copy="aOutB"]').click();
    expect(await clip(page)).toBe(await page.locator('#aOutB').inputValue());
    await page.reload();
    await expect(page.locator('#tab-start')).toBeVisible();
    await expect(rec(page)).toHaveAttribute('data-way', 'routine');
  });

  test('舊版存檔（一長串表單）照樣讀得進來', async ({ page }) => {
    await page.goto('/agent.html');
    await page.evaluate(() => localStorage.setItem('agent-gen', JSON.stringify({ aTask: '舊的任務', aWhen: 'comment', tRepo: true, aBudget: '5', aOut: 'removed' })));
    await page.reload();
    await tab(page, 'start');
    await expect(rec(page)).toHaveAttribute('data-way', 'action');
    expect(await page.locator('#aOutB').inputValue()).toContain('舊的任務');
    // 不存在的選項回到預設
    await expect(page.locator('#wizard .sum').filter({ has: page.locator('[data-edit="q-out"]') })).toContainText('Claude App 裡的報告或檔案');
  });
});

test.describe('範例', () => {
  test('三個完整案例都在；範例可以依分類篩選，參考專案用 Repo X-Ray 打開', async ({ page }) => {
    await page.goto('/agent.html');
    await tab(page, 'examples');
    for (const id of ['case-close', 'case-brief', 'case-fix', 'case-backtest']) {
      const c = page.locator('#' + id);
      await c.locator('summary').click();
      expect(await c.locator('.cstep').count(), id).toBeGreaterThanOrEqual(4);
    }
    await page.locator('#tplFilters [data-cat="開發維護"]').click();
    for (const t of await page.locator('#tplGallery .tcard .meta').allInnerTexts()) expect(t).toContain('開發維護');
    await expect(page.locator('#tplGallery [data-tpl="fix-bug"] .refs a').first()).toHaveAttribute('href', /^xray\.html\?repo=[\w.-]+%2F[\w.-]+$/);
  });

  test('案例裡的步驟連結會切到 GitHub Actions 做法並打開那一步', async ({ page }) => {
    await page.goto('/agent.html');
    await tab(page, 'examples');
    await page.locator('#case-fix summary').click();
    await page.locator('#case-fix a[href="#st-ga-key"]').click();
    await expect(page.locator('#tab-learn')).toBeVisible();
    await expect(page.locator('#st-ga-key')).toBeVisible();
    await expect(page.locator('#st-ga-key')).toHaveAttribute('open', '');
  });

  test('我的 App 清單來自 apps.json；按「加 Agent」帶入 repo，建議用 Routine 開 PR', async ({ page }) => {
    await page.goto('/agent.html');
    await tab(page, 'examples');
    const rows = page.locator('#myApps [data-app]');
    await expect(rows).toHaveCount(APPS.length);
    await rows.first().getByRole('button', { name: '加 Agent' }).click();
    await expect(rec(page)).toHaveAttribute('data-way', 'routine');
    const href = await page.locator('#wizard a[data-open="aOutB"]').getAttribute('href');
    expect(new URL(href).searchParams.get('repositories')).toBe(APPS[0].repo);
    expect(await page.locator('#aOutB').inputValue()).toContain('結果用 Pull Request 交給我');
  });

  test.describe('apps.json 讀不到時', () => {
    test.use({ appsJson: 'fail' });
    test('顯示說明，教你改在起手式填 repo', async ({ page }) => {
      await page.goto('/agent.html');
      await tab(page, 'examples');
      await expect(page.locator('#myApps .note')).toContainText('最後一題');
    });
  });
});

test('參考資源可以依種類篩選，⭐ 是最推薦先看的', async ({ page }) => {
  await page.goto('/agent.html');
  await tab(page, 'refs');
  const all = await page.locator('#refList .refcard').count();
  await page.locator('#refFilters [data-kind="新手課程"]').click();
  const some = await page.locator('#refList .refcard').count();
  expect(some).toBeGreaterThan(0);
  expect(some).toBeLessThan(all);
  await page.locator('#refFilters [data-kind="全部"]').click();
  await expect(page.locator('#refList .refcard h3').filter({ hasText: '⭐' }).first()).toBeVisible();
  await expect(page.locator('#refList a[href^="xray.html?repo="]').first()).toBeVisible();
});

test('「裝完接著做」規則跟技能庫網頁一字不差（兩邊要一致）', async ({ page }) => {
  const tail = (s) => s.slice(s.indexOf('裝好後不要停'));
  await page.goto('/index.html');
  const fromIndex = tail(await page.locator('#autoPrompt').inputValue());
  await page.goto('/agent.html');
  await applyTemplate(page, 'stock-close');
  const fromAgent = tail(await page.locator('#aOutB').inputValue());
  expect(fromIndex.length).toBeGreaterThan(100);
  expect(fromAgent).toBe(fromIndex);
});

test('「⭐ 用我的技能」只列用到 skl 技能的範例，卡片標出用了哪些技能，套用後照樣產生指令', async ({ page }) => {
  await page.goto('/agent.html');
  await tab(page, 'examples');
  await page.locator('#tplFilters [data-cat="⭐ 用我的技能"]').click();
  const cards = page.locator('#tplGallery .tcard');
  expect(await cards.count()).toBeGreaterThanOrEqual(10);
  const withSkills = await page.locator('#tplGallery .tcard .tskills').count();
  expect(withSkills).toBe(await cards.count());
  const card = page.locator('#tplGallery [data-tpl="backtest-weekly"]');
  await expect(card.locator('.tskills')).toContainText('tw-stock');
  await card.locator('[data-apply="backtest-weekly"]').click();
  await expect(page.locator('#tab-start')).toBeVisible();
  await expect(page.locator('#wizard h2')).toHaveText('結果：照下面做');
  await expectNoHorizontalScroll(page);
});
