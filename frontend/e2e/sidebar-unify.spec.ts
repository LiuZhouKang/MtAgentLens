import { test, expect, type Page } from '@playwright/test';

// 侧边栏统一验收：4 页面结构 + 3 工作台 + 官方命名 + 数据对齐 + 实时响应。
// 页面已精简为 态势/仪表盘/告警/追踪，侧边栏 dock 为 事件/证据/链路。

async function openRail(page: Page) {
  await page.goto('/console', { waitUntil: 'domcontentloaded' });
  // 数据经 loader 兜底加载，重型首页在并行负载下可能需要更久
  await expect(page.locator('.incident-rail')).toBeVisible({ timeout: 20000 });
}

test.describe('侧边栏统一 · 4 页面结构 + 3 工作台', () => {
  test('顶部 4 tab 与侧边栏 3 工作台齐全，无死链', async ({ page }) => {
    await openRail(page);

    // 顶部 4 tab
    for (const tab of ['态势', '仪表盘', '告警', '追踪']) {
      await expect(page.getByRole('link', { name: tab, exact: true })).toBeVisible();
    }

    // dock 恰为 3 个工作台
    await expect(page.getByRole('button', { name: '打开事件工作台' })).toBeVisible();
    await expect(page.getByRole('button', { name: '打开证据工作台' })).toBeVisible();
    await expect(page.getByRole('button', { name: '打开链路工作台' })).toBeVisible();
    await expect(page.locator('.incident-rail__navitem')).toHaveCount(3);

    // 全页面不得存在指向已删除页面的死链
    await expect(
      page.locator('a[href^="/agents"], a[href^="/handoff"], a[href^="/clusters"]'),
    ).toHaveCount(0);
  });

  test('三工作台官方副标题 + 数据对齐（副标题 agent == 事实快照主体）', async ({ page }) => {
    await openRail(page);

    // 事件工作台
    await page.getByRole('button', { name: '打开事件工作台' }).click({ force: true });
    const subtitle = page.locator('.incident-rail__panel-heading small');
    await expect(subtitle).toContainText('事件研判 /');
    const subtitleText = (await subtitle.textContent()) ?? '';
    const agentFromSubtitle = subtitleText.match(/事件研判 \/ ([a-z0-9-]+)/)?.[1];
    expect(agentFromSubtitle).toBeTruthy();

    // 数据对齐：事实快照「主体」行 == 副标题里的主主体
    const subjectValue = await page
      .locator('.incident-rail__panel')
      .getByText('主体', { exact: true })
      .locator('..')
      .locator('span')
      .last()
      .textContent();
    expect(subjectValue?.trim()).toBe(agentFromSubtitle);

    // 证据工作台
    await page.getByRole('button', { name: '打开证据工作台' }).click({ force: true });
    await expect(subtitle).toContainText('证据核验 /');
    // 链路工作台
    await page.getByRole('button', { name: '打开链路工作台' }).click({ force: true });
    await expect(subtitle).toContainText('责任溯源 /');
  });

  test('实时更新：store 推送到修改 → 事件工作台响应式刷新', async ({ page }) => {
    await openRail(page);
    await page.getByRole('button', { name: '打开事件工作台' }).click({ force: true });
    const subtitle = page.locator('.incident-rail__panel-heading small');
    await expect(subtitle).toContainText('事件研判 /');

    // 读取当前选中告警的主主体
    const currentAgent =
      (await subtitle.textContent())?.match(/事件研判 \/ ([a-z0-9-]+)/)?.[1] ?? null;
    expect(currentAgent).toBeTruthy();

    // dev 钩子：把「当前选中告警」的主主体改为 agent-live。排序键（severity/
    // decision/timestamp）不变，选中项稳定，侧边栏应响应式刷新副标题与数据。
    await page.evaluate((agentId) => {
      const store = (
        window as unknown as {
          __MTA_LENS_STORE__?: { getState: () => any; setState: (patch: any) => void };
        }
      ).__MTA_LENS_STORE__;
      const alerts = (store?.getState()?.alerts ?? []) as any[];
      if (!alerts.length || !store) return;
      const updated = alerts.map((a: any) =>
        a.primary_agent?.agent_id === agentId
          ? { ...a, primary_agent: { agent_id: 'agent-live', claimed_role: 'live-injector' } }
          : a,
      );
      store.setState({ alerts: updated });
    }, currentAgent);

    await expect
      .poll(async () => (await subtitle.textContent()) ?? '', { timeout: 10000 })
      .toContain('事件研判 / agent-live');
  });
});
