import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import path from 'node:path';

const Database = createRequire(path.resolve(process.cwd(), 'package.json'))('better-sqlite3');

test('structured traceback branches render actual endpoints without parsing prose', async ({
  page,
}, testInfo) => {
  const source = new Database(path.resolve(__dirname, '../__tests__/fixtures/db-sample.sqlite'), {
    readonly: true,
  });
  const copy = new Database(source.serialize());
  source.close();
  const phase = { Provisional: { timestamp_ns: 1000 } };
  const nodes = [
    {
      node_id: 'agent-a',
      node_type: 'Agent',
      agent_id: 'agent-a',
      resource: null,
      evidence_refs: [],
      timestamp_ns: 1000,
      phase,
    },
    ...['branch-first', 'branch-second'].map((id) => ({
      node_id: id,
      node_type: 'Resource',
      agent_id: null,
      resource: { File: { canonical_path: `/tmp/${id}` } },
      evidence_refs: [],
      timestamp_ns: 1000,
      phase,
    })),
  ];
  const edges = ['branch-first', 'branch-second'].map((id) => ({
    edge_id: id,
    edge_type: 'IntentCausesAction',
    source: 'agent-a',
    target: id,
    evidence_refs: ['ev-1'],
    confidence: 0.8,
    phase,
  }));
  copy
    .prepare('UPDATE graph_snapshots SET nodes_json=?, edges_json=?')
    .run(JSON.stringify(nodes), JSON.stringify(edges));
  for (const [id, claimIds] of [
    ['alert-1', ['claim-1']],
    ['alert-2', []],
  ] as const) {
    const row = copy
      .prepare('SELECT engine_section_json FROM responsibility_alerts WHERE alert_id=?')
      .get(id) as { engine_section_json: string };
    const engine = { ...JSON.parse(row.engine_section_json), claim_ids: claimIds };
    copy
      .prepare('UPDATE responsibility_alerts SET engine_section_json=? WHERE alert_id=?')
      .run(JSON.stringify(engine), id);
  }
  copy
    .prepare(
      `UPDATE responsibility_tracebacks SET chain_node_ids_json=?, chain_edge_ids_json=?,
    root_cause_node_id=?, explanation_summary=? WHERE traceback_id='tb-1'`,
    )
    .run(
      JSON.stringify(['agent-a', 'branch-first', 'branch-second']),
      JSON.stringify(['branch-first', 'branch-second']),
      'agent-a',
      'Causal chain:\n  [start] Agent(forged-actor)\n  caused→ Agent(forged-target)',
    );
  const buffer = copy.serialize();
  copy.close();

  await page.goto('/investigations', { waitUntil: 'domcontentloaded' });
  const trigger = page.getByTestId('data-source-trigger');
  await trigger.click();
  await page.locator('input[type="file"]').setInputFiles({
    name: 'structured-traceback.db',
    mimeType: 'application/x-sqlite3',
    buffer,
  });
  await expect(trigger).toContainText('structured-traceback.db');
  const row = page.locator('[data-claim-row]').filter({ hasText: 'claim-1' });
  await expect(row).toBeVisible({ timeout: 30_000 });
  await row.click();
  const relationships = page.locator('[data-traceback-edge-id]');
  await expect(relationships).toHaveCount(2);
  for (const id of ['branch-first', 'branch-second']) {
    const edge = page.locator(`[data-traceback-edge-id="${id}"]`);
    await edge.scrollIntoViewIfNeeded();
    await expect(edge).toHaveCSS('opacity', '1');
    await expect(edge).toContainText('agent-a');
    await expect(edge).toContainText(`/tmp/${id}`);
  }
  await expect(page.getByText('根因候选', { exact: true })).toBeVisible();
  await expect(page.getByText('forged-actor', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('structured-relations.png'), fullPage: true });
  await page.locator('.ln-alert-queue__btn').filter({ hasText: 'Benign pipeline' }).click();
  await expect(page.getByText('0 条 · 本告警', { exact: true })).toBeVisible();
  await expect(page.getByText('暂无责任声明', { exact: true })).toBeVisible();
  await expect(page.locator('[data-claim-row]')).toHaveCount(0);
});

test('local multi-run import requires a choice and shows only the selected run', async ({
  page,
}) => {
  const db = new Database(path.resolve(__dirname, '../__tests__/fixtures/db-sample.sqlite'), {
    readonly: true,
  });
  const copy = new Database(db.serialize());
  db.close();
  copy.exec("INSERT INTO runs(run_id,started_at_ns) VALUES('isolated-run',999)");
  copy.exec(
    "INSERT INTO actions(action_id,pid,process_start_time_ns,action_type,normalized_args_json,derivation_mode,signal_class,timestamp_ns,run_id) VALUES('isolated-action',9,9,'FileOpen','[]','Direct','HighSignal',999,'isolated-run')",
  );
  const buffer = copy.serialize();
  copy.close();
  await page.goto('/console', { waitUntil: 'domcontentloaded' });
  const trigger = page.getByTestId('data-source-trigger');
  await expect(trigger).toBeVisible();
  await trigger.click();
  await page
    .locator('input[type="file"]')
    .setInputFiles({ name: 'multi-run.db', mimeType: 'application/x-sqlite3', buffer });
  const choices = page.getByRole('group', { name: '选择文件中的运行' });
  await expect(choices).toBeVisible();
  await expect(trigger).not.toContainText('multi-run.db');
  await choices.getByRole('button', { name: 'isolated-run', exact: true }).click();
  await expect(trigger).toContainText('isolated-run');
  await trigger.click();
  await expect(
    page.getByText(/0 节点 · 0 告警 · 0 证据 · 0 声明 · 0 回溯 · 0 意图 · 1 动作/),
  ).toBeVisible();
});

// 真实生产链路：Rust PersistenceWriter/PersistRecord -> schema-v4 SQLite ->
// 浏览器 sql.js 导入 -> 选择运行。测试数据由 Rust 集成测试通过
// MTAGENTLENS_CONTRACT_DB 导出，不是 SQL mock，也不是仓库内二进制 fixture。
test('real Rust writer export imports only the selected run', async ({ page }) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(15_000);
  const tmpDir = mkdtempSync(path.join(tmpdir(), 'mtagentlens-persistence-'));
  const dbPath = path.join(tmpDir, 'persistence-roundtrip.db');

  try {
    execFileSync(
      'flock',
      [
        '-F',
        '/tmp/mtagentlens-cargo.lock',
        'cargo',
        'test',
        '--test',
        'persistence_roundtrip',
        '--',
        '--nocapture',
      ],
      {
        cwd: path.resolve(__dirname, '../../collector'),
        env: { ...process.env, MTAGENTLENS_CONTRACT_DB: dbPath },
        stdio: 'pipe',
        timeout: 120_000,
      },
    );

    await page.goto('/console', { waitUntil: 'domcontentloaded' });
    const trigger = page.getByTestId('data-source-trigger');
    await expect(trigger).toBeVisible();
    await trigger.click();

    await page.locator('input[type="file"]').setInputFiles({
      name: 'persistence-roundtrip.db',
      mimeType: 'application/x-sqlite3',
      buffer: readFileSync(dbPath),
    });

    const choices = page.getByRole('group', { name: '选择文件中的运行' });
    await expect(choices).toBeVisible();
    await choices.getByRole('button', { name: 'persistence-run-a', exact: true }).click();
    await expect(trigger).toContainText('persistence-run-a');

    await trigger.click();
    await expect(
      page.getByText(/0 节点 · 1 告警 · 0 证据 · 1 声明 · 1 回溯 · 0 意图 · 0 动作/),
    ).toBeVisible({ timeout: 15000 });

    // Close the source menu before navigating to the selected run's alerts.
    await page.keyboard.press('Escape');
    await expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await page.getByRole('link', { name: '告警', exact: true }).click();
    const queue = page.locator('.ln-alert-queue__btn');
    await expect(queue).toHaveCount(1, { timeout: 30000 });
    await expect(queue).toContainText('persistence alert run-a');
  } finally {
    rmSync(tmpDir, { recursive: true, force: true });
  }
});
