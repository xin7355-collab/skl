// 網頁 E2E 測試：技能庫網頁（index.html）與 Repo X-Ray（xray.html）。
// 所有對外連線（GitHub API、raw、Google 翻譯）都在 fixtures.js 用假資料回應，
// 所以不連網、不花 GitHub 額度，CI 每次推送都能穩定跑。
const { defineConfig, devices } = require('@playwright/test');

const PORT = Number(process.env.E2E_PORT || 8790);

module.exports = defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.js$/,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  // 網路全部是假的，照理不會時好時壞；CI 留 1 次重試只為了吸收 runner 偶發的卡頓，
  // 重試才過的會在報告裡標成 flaky，要回頭查，不能當成沒事
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  timeout: 45000,
  expect: { timeout: 10000 },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    permissions: ['clipboard-read', 'clipboard-write'],
  },
  projects: [
    // 使用者只用 iPhone：用 iPhone 13 的畫面尺寸與觸控設定，瀏覽器用 Chromium（CI 上最穩）
    { name: 'iphone', use: { ...devices['iPhone 13'], browserName: 'chromium' } },
  ],
  webServer: {
    command: `python3 serve.py --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/index.html`,
    reuseExistingServer: !process.env.CI,
    timeout: 120000,
    // 預設是直接 SIGKILL；改送 SIGTERM，serve.py 才有機會刪掉暫存的 ZIP
    gracefulShutdown: { signal: 'SIGTERM', timeout: 5000 },
  },
});
