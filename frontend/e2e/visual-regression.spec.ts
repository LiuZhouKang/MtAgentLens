import { test, expect } from '@playwright/test';

const FIXED_NOW_MS = Date.parse('2026-06-20T09:30:00.000Z');

test.use({
  viewport: { width: 1440, height: 900 },
  colorScheme: 'dark',
});

test.beforeEach(async ({ page }) => {
  await page.addInitScript((fixedNow) => {
    const fixed = Number(fixedNow);
    Date.now = () => fixed;
  }, FIXED_NOW_MS);

  await page.emulateMedia({ reducedMotion: 'reduce' });

  await page.addStyleTag({
    content: [
      '*{animation:none!important;transition:none!important}',
      'html{scroll-behavior:auto!important}',
    ].join(''),
  });
});

test.describe('visual regression', () => {
  test('investigations workbench', async ({ page }) => {
    await page.goto('/investigations#alertId=demo-alert-1', { waitUntil: 'domcontentloaded' });
    await page.locator('#app-main').waitFor();
    // 数据稳定后再截图 —— 并行负载下 #app-main 先于告警队列渲染，过早截图会抖
    await expect(page.getByText('告警队列')).toBeVisible({ timeout: 20000 });
    await expect(page.locator('#app-main')).toHaveScreenshot('investigations.png', {
      maxDiffPixelRatio: 0.01,
    });
  });
});
