import { test, expect } from '@playwright/test';

test.describe('keyboard accessibility', () => {
  test('home exposes skip link and keyboard focus path', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    await page.keyboard.press('Tab');
    const skipLink = page.getByRole('link', { name: '跳到主要内容' });
    await expect(skipLink).toBeFocused();

    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/#app-main$/);
    await expect(page.locator('#app-main')).toBeVisible();
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
