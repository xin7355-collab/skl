// 技能庫網頁（web/index.html）
const fs = require('fs');
const path = require('path');
const { test, expect, expectNoHorizontalScroll } = require('./fixtures');

const APPS = JSON.parse(fs.readFileSync(path.join(__dirname, '../../.github/apps.json'), 'utf8')).apps.map((a) => a.repo);

async function openOps(page) {
  await page.goto('/index.html');
  await page.locator('nav.tabs button[data-tab="ops"]').click();
  await expect(page.locator('#tab-ops')).toBeVisible();
}

test('每個網頁都有 noindex（公開 repo 的網站不讓搜尋引擎收錄）', async ({ page }) => {
  for (const p of ['/index.html', '/xray.html', '/ai.html', '/dev-guide.html', '/agent.html']) {
    await page.goto(p);
    await expect(page.locator('meta[name="robots"]'), p).toHaveAttribute('content', /noindex/);
  }
});

test('每個分頁在手機寬度都不會左右滑', async ({ page }) => {
  await page.goto('/index.html');
  for (const tab of ['packs', 'browse', 'ops', 'help']) {
    await page.locator(`nav.tabs button[data-tab="${tab}"]`).click();
    await expect(page.locator('#tab-' + tab)).toBeVisible();
    await expectNoHorizontalScroll(page);
  }
});

test.describe('該裝哪些技能（原生下拉選單）', () => {
  test('選單列出 apps.json 的每個 App，選了指令就跟著換', async ({ page }) => {
    await openOps(page);
    const pick = page.locator('#advPick');
    await expect(pick.locator('option')).toHaveCount(APPS.length + 2);   // 「目前這個 repo」＋App＋「其他」
    await expect(page.locator('#advPrompt')).toHaveValue(/請檢查目前這個 repo/);
    await expect(page.locator('#advXray')).toBeDisabled();

    await pick.selectOption(APPS[0]);
    await expect(page.locator('#advPrompt')).toHaveValue(new RegExp('https://github.com/' + APPS[0]));
    await expect(page.locator('#advPrompt')).toHaveValue(/先不要安裝/);
    await expect(page.locator('#advXray')).toBeEnabled();
    await expect(page.locator('#advRepo')).toBeHidden();
  });

  test('選「其他」才出現網址框，.git 結尾也認得', async ({ page }) => {
    await openOps(page);
    await page.locator('#advPick').selectOption('__url');
    await expect(page.locator('#advRepo')).toBeVisible();
    await expect(page.locator('#advXray')).toBeDisabled();
    await page.locator('#advRepo').fill('https://github.com/foo/bar.git');
    await expect(page.locator('#advPrompt')).toHaveValue(/https:\/\/github\.com\/foo\/bar（/);
    await expect(page.locator('#advXray')).toBeEnabled();
  });

  test('重新整理後還記得上次選的 App，按 X-Ray 會開那個 App', async ({ page }) => {
    const app = APPS[APPS.length - 1];
    await openOps(page);
    await page.locator('#advPick').selectOption(app);
    await page.reload();
    await page.locator('nav.tabs button[data-tab="ops"]').click();
    await expect(page.locator('#advPick')).toHaveValue(app);
    await page.locator('#advXray').click();
    await expect(page).toHaveURL(new RegExp('xray\\.html\\?repo=' + encodeURIComponent(app).replace(/[.]/g, '\\.')));
  });

  test('複製按鈕複製的就是畫面上的指令', async ({ page }) => {
    await openOps(page);
    await page.locator('#advPick').selectOption(APPS[0]);
    await page.locator('button[data-copy="advPrompt"]').click();
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip).toBe(await page.locator('#advPrompt').inputValue());
  });
});

test.describe('App 清單讀不到時', () => {
  test.use({ appsJson: 'fail' });
  test('顯示提示、仍可貼網址，不會看起來像壞掉', async ({ page }) => {
    await openOps(page);
    await expect(page.locator('#advNote')).toBeVisible();
    await expect(page.locator('#advNote')).toContainText('貼 GitHub 網址');
    await expect(page.locator('#advPick option')).toHaveCount(2);
  });
});

// 使用者要求：確認要裝技能後，不要停在「裝好了」——先列執行方案，再直接接著把技能用上
test.describe('安裝技能的指令：裝完先列執行方案，再直接接著做', () => {
  const FOLLOW = /執行方案[\s\S]*直接照方案做/;
  const clip = (page) => page.evaluate(() => navigator.clipboard.readText());

  test('「讓 App 自己挑」與「組合安裝」', async ({ page }) => {
    await page.goto('/index.html');
    await expect(page.locator('#autoPrompt')).toHaveValue(FOLLOW);
    await expect(page.locator('#packPrompt')).toHaveValue(FOLLOW);
  });

  test('瀏覽技能：單個複製、全部安裝、讓 App 挑', async ({ page }) => {
    await page.goto('/index.html');
    await page.locator('nav.tabs button[data-tab="browse"]').click();
    const first = page.locator('#skillList .skill').first();
    await first.locator('.top > button').click();
    expect(await clip(page)).toMatch(FOLLOW);
    await first.locator('input[type="checkbox"]').check();
    await page.locator('#copySel').click();
    expect(await clip(page)).toMatch(FOLLOW);
    await page.locator('#copyPick').click();
    expect(await clip(page)).toMatch(FOLLOW);
  });

  test('「該裝哪些技能」：這一步只建議，我說要裝之後才照方案做', async ({ page }) => {
    await openOps(page);
    const v = await page.locator('#advPrompt').inputValue();
    expect(v).toMatch(/先不要安裝/);
    expect(v).toMatch(/我說要裝之後/);
    expect(v).toMatch(FOLLOW);
  });
});

// claude.ai 網頁版不能從 GitHub 裝技能，只能上傳 ZIP；部署時 build_zips.py 打包並把路徑寫進 catalog.json
test.describe('技能卡片的「下載 ZIP」（給 claude.ai 網頁版上傳）', () => {
  async function openBrowse(page) {
    await page.goto('/index.html');
    await page.locator('nav.tabs button[data-tab="browse"]').click();
    await expect(page.locator('#skillList .skill').first()).toBeVisible();
  }

  test('每張卡片都有下載連結，href 是 zips/<catalog 路徑>.zip，旁邊寫上傳位置', async ({ page }) => {
    await openBrowse(page);
    const cards = page.locator('#skillList .skill');
    const n = await cards.count();
    expect(n).toBeGreaterThan(0);
    for (let i = 0; i < Math.min(n, 10); i++) {
      const card = cards.nth(i);
      const link = card.locator('a.zip');
      await expect(link).toHaveText('下載 ZIP');
      await expect(link).toHaveAttribute('download', '');
      const p = await card.locator('.pa').textContent();
      await expect(link).toHaveAttribute('href', `zips/${p}.zip`);
      await expect(card.locator('.dl small')).toContainText('Customize → Skills');
    }
    await expectNoHorizontalScroll(page);
  });

  test('連結真的下載得到 ZIP，根目錄是技能名稱的資料夾（claude.ai 的規定）', async ({ page }) => {
    await openBrowse(page);
    await page.locator('#q').fill('archify');
    const card = page.locator('#skillList .skill').filter({ hasText: 'community/skills/archify' }).first();
    const link = card.locator('a.zip');
    await expect(link).toHaveAttribute('href', 'zips/community/skills/archify.zip');
    const res = await page.request.get(await link.getAttribute('href'));
    expect(res.status()).toBe(200);
    const buf = await res.body();
    expect(buf.readUInt32LE(0)).toBe(0x04034b50);   // ZIP 本地檔頭 "PK\x03\x04"
    const nameLen = buf.readUInt16LE(26);
    expect(buf.subarray(30, 30 + nameLen).toString('utf8')).toMatch(/^archify\//);
  });

  test('catalog.json 沒有 zip 欄位的技能不顯示連結（不會點到 404）', async ({ page }) => {
    await page.route('**/catalog.json', async (route) => {
      const res = await route.fetch();
      const cat = await res.json();
      for (const s of cat.skills) delete s.zip;
      await route.fulfill({ response: res, json: cat });
    });
    await openBrowse(page);
    await expect(page.locator('#skillList a.zip')).toHaveCount(0);
  });

  test('說明分頁寫出官方的上傳步驟與出處', async ({ page }) => {
    await page.goto('/index.html');
    await page.locator('nav.tabs button[data-tab="help"]').click();
    const help = page.locator('#zipHelp');
    await help.locator('summary').click();
    await expect(help).toContainText('Customize → Skills');
    await expect(help).toContainText('Upload a skill');
    await expect(help).toContainText('Settings → Capabilities');
    await expect(help.locator('a[href^="https://support.claude.com/"]')).toHaveCount(1);
  });
});
