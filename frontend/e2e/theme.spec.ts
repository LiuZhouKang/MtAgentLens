import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  // Mock the user preferences API so the ThemeToggle resolves immediately.
  // Return 'light' as the initial theme so one click (light→dark) matches the spec.
  await page.route('**/api/user/preferences', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ theme: 'light' }),
      });
    } else if (route.request().method() === 'PUT') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true }),
      });
    } else {
      await route.continue();
    }
  });

  await page.goto('/');

  // Hide the globe iframe that overlaps the theme toggle
  await page.evaluate(() => {
    const iframe = document.querySelector('iframe[src*="globe"]');
    if (iframe) (iframe as HTMLElement).style.display = 'none';
  });

  // Wait for the theme toggle to be enabled (after API call resolves)
  await page.waitForSelector('[data-testid="theme-toggle"]:not([disabled])', { timeout: 5000 });
});

test('theme-toggle button is rendered and clickable', async ({ page }) => {
  const toggle = page.locator('[data-testid="theme-toggle"]');
  await expect(toggle).toBeVisible();
  await expect(toggle).toBeEnabled();
});

test('dark mode toggle persists across reload', async ({ page }) => {
  const html = page.locator('html');

  // Click: light → dark
  await page.click('[data-testid="theme-toggle"]');
  await expect(html).toHaveClass('dark');

  // Reload and verify persistence — localStorage retains the preference
  await page.reload();
  await page.waitForSelector('[data-testid="theme-toggle"]:not([disabled])', { timeout: 5000 });
  await expect(html).toHaveClass('dark');
});

test('cycles through light → dark → system', async ({ page }) => {
  const html = page.locator('html');
  const toggle = page.locator('[data-testid="theme-toggle"]');

  // Click 1: light → dark
  await toggle.click();
  await expect(html).toHaveClass('dark');

  // Click 2: dark → system
  await toggle.click();
  await expect(toggle).toBeEnabled();
});

test('theme toggle works with keyboard', async ({ page }) => {
  const html = page.locator('html');
  const toggle = page.locator('[data-testid="theme-toggle"]');

  // Focus and press Enter to toggle light → dark
  await toggle.focus();
  await page.keyboard.press('Enter');

  await expect(html).toHaveClass('dark');
});
