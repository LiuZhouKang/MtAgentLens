// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import initSqlJs from 'sql.js';
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

describe('监控聚合提取器', () => {
  it('extractSnapshotMetricsFromDb 返回非空帧并按时间升序，跳过 null metrics 帧', async () => {
    const db = await openFixtureDb();
    const r = extractSnapshotMetricsFromDb(db);
    db.close();
    expect(r.available).toBe(true);
    expect(r.frames.map((f) => f.snapshot_id)).toEqual(['snap-zero', 'snap-mid', 'snap-latest']);
    expect(r.frames[0].nodeCount).toBe(0); // snap-zero 全零
    expect(r.frames[1].nodeCount).toBe(2); // snap-mid Agent1+Resource1
    expect(r.frames[2].nodeCount).toBe(3); // snap-latest Agent+Resource+Process
  });

  it('extractMismatchAggregateFromDb 统计 reason 分布与弱证据', async () => {
    const db = await openFixtureDb();
    const r = extractMismatchAggregateFromDb(db);
    db.close();
    expect(r.available).toBe(true);
    expect(r.count).toBe(3);
    expect(r.byReason).toEqual({ BelowConfidenceCutoff: 2, NoArgumentMatch: 1 });
    expect(r.weak).toBe(1); // ev-3 L5Substring
    expect(r.strong).toBe(2); // ev-1 L1Exact + ev-2 L3Containment
  });

  it('extractPollutionAggregateFromDb 按 source_agent_id 聚合', async () => {
    const db = await openFixtureDb();
    const r = extractPollutionAggregateFromDb(db);
    db.close();
    expect(r.available).toBe(true);
    expect(r.count).toBe(2);
    expect(r.bySource).toEqual({ 'agent-a': 1, 'agent-b': 1 });
  });

  it('extractPolicyComplianceFromDb 解析 judgment_json.result 得真实违规数', async () => {
    const db = await openFixtureDb();
    const r = extractPolicyComplianceFromDb(db);
    db.close();
    expect(r.available).toBe(true);
    expect(r.total).toBe(3);
    expect(r.violations).toBe(1); // 仅 pol-1 是 Violation
    expect(r.byBoundary).toEqual({ Capability: 3 });
  });

  it('extractTlsSessionAggregateFromDb 按 60s 桶聚合', async () => {
    const db = await openFixtureDb();
    const r = extractTlsSessionAggregateFromDb(db);
    db.close();
    expect(r.available).toBe(true);
    expect(r.total).toBe(4);
    expect(r.byPid).toEqual({ '42': 2, '43': 2 });
    expect(r.trend.map((b) => b.count)).toEqual([2, 2]);
    expect(r.trend[1].t - r.trend[0].t).toBe(60_000_000_000);
  });

  it('缺表提取器降级：available=false 且聚合归零', async () => {
    const SQL = await initSqlJs();
    const db = new SQL.Database(); // 空 db，无任何表
    const snapshots = extractSnapshotMetricsFromDb(db);
    const mismatch = extractMismatchAggregateFromDb(db);
    const pollution = extractPollutionAggregateFromDb(db);
    const policy = extractPolicyComplianceFromDb(db);
    const tls = extractTlsSessionAggregateFromDb(db);
    db.close();
    expect(snapshots.available).toBe(false);
    expect(snapshots.frames).toEqual([]);
    expect(mismatch).toEqual({ available: false, count: 0, byReason: {}, weak: 0, strong: 0 });
    expect(pollution).toEqual({ available: false, count: 0, bySource: {} });
    expect(policy).toEqual({ available: false, total: 0, violations: 0, byBoundary: {} });
    expect(tls).toEqual({ available: false, total: 0, byPid: {}, trend: [] });
  });
});
