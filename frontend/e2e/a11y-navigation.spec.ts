import { test, expect } from '@playwright/test';

test.describe('keyboard accessibility', () => {
  test('console home exposes skip link and keyboard focus path', async ({ page }) => {
    await page.goto('/console', { waitUntil: 'domcontentloaded' });
    // 等待主内容就绪，避免全量套件负载下 firefox 水合/时序抖动（既有 flaky：单跑过、
    // 全量偶发超时）。断言焦点落在 #app-main（跳转链接的真实行为）而非脆弱的 URL 哈希串。
    await expect(page.locator('#app-main')).toBeVisible();

    await page.keyboard.press('Tab');
    const skipLink = page.getByRole('link', { name: '跳到主要内容' });
    await expect(skipLink).toBeFocused();

    await page.keyboard.press('Enter');
    await expect(page.locator('#app-main')).toBeFocused({ timeout: 10000 });
    await expect(page).toHaveURL(/#app-main$/, { timeout: 10000 });
  });

  // 4 页面结构：每个页面都共享顶部主导航（态势/仪表盘/告警/追踪）与跳转链接。
  for (const route of ['/overview', '/investigations', '/traces'] as const) {
    test(`${route} keeps the primary navigation keyboard reachable`, async ({ page }) => {
      await page.goto(route, { waitUntil: 'domcontentloaded' });

      await page.keyboard.press('Tab');
      const skipLink = page.getByRole('link', { name: '跳到主要内容' });
      await expect(skipLink).toBeFocused();

      // 跳过链接后，下一个可达焦点即主导航首个 tab「态势」
      await page.keyboard.press('Tab');
      await expect(page.getByRole('link', { name: '态势', exact: true })).toBeVisible();
    });
  }
});
