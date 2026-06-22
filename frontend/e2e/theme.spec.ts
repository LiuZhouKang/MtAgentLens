import { test, expect, type Page } from '@playwright/test';

async function expectEffectiveTheme(page: Page, theme: 'light' | 'dark') {
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect.poll(async () => {
    return page.evaluate(() => document.documentElement.style.colorScheme);
  }).toBe(theme);
}

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.addInitScript(() => {
    if (!window.localStorage.getItem('app-theme')) {
      window.localStorage.setItem('app-theme', 'light');
    }
  });

  await page.goto('/?audit=headless');
  await page.waitForSelector('[data-testid="theme-toggle"]:not([disabled])', { timeout: 5000 });
  await expectEffectiveTheme(page, 'light');
});

test('theme-toggle button is rendered and clickable', async ({ page }) => {
  const toggle = page.locator('[data-testid="theme-toggle"]');
  await expect(toggle).toBeVisible();
  await expect(toggle).toBeEnabled();
  await expect(toggle).toHaveAttribute('title', /浅色模式/);
});

test('dark mode toggle persists across reload', async ({ page }) => {
  await page.click('[data-testid="theme-toggle"]');
  await expectEffectiveTheme(page, 'dark');
  await expect.poll(async () => page.evaluate(() => window.localStorage.getItem('app-theme'))).toBe('dark');

  await page.reload();
  await page.waitForSelector('[data-testid="theme-toggle"]:not([disabled])', { timeout: 5000 });
  await expectEffectiveTheme(page, 'dark');
});

test('cycles through light → dark → system', async ({ page }) => {
  const toggle = page.locator('[data-testid="theme-toggle"]');

  await toggle.click();
  await expectEffectiveTheme(page, 'dark');
  await expect(toggle).toHaveAttribute('title', /深色模式/);

  await toggle.click();
  await expect(toggle).toBeEnabled();
  await expectEffectiveTheme(page, 'light');
  await expect(toggle).toHaveAttribute('title', /跟随系统/);
  await expect.poll(async () => page.evaluate(() => window.localStorage.getItem('app-theme'))).toBe('system');
});

test('theme toggle works with keyboard', async ({ page }) => {
  const toggle = page.locator('[data-testid="theme-toggle"]');

  await toggle.focus();
  await page.keyboard.press('Enter');
  await expectEffectiveTheme(page, 'dark');
});

test('investigation page exposes theme toggle and no fatal console errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') {
      errors.push(message.text());
    }
  });

  await page.goto('/investigations#alertId=demo-alert-1');
  await expect(page.locator('[data-testid="theme-toggle"]')).toBeVisible();
  expect(errors.filter((item) => !item.includes('favicon'))).toEqual([]);
});

test('handoff page keeps the shared theme toggle visible', async ({ page }) => {
  await page.goto('/handoff');
  await expect(page.locator('[data-testid="theme-toggle"]')).toBeVisible();
});
