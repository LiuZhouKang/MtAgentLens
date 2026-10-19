import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import {
  extractDatasetOverviewFromDb,
  extractIdentityProcessMapFromDb,
  extractProcessEventsFromDb,
  openDatabase,
} from '@/lib/db-import';

/**
 * 前端 SQL 契约：schema v2 把身份映射从一张表改成追加式
 * `identity_process_beliefs` + 兼容视图 `identity_process_map`。
 *
 * 前端本轮不改代码，所以这个测试的作用是证明「不改也能继续工作」：
 * 在真实 v1 fixture 上按 v2 的形状重建身份层后，所有既有查询（概览计数、
 * 身份全行、process JOIN 标注）结果必须逐字不变。
 */
const fixtureBytes = (): Uint8Array => {
  const buf = readFileSync(new URL('./fixtures/db-sample.sqlite', import.meta.url));
  return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
};

describe('schema v2 兼容视图 — 前端 SQL 契约', () => {
  it('身份层重建为 beliefs + 视图后，既有查询结果不变', async () => {
    const db = await openDatabase(fixtureBytes());

    const beforeMap = extractIdentityProcessMapFromDb(db);
    const beforeOverview = extractDatasetOverviewFromDb(db);
    const beforeProcesses = extractProcessEventsFromDb(db);

    db.run(`
      CREATE TABLE identity_process_beliefs (
        belief_id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT NOT NULL,
        pid INTEGER NOT NULL,
        start_time_ns INTEGER NOT NULL,
        run_id TEXT NOT NULL,
        belief_level TEXT NOT NULL CHECK (
          belief_level IN ('Unknown', 'Provisional', 'Inferred', 'Authoritative')
        ),
        observed_at_ns INTEGER NOT NULL,
        source TEXT NOT NULL
      );
      INSERT INTO identity_process_beliefs
        (agent_id, pid, start_time_ns, run_id, belief_level, observed_at_ns, source)
      SELECT agent_id, pid, start_time_ns, run_id, 'Authoritative', 0, 'v1_identity_process_map'
      FROM identity_process_map;
      DROP TABLE identity_process_map;
      CREATE VIEW identity_process_map AS
      SELECT agent_id, pid, start_time_ns, run_id
      FROM identity_process_beliefs b
      WHERE b.belief_id = (
        SELECT b2.belief_id FROM identity_process_beliefs b2
        WHERE b2.pid = b.pid AND b2.start_time_ns = b.start_time_ns
        ORDER BY CASE b2.belief_level
                   WHEN 'Authoritative' THEN 3
                   WHEN 'Inferred' THEN 2
                   WHEN 'Provisional' THEN 1
                   ELSE 0
                 END DESC,
                 b2.observed_at_ns DESC,
                 b2.belief_id DESC
        LIMIT 1
      );
    `);

    const afterMap = extractIdentityProcessMapFromDb(db);
    const afterOverview = extractDatasetOverviewFromDb(db);
    const afterProcesses = extractProcessEventsFromDb(db);
    db.close();

    expect(afterMap).toEqual(beforeMap);
    expect(afterOverview.identity_process_map.count).toBe(
      beforeOverview.identity_process_map.count,
    );
    expect(afterProcesses.items).toEqual(beforeProcesses.items);
    expect(afterProcesses.total).toBe(beforeProcesses.total);
  });
});
