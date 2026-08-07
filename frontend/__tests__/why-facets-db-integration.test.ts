import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { loadDatabase } from '@/lib/db-import';
import { buildWhyFacets } from '@/lib/why-facets';

/**
 * M5 真实数据集成验证：db-sample.sqlite → loadDatabase 导入管线 → buildWhyFacets。
 *
 * 覆盖 spec §十「fixture 真值校准」从真实导入数据的端到端复现——这是对
 * 「纯函数单测（fixture 校准）」与「真实导入数据」之间断层的最终确认。
 * 若此处断言与 db 实际内容背离，说明导入管线或分面规则需要修正。
 */
describe('M5 why-facets 真实 db 集成', () => {
  it('真实导入 → 分面计数与摘要全量符合 spec §十', async () => {
    const bytes = fs.readFileSync(path.join(__dirname, 'fixtures/db-sample.sqlite'));
    const dbImport = await loadDatabase(bytes, 'db-sample.sqlite');

    // 前置：真实 db 的三表行数与告警 involved_agents 符合校准假设
    expect(dbImport.alerts.map((a) => a.involved_agents.map((x) => x.agent_id))).toEqual([
      ['agent-a'], ['agent-a'],
    ]);
    expect(dbImport.dbAssets.pollutionEvents).toHaveLength(3);
    expect(dbImport.dbAssets.policyEvaluations).toHaveLength(6);
    expect(dbImport.dbAssets.nonLinkDiagnostics).toHaveLength(4);

    const why = buildWhyFacets({
      alerts: dbImport.alerts,
      pollutionEvents: dbImport.dbAssets.pollutionEvents,
      policyEvaluations: dbImport.dbAssets.policyEvaluations,
      nonLinkDiagnostics: dbImport.dbAssets.nonLinkDiagnostics,
      intents: dbImport.intents,
      evidence: dbImport.evidence,
      graph: dbImport.graph,
    });

    // §十 实测预期
    expect(why.facetCounts).toEqual({ pollution: 2, policy: 2, diagnostic: 0 });
    expect(why.alertFacets['alert-1']).toEqual(['pollution', 'policy']);
    expect(why.alertFacets['alert-2']).toEqual(['pollution', 'policy']);

    expect(why.pollution.bySource).toEqual([
      { agentId: 'agent-a', count: 2 }, { agentId: 'agent-b', count: 1 },
    ]);
    expect(why.pollution.hubAgents).toEqual(['agent-a', 'agent-b']);
    expect(why.pollution.multiHopCount).toBe(1);
    expect(why.pollution.maxHops).toBe(3);
    expect(why.pollution.avgHops).toBe(1.0);

    expect(why.policy.byJudgment).toEqual([
      { result: 'Compliant', count: 4 }, { result: 'Allow', count: 1 }, { result: 'Violation', count: 1 },
    ]);
    expect(why.policy.violationCount).toBe(1);
    expect(why.policy.violationRate).toBeCloseTo(1 / 6, 5);

    expect(why.diagnostic.byReason).toEqual([
      { reason: 'BelowConfidenceCutoff', count: 2 }, { reason: 'AmbiguousMatch', count: 1 }, { reason: 'NoArgumentMatch', count: 1 },
    ]);
    expect(why.diagnostic.byIntentId).toEqual([
      { intentId: 'int-2', toolName: 'Read', count: 2 },
      { intentId: 'int-1', toolName: 'Bash', count: 1 },
      { intentId: 'int-3', toolName: 'Glob', count: 1 },
    ]);
    expect(why.diagnostic.intentsAffected).toBe(3);
  });
});
