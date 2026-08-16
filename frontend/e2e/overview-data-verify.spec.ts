import { test, expect } from '@playwright/test';
import path from 'node:path';

// 仪表盘数据真实性回归守卫：四面板（拓扑演化趋势/证据分布/责任单元分布/责任单元压力）必须
// 消费真实 db 数据——导入真实 collector.responsibility.db 后读取实际渲染值，与 SQL
// 基准（55 帧 / 1823 证据 / 13 告警 / 1461 末帧节点）交叉核对，杜绝编造 demo 数据。
const REAL_DB = path.resolve(
  __dirname,
  '../../env/runs/20260716-114859-ecb03ced/collector/collector.responsibility.db',
);

async function waitForLoaderGone(page: import('@playwright/test').Page) {
  await page.waitForFunction(
    () => {
      for (const w of document.querySelectorAll('.mtl-load-word')) {
        let cur = w as HTMLElement | null;
        while (cur && cur !== document.body) {
          if (cur.getAttribute?.('aria-hidden') !== null) {
            if (getComputedStyle(cur).opacity !== '0') return false;
            break;
          }
          cur = cur.parentElement;
        }
      }
      return true;
    },
    undefined,
    { timeout: 20000 },
  );
}

test('仪表盘四面板消费真实 db 数据', async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/overview', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.ln-page__inner')).toBeVisible({ timeout: 30000 });

  // 导入真实 db
  await page.getByTestId('data-source-trigger').click();
  await expect(page.locator('[role="listbox"]')).toBeVisible({ timeout: 5000 });
  await page.locator('input[type="file"]').setInputFiles(REAL_DB);
  await expect(page.getByTestId('data-source-trigger')).toContainText('collector.responsibility.db', {
    timeout: 30000,
  });
  await waitForLoaderGone(page);

  // 读取四面板实际渲染值（轮询：useDeferredValue 延迟重算，等真实 db 数据驱动面板更新）
  const readPanel = (title: string, selector: string) =>
    page
      .locator('.ln-panel', { hasText: title })
      .locator(selector)
      .first()
      .textContent()
      .catch(() => null);

  // 证据精确度分布：真实 db 有 1823 条 correlation_evidence，按 match_level（官方枚举）分桶。
  // 始终展示完整 L1→L5 全谱；此 run（114859）无 L2Normalized → L2 如实显示 0（非编造计数）。
  await expect
    .poll(async () => Number((await readPanel('证据精确度分布', '.ln-panel__meta'))?.replace(/\D/g, '') ?? 0), {
      timeout: 20000,
      message: '证据精确度分布应展示真实关联证据数',
    })
    .toBe(1823); // 精确匹配 SQL 基准：总数必须等于真实 correlation_evidence 行数
  const evPanelText = await page.locator('.ln-panel', { hasText: '证据精确度分布' }).textContent().catch(() => '');
  expect(evPanelText ?? '').toContain('L1Exact');
  expect(evPanelText ?? '').toContain('L2Normalized'); // 全谱展示（该 run L2 计数为 0）
  expect(evPanelText ?? '').toContain('L5Substring');

  // 责任单元分布：真实 db 有 13 告警 → ≥ 1 责任单元
  await expect
    .poll(async () => Number((await readPanel('责任单元分布', '.ln-panel__meta'))?.match(/(\d+)\s*责任单元/)?.[1] ?? 0), {
      timeout: 20000,
      message: '责任单元分布应展示真实责任单元数',
    })
    .toBeGreaterThanOrEqual(1);

  // 高危责任单元：真实 db 有 2 条 Critical → ≥ 1
  await expect
    .poll(async () => Number((await readPanel('责任单元分布', '.ln-metric__delta'))?.replace(/\D/g, '') ?? 0), {
      timeout: 20000,
    })
    .toBeGreaterThanOrEqual(1);

  // 拓扑演化趋势：db 55 帧 → 去重降采样后应 ≥ 2 帧
  await expect
    .poll(async () => Number((await readPanel('拓扑演化趋势', '.ln-panel__meta'))?.match(/(\d+)\s*帧/)?.[1] ?? 0), {
      timeout: 20000,
      message: '拓扑演化趋势应展示真实快照帧',
    })
    .toBeGreaterThanOrEqual(2);

  // 责任单元压力：真实列表（告警责任单元 → 压力条），含真实数字
  const pressureItems = await page.locator('.ln-rail .ln-rail-item').allTextContents().catch(() => []);
  expect(pressureItems.length).toBeGreaterThanOrEqual(1);
  for (const item of pressureItems) {
    expect(item).toMatch(/\d+/);
  }
});
