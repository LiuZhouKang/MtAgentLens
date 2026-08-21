import { test, expect } from '@playwright/test';
import path from 'node:path';
import { readFileSync } from 'node:fs';

// 后端 run 路径的最小 mock。用 page.route 在浏览器层拦截（Playwright 实践），
// 而不是起一个真实 http server 监听固定端口——后者在 fullyParallel 多 project
// worker 并行下必然 EADDRINUSE。route 拦截发生在请求到达 dev server 之前，因此
// 也不受 /api/* → 7397 重写影响。
const MOCK_RUN_ID = '20260716-114859-ecb03ced';
const MOCK_DB_PATH = path.resolve(
  __dirname,
  '../../env/runs/20260716-114859-ecb03ced/collector/collector.responsibility.db',
);

/** 拦截 /api/runs + /api/runs/:id/db，模拟真实 Rust 收集器（database: true）。 */
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
            artifact_availability: {
              database: true,
              alerts: true,
              correlation_evidence: true,
              events: true,
            },
          },
        ],
      }),
    }),
  );
  await page.route(`**/api/runs/${MOCK_RUN_ID}/db`, (route) =>
    route.fulfill({
      contentType: 'application/x-sqlite3',
      body: readFileSync(MOCK_DB_PATH),
    }),
  );
}

// 真实数据端到端验收：消费真正的 collector.responsibility.db。
//
// 数据源选择器 → 导入本地 .db 文件 → sql.js 在浏览器解析 → 图 / 告警 / 证据
// 全部就绪，且统计数字与 SQLite 真实内容一致（env/runs 下的真实 run）。
// 文件来自仓库自身的运行产物，不是 fixture：改用它处 db 时请同步更新下方
// 三个统计断言（节点 / 告警 / 证据）。

const REAL_DB = path.resolve(
  __dirname,
  '../../env/runs/20260716-114859-ecb03ced/collector/collector.responsibility.db',
);

// 真实 run 的关键计数（来自该 db 的 graph_snapshots 最新一帧 + 各表 COUNT）
const EXPECTED = {
  nodes: 1461,
  alerts: 13,
  evidence: 1823,
  meta: '1461 节点 · 13 告警 · 1823 证据 · 22 声明 · 22 回溯 · 23 意图 · 474 动作',
};

// 有完整图但 responsibility_alerts 为空的 run —— 曾导致态势页永远 LoadingScreen
// + 告警页误导性空态。回归钉住修复后的行为（态势渲染 + 空态带统计）。
const NO_ALERT_DB = path.resolve(
  __dirname,
  '../../env/runs/20260716-022905-d958784b/collector/collector.responsibility.db',
);
const NO_ALERT_EXPECTED = { nodes: 38, evidence: 55 };

function trigger(page: import('@playwright/test').Page) {
  return page.getByTestId('data-source-trigger');
}

/** 打开数据源选择器（要求当前处于关闭态）。 */
async function openSelector(page: import('@playwright/test').Page) {
  await trigger(page).click();
  await expect(page.locator('[role="listbox"]')).toBeVisible({ timeout: 5000 });
}

/** 关闭数据源选择器：Escape（搜索框 autoFocus，keydown 冒泡到 listbox 处理）。 */
async function closeSelector(page: import('@playwright/test').Page) {
  await page.keyboard.press('Escape');
  await expect(page.locator('[role="listbox"]')).toBeHidden({ timeout: 5000 });
}

test.describe('真实 db 导入（collector.responsibility.db）', () => {
  test('导入后数据源统计、告警队列与证据全部就绪', async ({ page }) => {
    await page.goto('/console', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.incident-rail')).toBeVisible({ timeout: 20000 });

    await openSelector(page);

    // 通过隐藏的 file input 导入真实 db（59MB → sql.js 解析需要数秒）
    await page.locator('input[type="file"]').setInputFiles(REAL_DB);

    // trigger 切换为 db 文件名 = 导入已落库到 store
    await expect(trigger(page)).toContainText('collector.responsibility.db', {
      timeout: 30000,
    });

    // 等 popover 完全关闭后再重开，避免与 setOpen(false) 竞态
    await expect(page.locator('[role="listbox"]')).toBeHidden({ timeout: 5000 });
    await trigger(page).click();

    // 来源条目显示真实统计（db 导入才有证据计数）
    await expect(page.getByText(EXPECTED.meta)).toBeVisible({ timeout: 15000 });

    await closeSelector(page);

    // 告警工作台：真实 13 条告警
    await page.getByRole('link', { name: '告警', exact: true }).click();
    await expect(page.locator('.ln-alert-queue__btn')).toHaveCount(EXPECTED.alerts, {
      timeout: 30000,
    });

    // 证据就绪：证据 dock 徽标 = selectedModel.evidenceRefs.length，真实 db 应 > 0
    const evDock = page.getByRole('button', { name: '打开证据工作台' });
    // rail 的 InvestigationModel 由 useAlertModels 在 rIC 分块中延迟计算（12ms/块，
    // 最坏 1000ms/块 × 13 告警）。fullyParallel 多浏览器并行 + 59MB db 解析会饿死
    // idle callback，最坏 ~20s+ 才渲染 dock。60s 是 rIC 保证上界（13s）的数倍余量。
    await expect(evDock).toBeVisible({ timeout: 60000 });
    const evBadgeCount = parseInt((await evDock.textContent())?.replace(/\D/g, '') ?? '0', 10);
    expect(evBadgeCount).toBeGreaterThan(0);

    // 打开证据工作台：关联记录（linkedEvidence）就绪
    await evDock.click();
    await expect(page.getByText('关联记录')).toBeVisible({ timeout: 10000 });

    // 选中告警的标题来自真实 db（非空）
    const firstAlertTitle = (await page.locator('.ln-alert-queue__btn').first().textContent()) ?? '';
    expect(firstAlertTitle.trim().length).toBeGreaterThan(0);
  });

  test('拖放导入 db 同样生效（同一 importFile 管线）', async ({ page }) => {
    await page.goto('/console', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.incident-rail')).toBeVisible({ timeout: 20000 });

    await openSelector(page);

    // 通过 DataTransfer 构造 File 并派发 drop。用小型合成 db（32KB，
    // 3 节点/2 告警/3 证据）经 base64 注入，避免把大文件序列化进 evaluate。
    const b64 = readFileSync(
      path.join(process.cwd(), '__tests__', 'fixtures', 'db-sample.sqlite'),
    ).toString('base64');
    const dropped = await page.evaluate(async (bytesB64) => {
      const bin = atob(bytesB64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
      const file = new File([bytes], 'db-sample.sqlite', { type: 'application/x-sqlite3' });
      const dt = new DataTransfer();
      dt.items.add(file);
      const el = document.querySelector('[role="listbox"]');
      if (!el) return 'no-listbox';
      el.dispatchEvent(
        new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }),
      );
      return 'dropped';
    }, b64);

    expect(dropped).toBe('dropped');

    // drop 与 setInputFiles 共用 importFile → trigger 显示文件名
    await expect(trigger(page)).toContainText('db-sample.sqlite', { timeout: 30000 });

    await expect(page.locator('[role="listbox"]')).toBeHidden({ timeout: 5000 });
    await trigger(page).click();
    await expect(page.getByText('3 节点 · 2 告警 · 3 证据')).toBeVisible({ timeout: 15000 });
  });

  test('有图无告警 db：态势渲染而非卡 LoadingScreen，告警空态带数据源统计', async ({ page }) => {
    // 真实监控 run 常无触发告警。此前态势页把「无告警」误判为「加载中」→ 永远
    // LoadingScreen（aria-busy 卡死），告警页则误导性提示「等待实时观测会话接入」。
    await page.goto('/console', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.incident-rail')).toBeVisible({ timeout: 20000 });

    await openSelector(page);
    await page.locator('input[type="file"]').setInputFiles(NO_ALERT_DB);
    await expect(trigger(page)).toContainText('collector.responsibility.db', {
      timeout: 30000,
    });

    // 态势页渲染出 3D canvas，且不再停留在 aria-busy 加载态
    await expect(page.locator('canvas')).toBeVisible({ timeout: 20000 });
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);

    // 告警空态应报告已解析的计数，而非误导「等待实时会话接入」
    await page.getByRole('link', { name: '告警', exact: true }).click();
    await expect(
      page.getByText(
        `已解析 ${NO_ALERT_EXPECTED.nodes} 节点 · ${NO_ALERT_EXPECTED.evidence} 条证据`,
      ),
    ).toBeVisible({ timeout: 15000 });
  });

  test('后端 run 经 /api/runs/:id/db 消费（database: true 识别 + 全链路渲染）', async ({
    page,
  }) => {
    // goto 前先挂拦截：initData 会自动识别 database:true 的 run 并
    // loadRun → 下载 db → sql.js 解析。这是"后端 run → db 消费"的完整浏览器链路。
    await mockBackendRoutes(page);
    await page.goto('/console', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.incident-rail')).toBeVisible({ timeout: 20000 });

    // loadRun 完成（59MB db 下载 + 解析）后，trigger 显示 scenario 标签
    await expect(trigger(page)).toContainText('real-run', { timeout: 40000 });

    // 告警 / 证据来自 db：进入告警工作台核对
    await page.getByRole('link', { name: '告警', exact: true }).click();
    await expect(page.locator('.ln-alert-queue__btn')).toHaveCount(EXPECTED.alerts, {
      timeout: 30000,
    });

    const evDock = page.getByRole('button', { name: '打开证据工作台' });
    // rail 的 InvestigationModel 由 useAlertModels 在 rIC 分块中延迟计算（12ms/块，
    // 最坏 1000ms/块 × 13 告警）。fullyParallel 多浏览器并行 + 59MB db 解析会饿死
    // idle callback，最坏 ~20s+ 才渲染 dock。60s 是 rIC 保证上界（13s）的数倍余量。
    await expect(evDock).toBeVisible({ timeout: 60000 });
    const evBadgeCount = parseInt((await evDock.textContent())?.replace(/\D/g, '') ?? '0', 10);
    expect(evBadgeCount).toBeGreaterThan(0);
  });
});
