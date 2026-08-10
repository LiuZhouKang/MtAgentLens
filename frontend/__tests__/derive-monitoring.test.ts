import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import initSqlJs from 'sql.js';
import {
  deriveMonitoringState,
  type MonitoringDeriveInput,
} from '@/lib/monitoring/derive-monitoring';
import {
  extractSnapshotMetricsFromDb,
  extractMismatchAggregateFromDb,
  extractPollutionAggregateFromDb,
  extractPolicyComplianceFromDb,
  extractTlsSessionAggregateFromDb,
} from '@/lib/db-import';
import type { Database } from 'sql.js';

async function openFixtureDb(): Promise<Database> {
  const buf = readFileSync(new URL('./fixtures/db-sample.sqlite', import.meta.url));
  const bytes = new Uint8Array(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
  const SQL = await initSqlJs();
  return new SQL.Database(bytes);
}

describe('deriveMonitoringState — 6 视角比率派生', () => {
  it('fixture 派生：漂移 warning、健康 warning、合规 warning、其余 normal，全 available', async () => {
    const db = await openFixtureDb();
    const input: MonitoringDeriveInput = {
      sourceLabel: 'fixture',
      derivedAtMs: 0,
      snapshots: extractSnapshotMetricsFromDb(db),
      alerts: [],
      pollution: extractPollutionAggregateFromDb(db),
      mismatch: extractMismatchAggregateFromDb(db),
      policy: extractPolicyComplianceFromDb(db),
      tls: extractTlsSessionAggregateFromDb(db),
      intents: [],
      actions: [],
    };
    db.close();
    const m = deriveMonitoringState(input);
    expect(m.health.topologyDrift.status).toBe('warning'); // driftRatio 0.5
    expect(m.health.topologyDrift.score).toBe(50);
    expect(m.health.topologyHealth.status).toBe('warning'); // pollutionCount 2
    expect(m.health.intentMismatch.status).toBe('normal'); // density 3/3=1
    expect(m.health.policyCompliance.status).toBe('warning'); // 1/3≈0.33 > 0.05
    expect(m.health.toolFootprint.status).toBe('normal'); // 空数组 → 无信号
    expect(m.health.networkSessions.status).toBe('normal');
    for (const key of Object.keys(m.health) as (keyof typeof m.health)[]) {
      expect(m.health[key].available).toBe(true);
    }
    expect(m.topologyDrift?.delta).toEqual({ nodes: 1, edges: 0, agents: 0 });
  });

  it('漂移阈值边界：driftRatio≥2 → critical、≥0.5 → warning、否则 normal', () => {
    const base: MonitoringDeriveInput = {
      sourceLabel: 't',
      derivedAtMs: 0,
      snapshots: { available: true, frames: [] },
      alerts: [],
      pollution: { available: true, count: 0, bySource: {} },
      mismatch: { available: true, count: 0, byReason: {}, weak: 0, strong: 0 },
      policy: { available: true, total: 0, violations: 0, byBoundary: {} },
      tls: { available: true, total: 0, byPid: {}, trend: [] },
      intents: [],
      actions: [],
    };
    const withNodes = (a: number, b: number): MonitoringDeriveInput => ({
      ...base,
      snapshots: {
        available: true,
        frames: [
          {
            snapshot_id: 'f0',
            timestamp_ns: 0,
            nodeCount: a,
            edgeCount: a,
            agentCount: a,
            crossAgentEdges: 0,
            delegationDepth: 0,
            taintChains: 0,
            avgConfidence: 0,
          },
          {
            snapshot_id: 'f1',
            timestamp_ns: 1,
            nodeCount: b,
            edgeCount: b,
            agentCount: b,
            crossAgentEdges: 0,
            delegationDepth: 0,
            taintChains: 0,
            avgConfidence: 0,
          },
        ],
      },
    });
    expect(deriveMonitoringState(withNodes(10, 10)).health.topologyDrift.status).toBe('normal'); // 0
    expect(deriveMonitoringState(withNodes(10, 15)).health.topologyDrift.status).toBe('warning'); // 0.5
    expect(deriveMonitoringState(withNodes(10, 30)).health.topologyDrift.status).toBe('critical'); // 2.0
    // 全零帧 → normal（无基线）
    expect(deriveMonitoringState(base).health.topologyDrift.status).toBe('normal');
  });

  it('缺表视角：available=false，对应 state 为 null，score null', () => {
    const m = deriveMonitoringState({
      sourceLabel: 't',
      derivedAtMs: 0,
      snapshots: { available: false, frames: [] },
      alerts: [],
      pollution: { available: false, count: 0, bySource: {} },
      mismatch: { available: false, count: 0, byReason: {}, weak: 0, strong: 0 },
      policy: { available: false, total: 0, violations: 0, byBoundary: {} },
      tls: { available: false, total: 0, byPid: {}, trend: [] },
      intents: [],
      actions: [],
    });
    expect(m.health.topologyDrift.available).toBe(false);
    expect(m.health.topologyDrift.status).toBe('normal');
    expect(m.health.topologyDrift.score).toBe(null);
    expect(m.topologyDrift).toBeNull();
    expect(m.health.policyCompliance.available).toBe(false);
    expect(m.policyCompliance).toBeNull();
  });

  it('拓扑健康按告警严重度 + 污染计数派生状态', () => {
    const base = (
      alerts: MonitoringDeriveInput['alerts'],
      pollutionCount: number,
    ): MonitoringDeriveInput => ({
      sourceLabel: 't',
      derivedAtMs: 0,
      snapshots: { available: true, frames: [] },
      alerts,
      pollution: { available: true, count: pollutionCount, bySource: {} },
      mismatch: { available: true, count: 0, byReason: {}, weak: 0, strong: 0 },
      policy: { available: true, total: 0, violations: 0, byBoundary: {} },
      tls: { available: true, total: 0, byPid: {}, trend: [] },
      intents: [],
      actions: [],
    });
    const critical = { severity: 'Critical', primary_agent: { agent_id: 'a', claimed_role: null } };
    const warning = { severity: 'Warning', primary_agent: { agent_id: 'a', claimed_role: null } };
    expect(deriveMonitoringState(base([critical as never], 0)).health.topologyHealth.status).toBe(
      'critical',
    );
    expect(deriveMonitoringState(base([warning as never], 0)).health.topologyHealth.status).toBe(
      'warning',
    );
    expect(deriveMonitoringState(base([], 3)).health.topologyHealth.status).toBe('warning');
    expect(deriveMonitoringState(base([], 0)).health.topologyHealth.status).toBe('normal');
  });
});
