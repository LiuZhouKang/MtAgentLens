import { test, expect } from '@playwright/test';
import path from 'node:path';
import { readFileSync } from 'node:fs';

// 原始事件流抽屉回归钉：
// - 抽屉必须位于 32px 顶栏之下（曾因 inset-y-0 顶到视口顶部，标题/关闭按钮被顶栏盖住 → 「超出屏幕」）。
// - 关闭按钮必须可点击（曾因抽屉 z-[80] 被 rail 包装容器 z-10 堆叠上下文钳制，右上角
//   实时告警流 z-20 绘制在抽屉之上，盖住关闭按钮与图标）。
// - 抽屉内容不得视觉溢出抽屉（overflow-hidden）。
// 修复：top-[32px] + overflow-hidden + createPortal(document.body) 逃逸堆叠上下文 + 内联 SVG 关闭图标。

const MOCK_RUN_ID = '20260716-114859-ecb03ced';
const REAL_DB = path.resolve(
  __dirname,
  '../../env/runs/20260716-114859-ecb03ced/collector/collector.responsibility.db',
);

async function mockBackendRoutes(page: import('@playwright/test').Page): Promise<void> {
  await page.route('**/api/runs', (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        runs: [
          {
            run_id: MOCK_RUN_ID,
            started_at_ns: 0,
            state: 'completed',
            scenario_ids: ['real-run'],
            artifact_availability: { database: true, alerts: true, correlation_evidence: true, events: true },
          },
        ],
      }),
    }),
  );
  await page.route(`**/api/runs/${MOCK_RUN_ID}/db`, (route) =>
    route.fulfill({
      contentType: 'application/x-sqlite3',
      body: readFileSync(REAL_DB),
    }),
  );
}

test.describe('原始事件流抽屉', () => {
  test('抽屉位于顶栏之下、关闭按钮可点击、内容不溢出', async ({ page }) => {
    test.setTimeout(150000);
    await mockBackendRoutes(page);
    await page.goto('/console', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.incident-rail')).toBeVisible({ timeout: 20000 });

    // 导入真实 db
    await page.getByTestId('data-source-trigger').click();
    await page.locator('[role="listbox"]').waitFor({ timeout: 5000 });
    await page.locator('input[type="file"]').setInputFiles(REAL_DB);
    await expect(page.getByTestId('data-source-trigger')).toContainText(
      'collector.responsibility.db',
      { timeout: 30000 },
    );
    await page.keyboard.press('Escape');

    // 展开事件工作台并打开原始事件流抽屉
    await page.getByRole('button', { name: '打开事件工作台' }).click({ timeout: 30000 });
    const railBtn = page
      .locator('aside[aria-label="事件调查控制轨"]')
      .getByRole('button', { name: '原始事件流', exact: true });
    await expect(railBtn).toBeVisible({ timeout: 15000 });
    await railBtn.click();
    const drawer = page.locator('aside[aria-label="原始事件流抽屉"]');
    const close = drawer.getByRole('button', { name: '关闭事件流' });
    await expect(drawer).toBeVisible({ timeout: 10000 });
    await page.waitForTimeout(1200);

    // ① 抽屉位于顶栏之下（top=32）——标题与关闭按钮完整可见
    const geom = await drawer.evaluate((el) => {
      const dr = el.getBoundingClientRect();
      const title = el.querySelector('h2') as HTMLElement | null;
      const close = el.querySelector('button[aria-label="关闭事件流"]') as HTMLElement | null;
      const drRect = { top: dr.top, bottom: dr.bottom, right: dr.right };
      return {
        drawer: drRect,
        belowTopBar: Math.round(dr.top) >= 32,
        titleVisible: title ? title.getBoundingClientRect().top >= 32 : false,
        closeVisible: close ? close.getBoundingClientRect().top >= 32 : false,
        parentIsBody: el.parentElement === document.body,
      };
    });
    expect(geom.belowTopBar).toBe(true);
    expect(geom.titleVisible).toBe(true);
    expect(geom.closeVisible).toBe(true);
    expect(geom.parentIsBody).toBe(true); // portal：z-[80] 已在根堆叠上下文生效

    // ② 关闭按钮不被遮挡：elementFromPoint 命中按钮自身或其 SVG 子元素
    const hitTest = await drawer.evaluate(() => {
      const close = document.querySelector('button[aria-label="关闭事件流"]') as HTMLElement | null;
      if (!close) return { ok: false, reason: 'no close btn' };
      const r = close.getBoundingClientRect();
      const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return { ok: !!el && (el === close || close.contains(el)), topmost: (el?.tagName ?? '') };
    });
    expect(hitTest.ok, `关闭按钮应可点击，实际命中 ${hitTest.topmost}`).toBe(true);

    // ③ 关闭按钮真实点击 → 抽屉关闭（重型页面点击后浏览器 settle 较慢，放宽超时）
    await close.click({ timeout: 60000 });
    await expect(drawer).toBeHidden({ timeout: 60000 });
  });
});
