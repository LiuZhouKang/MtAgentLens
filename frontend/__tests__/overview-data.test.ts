import { describe, expect, it } from 'vitest'
import type { DatasetOverview } from '@/lib/types'
import { deriveDatasetSummary } from '@/lib/dataset-summary'

/** 造一个含真实分布的概览样本（值来自真实 run 的量级，非必精确）。 */
function sampleOverview(over: Partial<DatasetOverview> = {}): DatasetOverview {
  return {
    run: null,
    snapshots: { count: 0 },
    graph: { nodes: 0, edges: 0 },
    alerts: { count: 0 },
    evidence: { count: 0, by_match_level: {} },
    claims: { count: 0, by_type: {}, by_severity: {} },
    tracebacks: { count: 0 },
    intents: { count: 0, by_tool_name: {}, by_authority_source: {} },
    actions: { count: 0, by_action_type: {}, by_signal_class: {} },
    pollution: { count: 0 },
    policy_evaluations: { count: 0, violations: 0 },
    processes: { count: 0 },
    tls_sessions: { count: 0 },
    identity_process_map: { count: 0 },
    provisional_identities: { count: 0 },
    agent_identities: { count: 0 },
    message_bindings: { count: 0 },
    delegation_records: { count: 0 },
    subagent_boundaries: { count: 0 },
    refinements: { claim: 0, classification: 0, evidence: 0, graph: 0, identity: 0 },
    non_link_diagnostics: { count: 0 },
    ...over,
  }
}

describe('deriveDatasetSummary — 纯函数派生（Nothing is invented）', () => {
  it('computes a normal health badge when there are no criticals', () => {
    const s = deriveDatasetSummary(sampleOverview())
    expect(s.health).toBe('normal')
    expect(s.healthReason).toContain('无风险')
  })

  it('flips to critical when a Critical claim exists', () => {
    const s = deriveDatasetSummary(
      sampleOverview({ claims: { count: 1, by_type: {}, by_severity: { Critical: 1 } } }),
    )
    expect(s.health).toBe('critical')
  })

  it('derives the 6 KPI tiles from counts (文件活动 = File* actions 求和)', () => {
    const s = deriveDatasetSummary(
      sampleOverview({
        processes: { count: 160 },
        actions: { count: 3, by_action_type: { FileWrite: 2, FileRead: 1 }, by_signal_class: {} },
        intents: { count: 3, by_tool_name: { Bash: 2, Read: 1 }, by_authority_source: {} },
        tls_sessions: { count: 1067 },
        claims: { count: 2, by_type: {}, by_severity: { Warning: 2 } },
        alerts: { count: 13 },
      }),
    )
    expect(s.kpi).toHaveLength(6)
    const byKey = Object.fromEntries(s.kpi.map((t) => [t.key, t]))
    expect(byKey.processes?.value).toBe(160)
    expect(byKey.fileActivity?.value).toBe(3) // FileWrite 2 + FileRead 1
    expect(byKey.tools?.value).toBe(3)
    expect(byKey.network?.value).toBe(1067)
    expect(byKey.claims?.value).toBe(2)
    expect(byKey.alerts?.value).toBe(13)
    // claims KPI 的 hint 按 severity 分解
    expect(byKey.claims?.hint).toContain('2')
  })

  it('marks the claims KPI danger when Critical claims exist', () => {
    const s = deriveDatasetSummary(
      sampleOverview({ claims: { count: 3, by_type: {}, by_severity: { Critical: 3 } } }),
    )
    expect(s.kpi.find((t) => t.key === 'claims')?.tone).toBe('danger')
  })

  it('aggregates the four signal rules into rows with ×count', () => {
    const s = deriveDatasetSummary(
      sampleOverview({
        pollution: { count: 219 },
        claims: { count: 3, by_type: {}, by_severity: { Critical: 3 } },
        evidence: { count: 1823, by_match_level: { L5Substring: 830, L1Exact: 38 } },
        policy_evaluations: { count: 755, violations: 12 },
      }),
    )
    expect(s.signals).toHaveLength(4)
    const poll = s.signals.find((x) => x.category === 'context-pollution')!
    expect(poll.count).toBe(219)
    expect(poll.severity).toBe('Critical')
    const weak = s.signals.find((x) => x.category === 'weak-evidence')!
    expect(weak.count).toBe(830)
    expect(weak.detail).toContain('45.5') // 830/1823 占比（不四舍五入写死）
  })

  it('omits signal rules with zero hits (nothing is invented)', () => {
    const s = deriveDatasetSummary(sampleOverview())
    expect(s.signals).toHaveLength(0)
  })

  it('builds stat card groups from overview counts', () => {
    const s = deriveDatasetSummary(
      sampleOverview({
        snapshots: { count: 55 },
        graph: { nodes: 1461, edges: 3300 },
        tracebacks: { count: 22 },
        intents: { count: 23, by_tool_name: {}, by_authority_source: {} },
        actions: { count: 474, by_action_type: {}, by_signal_class: {} },
      }),
    )
    const byCategory = Object.fromEntries(s.statCards.map((c) => [c.category, c]))
    const graphCard = byCategory['图']
    expect(graphCard.items).toEqual([
      { label: '快照帧', value: 55 },
      { label: '节点', value: 1461 },
      { label: '边', value: 3300 },
    ])
    expect(byCategory['意图动作'].items).toContainEqual({ label: '意图', value: 23 })
    expect(byCategory['意图动作'].items).toContainEqual({ label: '动作', value: 474 })
  })
})
