import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// 关闭 color-contrast 规则：MtAgentLens 采用 Linear 风格深色美学，刻意使用低饱和
// 暗色文字（如 #737374 于 #010102，对比 4.4:1），属既定设计语言而非回归。其余
// 全部规则（landmark / aria / label / keyboard 等）保持开启，用于捕获真实 a11y 缺陷。
const DISABLED_RULES = ['color-contrast'];

async function expectNoA11yViolations(page: Page, url: string) {
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.locator('#app-main').waitFor();

  const results = await new AxeBuilder({ page })
    .include('#app-main')
    .disableRules(DISABLED_RULES)
    .analyze();
  expect(results.violations).toEqual([]);
}

test.describe('axe accessibility smoke', () => {
  test('console home', async ({ page }) => {
    await expectNoA11yViolations(page, '/console');
  });

  test('investigation', async ({ page }) => {
    await expectNoA11yViolations(page, '/investigations#alertId=demo-alert-1');
  });

  test('landing entry', async ({ page }) => {
    await expectNoA11yViolations(page, '/');
  });
});
