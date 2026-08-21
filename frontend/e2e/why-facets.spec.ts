import { test, expect } from '@playwright/test';
import path from 'node:path';

// M5「为什么告警分面 + 全局归因」真实浏览器验收。
//
// 数据源为小型真实 fixture（3 节点/2 告警/3 证据 + 三张「为什么」表）：
// - 2 告警 involved 均 {agent-a} → pollution ×2 / policy ×2 / diagnostic ×0
// - pollutionEvents ×3（agent-a×2, agent-b×1，含 1 条多跳链 maxHops 3）
// - policyEvaluations ×6（Compliant×4 / Allow×1 / Violation×1 → violationRate 1/6）
// - nonLinkDiagnostics ×4（3 意图）
// 上述真值已由 __tests__/why-facets-db-integration.test.ts（真实导入管线）锁定，
// 此处断言必须与之保持一致 —— 浏览器层面复现同一套 spec §十真值。

const SAMPLE_DB = path.join(process.cwd(), '__tests__', 'fixtures', 'db-sample.sqlite');

/** 打开数据源选择器并导入 db-sample.sqlite（importFile 管线，与 db-import.spec 一致）。 */
async function importSampleDb(page: import('@playwright/test').Page): Promise<void> {
  await page.getByTestId('data-source-trigger').click();
  await expect(page.locator('[role="listbox"]')).toBeVisible({ timeout: 5000 });
  await page.locator('input[type="file"]').setInputFiles(SAMPLE_DB);
  await expect(page.getByTestId('data-source-trigger')).toContainText('db-sample.sqlite', {
    timeout: 30000,
  });
  // importFile 完成后 popover 自动关闭；等它消失再断言面板，避免与 store 更新竞态
  await expect(page.locator('[role="listbox"]')).toBeHidden({ timeout: 5000 });
}

/**
 * 归因面板 section。标题不可作为唯一锚点 —— 取证链面板在 live 态同样是「待 db 数据」；
 * 用 `全局归因` eyebrow（仅本面板持有）锚定后再按标题过滤。
 * 所有 chip / 折叠钮 / 摘要断言都 scoped 到面板内 —— 页面工作台可能出现同名文本按钮，
 * Playwright strict mode 不容重复匹配。
 */
function attributionPanel(page: import('@playwright/test').Page, title: string | RegExp) {
  return page
    .locator('section.ln-panel')
    .filter({
      has: page.getByText('全局归因'),
    })
    .filter({
      has: page.getByRole('heading', { name: title }),
    });
}

test.describe('M5 为什么告警分面 + 全局归因', () => {
  test('live 态（无 db）：面板待导入、chips 与折叠钮禁用', async ({ page }) => {
    await page.goto('/investigations', { waitUntil: 'domcontentloaded' });

    // 无 db → dbPresent=false：标题「待 db 数据」，导入提示可见
    const panel = attributionPanel(page, '待 db 数据');
    await expect(panel).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/导入 responsibility\.db 后启用/)).toBeVisible();

    // 全部 chips 与归因折叠钮均禁用（live 无可消费数据）
    for (const name of [/全部/, /污染/, /违规/, /失配/]) {
      await expect(panel.getByRole('button', { name })).toBeDisabled();
    }
    await expect(panel.getByRole('button', { name: /归因/ })).toBeDisabled();
  });

  test('导入 db：分面计数 / chips 可用态 / 归因三列摘要对齐真实真值', async ({ page }) => {
    await page.goto('/investigations', { waitUntil: 'domcontentloaded' });
    await importSampleDb(page);

    // 面板标题 = 真实计数（spec §十：pollution 2 / policy 2 / diagnostic 0）
    const panel = attributionPanel(page, '2 条告警 · 2 污染 / 2 违规 / 0 失配');
    await expect(panel).toBeVisible({ timeout: 15000 });

    // chips：0 命中（失配）禁用，其余启用
    await expect(panel.getByRole('button', { name: /污染 2/ })).toBeEnabled();
    await expect(panel.getByRole('button', { name: /违规 2/ })).toBeEnabled();
    await expect(panel.getByRole('button', { name: /失配 0/ })).toBeDisabled();

    // 展开归因 → 三列摘要（integration 测试锁定的 db 真值）。
    // 注意面板数为「事件数」而非 chip 的「告警数」：污染列 total=3（3 条传播事件），
    // 标题「2 污染」是命中分面的告警数 —— 二者单位不同，按 spec 设计。
    await panel.getByRole('button', { name: /归因/ }).click();
    await expect(panel.getByRole('button', { name: /归因/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    await expect(panel.getByText('3 链 · 多跳 1 · 最长 3 · 平均 1')).toBeVisible();
    await expect(panel.getByText('6 判定 · 违规 1（17%）')).toBeVisible();
    await expect(panel.getByText('4 诊断 · 涉及 3 意图')).toBeVisible();

    // 污染源头排行（真实 db：agent-a×2 居首）与枢纽。agent-a 同时出现在 bySource 行
    // 与枢纽 chip，逐元素断言会 strict 冲突 → 用污染列容器 toContainText 整体校验。
    const pollutionCol = panel.getByText('污染传播').locator('..');
    await expect(pollutionCol).toContainText('agent-a');
    await expect(pollutionCol).toContainText('×2');
    await expect(pollutionCol).toContainText('枢纽');
    await expect(pollutionCol).toContainText('agent-b');
  });

  test('分面交互：点污染 → URL ?facet=pollution、chip 生效、刷新深链保持、重新导入恢复', async ({
    page,
  }) => {
    await page.goto('/investigations', { waitUntil: 'domcontentloaded' });
    await importSampleDb(page);

    const panel = attributionPanel(page, '2 条告警 · 2 污染 / 2 违规 / 0 失配');
    await expect(panel).toBeVisible({ timeout: 15000 });

    const pollutionChip = panel.getByRole('button', { name: /污染 2/ });
    await expect(pollutionChip).toBeEnabled();
    await pollutionChip.click();

    // URL 进入 ?facet=pollution，chip 生效，aria-live 播报分面命中数
    await expect(page).toHaveURL(/facet=pollution/, { timeout: 5000 });
    await expect(pollutionChip).toHaveAttribute('aria-pressed', 'true');
    await expect(panel.getByText('2 条告警 · 污染分面')).toBeVisible();

    // 刷新 → URL 深链保持（reload 清空内存 store → live 态，分面被 effectiveFacet 强制 all）
    await page.reload();
    await expect(page).toHaveURL(/facet=pollution/);
    const livePanel = attributionPanel(page, '待 db 数据');
    await expect(livePanel).toBeVisible({ timeout: 15000 });
    await expect(livePanel.getByRole('button', { name: /污染 0/ })).toBeDisabled(); // live 无 db

    // 重新导入 db → 深链分面自动恢复生效（URL 是唯一权威，db 就绪即恢复）
    await importSampleDb(page);
    const restoredPanel = attributionPanel(page, '2 条告警 · 2 污染 / 2 违规 / 0 失配');
    await expect(restoredPanel.getByRole('button', { name: /污染 2/ })).toHaveAttribute(
      'aria-pressed',
      'true',
      {
        timeout: 15000,
      },
    );

    // 清除分面 → URL 移除 facet 参数，回到全部
    await restoredPanel.getByRole('button', { name: /清除分面/ }).click();
    await expect(page).not.toHaveURL(/facet=pollution/);
    await expect(restoredPanel.getByRole('button', { name: /污染 2/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });
});
