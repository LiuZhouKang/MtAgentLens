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

  test('investigation page keeps primary navigation keyboard reachable', async ({ page }) => {
    await page.goto('/investigations#alertId=demo-alert-1', { waitUntil: 'domcontentloaded' });

    await page.keyboard.press('Tab');
    const skipLink = page.getByRole('link', { name: '跳到主要内容' });
    await expect(skipLink).toBeFocused();

    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: '首页' })).toBeVisible();
  });

  test('handoff page keeps overview navigation keyboard reachable', async ({ page }) => {
    await page.goto('/handoff', { waitUntil: 'domcontentloaded' });

    await page.keyboard.press('Tab');
    const skipLink = page.getByRole('link', { name: '跳到主要内容' });
    await expect(skipLink).toBeFocused();

    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: '返回主界面' })).toBeVisible();
  });
});
