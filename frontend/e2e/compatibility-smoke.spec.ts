import { expect, test } from '@playwright/test';

const routes = [
  {
    path: '/',
    title: 'MtAgentLens — 多 Agent 责任归因界面',
    assert: async (page: import('@playwright/test').Page) => {
      await expect(page.getByRole('heading', { name: /把多 Agent/i })).toBeVisible();
      await expect(page.getByRole('button', { name: '进入风险调查' })).toBeVisible();
    },
  },
  {
    path: '/investigations#alertId=demo-alert-1',
    title: 'MtAgentLens — 多 Agent 责任归因界面',
    assert: async (page: import('@playwright/test').Page) => {
      await expect(page.getByRole('heading', { name: /Agent "security-auditor" 尝试权限提升/ })).toBeVisible();
      await expect(page.getByRole('link', { name: '首页' })).toBeVisible();
    },
  },
  {
    path: '/handoff',
    title: 'MtAgentLens — 多 Agent 责任归因界面',
    assert: async (page: import('@playwright/test').Page) => {
      await expect(page.getByRole('heading', { name: /把委托链、扇出风险和边界漂移放回同一张工作台/ })).toBeVisible();
      await expect(page.getByRole('link', { name: '返回主界面' })).toBeVisible();
    },
  },
];

test.describe('cross-browser compatibility smoke', () => {
  for (const route of routes) {
    test(`${route.path} renders core content without fatal regressions`, async ({ page }) => {
      const consoleErrors: string[] = [];
      page.on('console', (message) => {
        if (message.type() === 'error') {
          consoleErrors.push(message.text());
        }
      });

      await page.goto(route.path, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveTitle(route.title);
      await route.assert(page);

      const dimensions = await page.evaluate(() => ({
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
      }));

      expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1);
      expect(consoleErrors.filter((error) => !error.includes('favicon'))).toEqual([]);
    });
  }
});
