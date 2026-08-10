import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import {
  DatabaseImportError,
  extractActionsFromDb,
  extractAgentIdentitiesFromDb,
  extractAlertsFromDb,
  extractClaimsFromDb,
  extractCountsFromDb,
  extractDatasetOverviewFromDb,
  extractEvidenceFromDb,
  extractGraphFromDb,
  extractIdentityProcessMapFromDb,
  extractIntentsFromDb,
  extractNonLinkDiagnosticsFromDb,
  extractPolicyEvaluationsFromDb,
  extractPollutionEventsFromDb,
  extractProcessEventsFromDb,
  extractProvisionalIdentitiesFromDb,
  extractSnapshotFramesFromDb,
  extractSubagentBoundariesFromDb,
  extractTlsSessionsFromDb,
  extractTracebacksFromDb,
  loadDatabase,
  openDatabase,
  parseMatchedFields,
  parsePhaseIndicator,
} from '@/lib/db-import';

const fixtureBytes = (): Uint8Array => {
  const buf = readFileSync(new URL('./fixtures/db-sample.sqlite', import.meta.url));
  return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
};

describe('db-import — SQLite query layer', () => {
  it('opens a valid SQLite database from raw bytes', async () => {
    const db = await openDatabase(fixtureBytes());
    expect(db).toBeTruthy();
    db.close();
  });

  it('extracts the latest graph snapshot (nodes, edges, metrics, run_id)', async () => {
    const db = await openDatabase(fixtureBytes());
    const { graph, metrics, runId } = extractGraphFromDb(db);
    db.close();

    // Latest snapshot has 3 nodes / 2 edges; the older one (2 nodes) must be skipped.
    expect(graph.nodes).toHaveLength(3);
    expect(graph.edges).toHaveLength(2);
    expect(graph.nodes[0]).toMatchObject({ node_id: 'agent-a', node_type: 'Agent' });
    expect(graph.nodes[1].resource).toEqual({ File: { canonical_path: '/tmp/x.sh' } });
    expect(metrics).toMatchObject({
      agent_count: 1,
      node_counts: { Agent: 1, Resource: 1, Process: 1 },
      avg_confidence: 0.85,
    });
    expect(runId).toBe('run-1');
  });

  it('maps responsibility_alerts into the frontend alert shape', async () => {
    const db = await openDatabase(fixtureBytes());
    const alerts = extractAlertsFromDb(db);
    db.close();

    expect(alerts).toHaveLength(2);

    const first = alerts[0];
    expect(first.alert_id).toBe('alert-1');
    expect(first.title).toBe('Suspicious shell resource use');
    expect(first.decision).toBe('Unsafe');
    expect(first.severity).toBe('Warning');
    expect(first.display_profile).toBe('UnsafeByEngine');
    expect(first.primary_agent).toEqual({ agent_id: 'agent-a', claimed_role: null });
    expect(first.involved_agents).toEqual([{ agent_id: 'agent-a', claimed_role: null }]);
    expect(first.phase).toEqual({ Provisional: { timestamp_ns: 1100 } });
    expect(first.engine.rule_verdicts).toHaveLength(0);
    expect(first.timeline).toEqual({ steps: [], total_duration_ms: 0 });
    // graph_snapshot is kept lean — nodes/edges stripped to save memory.
    expect(first.graph_snapshot.nodes).toEqual([]);
    expect(first.graph_snapshot.metrics.avg_confidence).toBe(0.85);
    expect(first.provisional_ref).toBeNull();
  });

  it('parses Authoritative phase with overrides null', async () => {
    const db = await openDatabase(fixtureBytes());
    const alerts = extractAlertsFromDb(db);
    db.close();

    const second = alerts[1];
    expect(second.alert_id).toBe('alert-2');
    expect(second.phase).toEqual({
      Authoritative: { timestamp_ns: 1200, overrides: null },
    });
    expect(second.provisional_ref).toBe('alert-1');
    expect(second.decision).toBe('Safe');
  });

  it('maps correlation_evidence rows with parsed fields and phase', async () => {
    const db = await openDatabase(fixtureBytes());
    const evidence = extractEvidenceFromDb(db);
    db.close();

    expect(evidence).toHaveLength(3);
    expect(evidence[0]).toEqual({
      evidence_id: 'ev-1',
      intent_id: 'int-1',
      action_id: 'act-1',
      match_level: 'L1Exact',
      confidence: 0.95,
      matched_fields: {
        canonical_path: '/tmp/x.sh',
        host: null,
        port: null,
        command: 'bash /tmp/x.sh',
        raw_args: ['/tmp/x.sh'],
      },
      derivation_mode: 'Direct',
      phase: { Provisional: { timestamp_ns: 1000 } },
      timestamp_ns: 1000,
    });
    expect(evidence[2].phase).toEqual({
      Authoritative: { timestamp_ns: 1200, overrides: null },
    });
    expect(evidence[2].match_level).toBe('L5Substring');
  });

  it('counts tracebacks, claims, intents and actions defensively', async () => {
    const db = await openDatabase(fixtureBytes());
    const counts = extractCountsFromDb(db);
    db.close();

    expect(counts.tracebackCount).toBe(1);
    expect(counts.claimCount).toBe(2);
    // fixture now includes intents/actions tables (Task 2): int-1/int-2/int-3 and act-1/act-2/act-3.
    expect(counts.intentCount).toBe(3);
    expect(counts.actionCount).toBe(3);
  });

  it('extracts responsibility_claims with phase-derived timestamps', async () => {
    const db = await openDatabase(fixtureBytes());
    const claims = extractClaimsFromDb(db);
    db.close();

    expect(claims).toHaveLength(2);
    const first = claims[0];
    expect(first.claim_id).toBe('claim-1');
    expect(first.claim_type).toBe('TaintedContextUse');
    expect(first.subject_agent_id).toBe('agent-a');
    expect(first.policy_id).toBe('POLICY-1');
    expect(first.boundary_id).toBe('BOUNDARY-1');
    expect(first.claim_state).toBe('Inferred');
    expect(first.severity).toBe('Critical');
    expect(first.evidence_refs).toEqual(['ev-1', 'ev-2']);
    expect(first.competing_explanations[0].rule_id).toBe('R-ALT');
    // traceback_ref 真实 db 全 NULL —— 前端走反向索引，不按它取链
    expect(first.traceback_ref).toBeNull();
    // timestamp_ns 从 phase 提取（Provisional { timestamp_ns: 1000 }）
    expect(first.timestamp_ns).toBe(1000);
    expect(first.phase).toEqual({ Provisional: { timestamp_ns: 1000 } });
    // Authoritative phase 提取
    expect(claims[1].timestamp_ns).toBe(1200);
  });

  it('extracts responsibility_tracebacks with parsed chain ids', async () => {
    const db = await openDatabase(fixtureBytes());
    const tracebacks = extractTracebacksFromDb(db);
    db.close();

    expect(tracebacks).toHaveLength(1);
    expect(tracebacks[0]).toEqual({
      traceback_id: 'tb-1',
      claim_id: 'claim-1',
      chain_node_ids: ['agent-a'],
      chain_edge_ids: ['e1'],
      root_cause_node_id: 'agent-a',
      explanation_summary: 'traceback summary',
      confidence: 0.9,
    });
  });

  it('loadDatabase produces the full consumable snapshot', async () => {
    const result = await loadDatabase(fixtureBytes(), 'db-sample.sqlite');

    expect(result.id).toBe('db:db-sample.sqlite');
    expect(result.label).toBe('db-sample.sqlite');
    expect(result.nodeCount).toBe(3);
    expect(result.edgeCount).toBe(2);
    expect(result.alertCount).toBe(2);
    expect(result.evidenceCount).toBe(3);
    expect(result.tracebackCount).toBe(1);
    expect(result.claimCount).toBe(2);
    expect(result.runId).toBe('run-1');
    expect(result.hasConsumableData).toBe(true);
    expect(result.metrics.avg_confidence).toBe(0.85);

    // M0 全行深链资产
    expect(result.dbAssets).toBeDefined();
    expect(result.dbAssets.processEvents).toHaveLength(7);
    expect(result.dbAssets.processEventCount).toBe(7);
    expect(result.dbAssets.processEventTruncated).toBe(false);
    expect(result.dbAssets.tlsSessions).toHaveLength(6);
    expect(result.dbAssets.snapshotFrames).toHaveLength(4);
    expect(result.dbAssets.pollutionEvents).toHaveLength(3);
    expect(result.dbAssets.provisionalIdentities).toHaveLength(3);
    expect(result.dbAssets.agentIdentities).toHaveLength(2);
    expect(result.dbAssets.subagentBoundaries).toHaveLength(2);
  });

  it('hasConsumableData 纳入 process/tls（仅事件类数据也可消费）', async () => {
    const db = await openDatabase(fixtureBytes());
    db.run(
      'DROP TABLE graph_snapshots; DROP TABLE responsibility_alerts; DROP TABLE correlation_evidence;' +
        ' DROP TABLE responsibility_claims; DROP TABLE responsibility_tracebacks;' +
        ' DROP TABLE intents; DROP TABLE actions;',
    );
    const bytes = db.export();
    db.close();
    const result = await loadDatabase(new Uint8Array(bytes), 'events-only.db');
    expect(result.graph).toEqual({ nodes: [], edges: [] });
    expect(result.hasConsumableData).toBe(true); // processEvents/tlsSessions 仍有数据
    expect(result.dbAssets.processEvents.length).toBeGreaterThan(0);
  });

  it('rejects bytes that are not a SQLite database', async () => {
    const junk = new TextEncoder().encode('this is definitely not a sqlite file at all');
    await expect(loadDatabase(junk, 'bad.db')).rejects.toThrow(DatabaseImportError);
  });

  it('handles a database with no consumable tables gracefully', async () => {
    const db = await openDatabase(fixtureBytes());
    // hasConsumableData 覆盖全部已消费表（含 M0 全行表）——必须全 DROP 才能为 false。
    // IF EXISTS：M0 全行表在当前 fixture 造数前可能不存在。
    db.run(
      'DROP TABLE IF EXISTS graph_snapshots; DROP TABLE IF EXISTS responsibility_alerts;' +
        ' DROP TABLE IF EXISTS correlation_evidence;' +
        ' DROP TABLE IF EXISTS responsibility_claims; DROP TABLE IF EXISTS responsibility_tracebacks;' +
        ' DROP TABLE IF EXISTS intents; DROP TABLE IF EXISTS actions;' +
        ' DROP TABLE IF EXISTS process_events; DROP TABLE IF EXISTS tls_sessions;' +
        ' DROP TABLE IF EXISTS provisional_identities; DROP TABLE IF EXISTS agent_identities;' +
        ' DROP TABLE IF EXISTS identity_process_map; DROP TABLE IF EXISTS subagent_boundaries;' +
        ' DROP TABLE IF EXISTS delegation_records; DROP TABLE IF EXISTS context_pollution_events;' +
        ' DROP TABLE IF EXISTS policy_evaluations; DROP TABLE IF EXISTS non_link_diagnostics;',
    );
    const bytes = db.export();
    db.close();

    const result = await loadDatabase(new Uint8Array(bytes), 'empty.db');
    expect(result.graph).toEqual({ nodes: [], edges: [] });
    expect(result.alerts).toEqual([]);
    expect(result.evidence).toEqual([]);
    expect(result.dbAssets.processEvents).toEqual([]);
    expect(result.dbAssets.tlsSessions).toEqual([]);
    expect(result.hasConsumableData).toBe(false);
  });

  it('maps intents with process_id nesting and Array tool_args (not parseMatchedFields)', async () => {
    const db = await openDatabase(fixtureBytes());
    const intents = extractIntentsFromDb(db);
    db.close();

    expect(intents).toHaveLength(3);
    const bash = intents.find((i) => i.intent_id === 'int-1')!;
    expect(bash).toMatchObject({
      process_id: { pid: 42, start_time_ns: 1000 },
      message_id: 'msg-1',
      tool_name: 'Bash',
      intent_class: 'FileSystem',
      parse_state: 'Eligible',
      primary_matching_eligible: true,
      authority_source: 'SseResponseToolUse',
      timestamp_ns: 1000,
    });
    // 真实数组形态 —— 断言保留了 {Command} 键（parseMatchedFields 会把它清空）
    expect(bash.tool_args).toEqual([{ Command: 'ls -la /tmp' }]);
    expect(intents.find((i) => i.intent_id === 'int-3')!.primary_matching_eligible).toBe(false);
  });

  it('maps actions with process_id nesting and Array normalized_args', async () => {
    const db = await openDatabase(fixtureBytes());
    const actions = extractActionsFromDb(db);
    db.close();

    expect(actions).toHaveLength(3);
    const write = actions.find((a) => a.action_id === 'act-1')!;
    expect(write).toMatchObject({
      process_id: { pid: 42, start_time_ns: 1000 },
      action_type: 'FileWrite',
      derivation_mode: 'Direct',
      signal_class: 'HighSignal',
      timestamp_ns: 1000,
    });
    // 数组形态断言（{Path} 键必须保留）
    expect(write.normalized_args).toEqual([{ Path: '/tmp/x.sh' }]);
    expect(actions.find((a) => a.action_id === 'act-3')!.signal_class).toBe('HighSignal');
  });

  it('extracts DatasetOverview counts and distributions from every table', async () => {
    const db = await openDatabase(fixtureBytes());
    const overview = extractDatasetOverviewFromDb(db);
    db.close();

    expect(overview.run).toEqual({
      run_id: 'run-1',
      started_at_ns: null,
      ended_at_ns: null,
      scenario_id: null,
      mode: null,
      collector_version: null,
    });
    expect(overview.snapshots.count).toBe(4);
    expect(overview.graph).toEqual({ nodes: 0, edges: 0 }); // fixture 无 graph_nodes 表 → 0
    expect(overview.alerts.count).toBe(2);
    expect(overview.evidence.count).toBe(3);
    expect(overview.evidence.by_match_level).toEqual({
      L1Exact: 1,
      L3Containment: 1,
      L5Substring: 1,
    });
    expect(overview.claims.count).toBe(2);
    expect(overview.claims.by_type).toEqual({ TaintedContextUse: 1, PolicyViolation: 1 });
    expect(overview.claims.by_severity).toEqual({ Critical: 1, Warning: 1 });
    expect(overview.tracebacks.count).toBe(1);
    expect(overview.intents.count).toBe(3);
    expect(overview.intents.by_tool_name).toEqual({ Bash: 1, Read: 1, Glob: 1 });
    expect(overview.actions.count).toBe(3);
    expect(overview.actions.by_action_type).toEqual({ FileWrite: 1, FileRead: 1, ProcessExec: 1 });
    expect(overview.pollution.count).toBe(3);
    expect(overview.policy_evaluations.count).toBe(6);
    expect(overview.processes.count).toBe(7);
    expect(overview.tls_sessions.count).toBe(6);
    expect(overview.identity_process_map.count).toBe(7);
    expect(overview.provisional_identities.count).toBe(3);
    expect(overview.agent_identities.count).toBe(2);
    expect(overview.delegation_records.count).toBe(2);
    expect(overview.subagent_boundaries.count).toBe(2);
    expect(overview.refinements).toEqual({
      claim: 0,
      classification: 0,
      evidence: 0,
      graph: 0,
      identity: 0,
    });
    expect(overview.non_link_diagnostics.count).toBe(4);
  });
});

describe('parsePhaseIndicator', () => {
  it('parses Provisional', () => {
    expect(parsePhaseIndicator('Provisional { timestamp_ns: 123 }')).toEqual({
      Provisional: { timestamp_ns: 123 },
    });
  });

  it('parses Authoritative with null overrides', () => {
    expect(parsePhaseIndicator('Authoritative { timestamp_ns: 9, overrides: null }')).toEqual({
      Authoritative: { timestamp_ns: 9, overrides: null },
    });
  });

  it('returns null for non-phase strings and empty values', () => {
    expect(parsePhaseIndicator('garbage')).toBeNull();
    expect(parsePhaseIndicator(null)).toBeNull();
    expect(parsePhaseIndicator(undefined)).toBeNull();
    expect(parsePhaseIndicator('')).toBeNull();
  });
});

describe('parseMatchedFields', () => {
  it('parses a full fields object', () => {
    expect(
      parseMatchedFields(
        '{"canonical_path":"/x","host":null,"port":null,"command":"ls","raw_args":["-l"]}',
      ),
    ).toEqual({
      canonical_path: '/x',
      host: null,
      port: null,
      command: 'ls',
      raw_args: ['-l'],
    });
  });

  it('returns the empty shape for missing or malformed input', () => {
    expect(parseMatchedFields(null)).toEqual({
      canonical_path: null,
      host: null,
      port: null,
      command: null,
      raw_args: [],
    });
    expect(parseMatchedFields('{broken')).toEqual({
      canonical_path: null,
      host: null,
      port: null,
      command: null,
      raw_args: [],
    });
  });
});

describe('db-import — M0 全行提取器（pollution / policy / nld / identity / boundary）', () => {
  it('解析 context_pollution_events 全行，含 propagation_path 数组', async () => {
    const db = await openDatabase(fixtureBytes());
    const rows = extractPollutionEventsFromDb(db);
    db.close();
    expect(rows).toHaveLength(3);
    expect(rows[0]).toMatchObject({
      source_agent_id: 'agent-a',
      contaminated_artifact: '/tmp/leaked.txt',
      propagation_path: [],
      detected_at_ns: 1000,
    });
    // 2-hop 传播行：writer → middle → reader
    const hop = rows.find((r) => r.pollution_id === 3)!;
    expect(hop.propagation_path).toEqual(['agent-a', 'agent-b', 'agent-c']);
  });

  it('解析 policy_evaluations 全行，judgment.result 与 resource 诚实解析', async () => {
    const db = await openDatabase(fixtureBytes());
    const rows = extractPolicyEvaluationsFromDb(db);
    db.close();
    expect(rows).toHaveLength(6);
    expect(rows.find((r) => r.policy_id === 'pol-1')!.judgment?.result).toBe('Violation');
    expect(rows.find((r) => r.policy_id === 'pol-5')!.judgment?.result).toBe('Allow');
    // resource_json 为 '[]'（数组形态）→ 非 NormalizedArgs → null
    expect(rows.find((r) => r.policy_id === 'pol-1')!.resource).toBeNull();
  });

  it('解析 non_link_diagnostics 全行', async () => {
    const db = await openDatabase(fixtureBytes());
    const rows = extractNonLinkDiagnosticsFromDb(db);
    db.close();
    expect(rows).toHaveLength(4);
    expect(rows.find((r) => r.diagnostic_id === 'nld-4')).toMatchObject({
      intent_id: 'int-2',
      action_id: null,
      reason: 'AmbiguousMatch',
      timestamp_ns: 1100,
    });
  });

  it('解析 provisional / agent / identity_map 三表，升级交集 = 1', async () => {
    const db = await openDatabase(fixtureBytes());
    const provisional = extractProvisionalIdentitiesFromDb(db);
    const authoritative = extractAgentIdentitiesFromDb(db);
    const map = extractIdentityProcessMapFromDb(db);
    db.close();
    expect(provisional).toHaveLength(3);
    expect(authoritative).toHaveLength(2);
    expect(map).toHaveLength(7);
    const both = authoritative.filter((a) => provisional.some((p) => p.agent_id === a.agent_id));
    expect(both.map((a) => a.agent_id)).toEqual(['agent-a']);
    expect(provisional.find((p) => p.agent_id === 'agent-c')!.primary_signal).toBe('TlsSessionNew');
  });

  it('解析 subagent_boundaries 全行', async () => {
    const db = await openDatabase(fixtureBytes());
    const rows = extractSubagentBoundariesFromDb(db);
    db.close();
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({
      parent_agent_id: 'agent-a',
      child_agent_id: 'agent-b',
      parent_pid: 42,
      child_pid: 43,
    });
  });
});

describe('db-import — process/tls 全行 + JOIN 标注 + cap 优先', () => {
  it('process_events JOIN 身份标注：已识别 → agent_id，未识别 → null', async () => {
    const db = await openDatabase(fixtureBytes());
    const { items, total, truncated } = extractProcessEventsFromDb(db);
    db.close();
    expect(total).toBe(7);
    expect(truncated).toBe(false);
    const claude = items.find((r) => r.pid === 42)!;
    expect(claude.agent_id).toBe('agent-a');
    expect(claude.comm).toBe('claude');
    // 无 identity_map 映射的进程 → agent_id null
    expect(items.find((r) => r.pid === 300)!.agent_id).toBeNull();
  });

  it('tls_sessions JOIN 标注 + cap', async () => {
    const db = await openDatabase(fixtureBytes());
    const { items, total } = extractTlsSessionsFromDb(db);
    db.close();
    expect(total).toBe(6);
    expect(items.find((r) => r.tls_session_id === 's-1')!.agent_id).toBe('agent-a');
    expect(items.find((r) => r.tls_session_id === 's-4')!.agent_id).toBe('agent-b');
    expect(items.find((r) => r.tls_session_id === 's-5')!.agent_id).toBeNull();
  });

  it('已识别进程优先于 cap（合成 db，cap=3 截断时身份行仍完整）', async () => {
    const db = await openDatabase(fixtureBytes());
    // fixture 已含这两表，先 DROP 换为合成数据（cap 优先逻辑只针对合成行）
    db.run('DROP TABLE IF EXISTS process_events');
    db.run(
      'CREATE TABLE process_events (process_event_id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL, start_time_ns INTEGER NOT NULL, parent_pid INTEGER, parent_start_time_ns INTEGER, comm TEXT NOT NULL, timestamp_ns INTEGER NOT NULL, run_id TEXT NOT NULL)',
    );
    db.run('DROP TABLE IF EXISTS identity_process_map');
    db.run(
      'CREATE TABLE identity_process_map (agent_id TEXT NOT NULL, pid INTEGER NOT NULL, start_time_ns INTEGER NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(pid, start_time_ns))',
    );
    // 3 条未识别（时间靠前）+ 2 条已识别（时间靠后）
    for (const pid of [1, 2, 3]) {
      db.run(
        'INSERT INTO process_events (pid, start_time_ns, parent_pid, parent_start_time_ns, comm, timestamp_ns, run_id) VALUES (?,?,?,?,?,?,?)',
        [pid, 1000, null, null, 'u' + pid, 1000 + pid, 'run-x'],
      );
    }
    for (const pid of [9, 10]) {
      db.run(
        'INSERT INTO process_events (pid, start_time_ns, parent_pid, parent_start_time_ns, comm, timestamp_ns, run_id) VALUES (?,?,?,?,?,?,?)',
        [pid, 1000, null, null, 'x' + pid, 2000 + pid, 'run-x'],
      );
      db.run('INSERT INTO identity_process_map VALUES (?,?,?,?)', ['agent-x', pid, 1000, 'run-x']);
    }
    const { items, total, truncated } = extractProcessEventsFromDb(db, 3);
    db.close();
    expect(total).toBe(5);
    expect(truncated).toBe(true);
    // 已识别 2 条优先进入 cap，再按时间补 1 条未识别
    expect(items.map((r) => r.pid)).toEqual([9, 10, 1]);
    expect(items[0].agent_id).toBe('agent-x');
  });
});

describe('db-import — snapshot 演化帧（去重 + 降采样 + 提取即解析）', () => {
  it('fixture 4 帧全保留：snap-old null metrics 帧不跳过，图仍解析', async () => {
    const db = await openDatabase(fixtureBytes());
    const frames = extractSnapshotFramesFromDb(db);
    db.close();
    expect(frames).toHaveLength(4);
    // snap-old 帧 metrics null 但 graph 有节点（agent-old）
    const old = frames.find((f) => f.snapshot_id === 'snap-old')!;
    expect(old.metrics).toBeNull();
    expect(old.graph.nodes).toHaveLength(1);
    // 最新帧 = snap-latest
    expect(frames[3].snapshot_id).toBe('snap-latest');
    expect(frames[3].graph.nodes).toHaveLength(3);
  });

  it('连续相同 metrics 帧被去重（合成 db）', async () => {
    const db = await openDatabase(fixtureBytes());
    db.run('DROP TABLE graph_snapshots');
    db.run(
      'CREATE TABLE graph_snapshots (snapshot_id TEXT, timestamp_ns INTEGER, nodes_json TEXT, edges_json TEXT, metrics_json TEXT, run_id TEXT)',
    );
    const m = JSON.stringify({
      node_counts: { Agent: 1 },
      edge_counts: {},
      agent_count: 1,
      cross_agent_edges: 0,
      delegation_depth: 0,
      taint_chains: 0,
      avg_confidence: 0.5,
    });
    for (const [sid, ts] of [
      ['a', 1],
      ['b', 2],
      ['c', 3],
    ]) {
      db.run('INSERT INTO graph_snapshots VALUES (?,?,?,?,?,?)', [sid, ts, '[]', '[]', m, 'run-x']);
    }
    const frames = extractSnapshotFramesFromDb(db);
    db.close();
    expect(frames.map((f) => f.snapshot_id)).toEqual(['a']);
  });

  it('超过 maxFrames 均匀降采样，首尾恒保留', async () => {
    const db = await openDatabase(fixtureBytes());
    db.run('DROP TABLE graph_snapshots');
    db.run(
      'CREATE TABLE graph_snapshots (snapshot_id TEXT, timestamp_ns INTEGER, nodes_json TEXT, edges_json TEXT, metrics_json TEXT, run_id TEXT)',
    );
    const metrics = (n: number) =>
      JSON.stringify({
        node_counts: { Agent: n },
        edge_counts: {},
        agent_count: n,
        cross_agent_edges: 0,
        delegation_depth: 0,
        taint_chains: 0,
        avg_confidence: 0.5,
      });
    for (let i = 1; i <= 10; i += 1) {
      db.run('INSERT INTO graph_snapshots VALUES (?,?,?,?,?,?)', [
        'f' + i,
        i,
        '[]',
        '[]',
        metrics(i),
        'run-x',
      ]);
    }
    const frames = extractSnapshotFramesFromDb(db, 4);
    db.close();
    expect(frames).toHaveLength(4);
    expect(frames[0].snapshot_id).toBe('f1');
    expect(frames[3].snapshot_id).toBe('f10');
    // 均匀等距：round((10-1)/3 * i) = 0,3,6,9 → f1,f4,f7,f10
    expect(frames.map((f) => f.snapshot_id)).toEqual(['f1', 'f4', 'f7', 'f10']);
  });

  it('坏 nodes_json 帧 → 空图（不抛错）', async () => {
    const db = await openDatabase(fixtureBytes());
    db.run('DROP TABLE graph_snapshots');
    db.run(
      'CREATE TABLE graph_snapshots (snapshot_id TEXT, timestamp_ns INTEGER, nodes_json TEXT, edges_json TEXT, metrics_json TEXT, run_id TEXT)',
    );
    db.run('INSERT INTO graph_snapshots VALUES (?,?,?,?,?,?)', [
      'bad',
      1,
      '{broken',
      '[]',
      null,
      'run-x',
    ]);
    const frames = extractSnapshotFramesFromDb(db);
    db.close();
    expect(frames).toHaveLength(1);
    expect(frames[0].graph.nodes).toEqual([]);
    expect(frames[0].metrics).toBeNull();
  });
});
