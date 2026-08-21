// Generates __tests__/fixtures/db-sample.sqlite — a small SQLite database that
// mirrors the real `collector.responsibility.db` tables the frontend consumes
// (graph_snapshots / responsibility_alerts / correlation_evidence /
// responsibility_tracebacks / responsibility_claims / intents / actions).
// Kept in-repo so db-import tests are hermetic and fast (no 59MB real db,
// no native deps).
//
// Run: node scripts/gen-db-fixture.mjs
import { writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import initSqlJs from 'sql.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

const node = (id, type, agentId, resource, ts) => ({
  node_id: id,
  node_type: type,
  agent_id: agentId,
  resource,
  evidence_refs: [],
  timestamp_ns: ts,
  phase: { Provisional: { timestamp_ns: ts } },
});

const edge = (id, type, source, target, conf, ts) => ({
  edge_id: id,
  edge_type: type,
  source,
  target,
  evidence_refs: [],
  confidence: conf,
  phase: { Provisional: { timestamp_ns: ts } },
  signal: null,
});

const nodesLatest = [
  node('agent-a', 'Agent', 'agent-a', null, 1000),
  node('file-sh', 'Resource', null, { File: { canonical_path: '/tmp/x.sh' } }, 1001),
  node(
    'proc-1',
    'Process',
    null,
    { Process: { process_id: { pid: 42, start_time_ns: 1000 } } },
    1002,
  ),
];
const edgesLatest = [
  edge('e1', 'IntentCausesAction', 'agent-a', 'proc-1', 0.9, 1002),
  edge('e2', 'ContextFlowsFrom', 'file-sh', 'agent-a', 0.8, 1001),
];
const nodesOld = [node('agent-old', 'Agent', 'agent-old', null, 500)];
const edgesOld = [];
const metrics = {
  node_counts: { Agent: 1, Resource: 1, Process: 1 },
  edge_counts: { IntentCausesAction: 1, ContextFlowsFrom: 1 },
  agent_count: 1,
  cross_agent_edges: 0,
  delegation_depth: 0,
  taint_chains: 0,
  avg_confidence: 0.85,
};
// 漂移帧 1：全零（早期帧），漂移基线不取它
const metricsZero = {
  node_counts: {},
  edge_counts: {},
  agent_count: 0,
  cross_agent_edges: 0,
  delegation_depth: 0,
  taint_chains: 0,
  avg_confidence: 0,
};
// 漂移帧 2：中间态（首个拓扑非零帧，漂移基线）。nodeCount=2（Agent1+Resource1），
// edgeCount=2（IntentCausesAction2）。相对 snap-latest(nodeCount=3) → driftRatio=|3-2|/2=0.5 → warning。
const metricsMid = {
  node_counts: { Agent: 1, Resource: 1 },
  edge_counts: { IntentCausesAction: 2 },
  agent_count: 1,
  cross_agent_edges: 0,
  delegation_depth: 0,
  taint_chains: 0,
  avg_confidence: 0.6,
};

const alert = (over) => ({
  alert_id: over.alert_id,
  timestamp_ns: over.ts,
  display_profile: over.display_profile ?? 'UnsafeByEngine',
  decision: over.decision ?? 'Unsafe',
  severity: over.severity ?? 'Warning',
  confidence: 0.85,
  authority: 'Authoritative',
  reasoning: 'Shell artifact touched by process spawned after context injection.',
  title: over.title ?? 'Suspicious shell resource use',
  summary: 'Detected curl exiting right after exec',
  action_hint: null,
  primary_agent_id: 'agent-a',
  involved_agents_json: JSON.stringify([{ agent_id: 'agent-a', claimed_role: null }]),
  engine_section_json: JSON.stringify({
    rule_verdicts: [
      {
        rule_id: 'R-BOUNDARY-1',
        description: 'Boundary crossing',
        matched_resource: '/tmp/x.sh',
        llm_agreed: null,
        llm_comment: null,
      },
    ],
    pattern_verdicts: [],
    classification_score: 0.9,
    classification_route: 'engine_unsafe',
    score_breakdown: {
      rule_score: 1,
      pattern_score: 0,
      boundary_score: 0.9,
      evidence_score: 0.8,
      role_score: 0,
      repetition_score: 0,
      total: 0.9,
    },
  }),
  timeline_json: JSON.stringify({
    steps: [
      {
        timestamp_ns: over.ts - 500,
        event_type: 'EXEC',
        description: 'exec /bin/bash',
        resource: '/bin/bash',
        evidence_ref: null,
        agent_id: 'agent-a',
        operation: null,
        evidence_level: null,
        triggering_rule_ids: [],
        triggering_pattern_id: null,
        source_event_id: null,
      },
      {
        timestamp_ns: over.ts,
        event_type: 'FILE_WRITE',
        description: 'write /tmp/x.sh',
        resource: '/tmp/x.sh',
        evidence_ref: null,
        agent_id: 'agent-a',
        operation: 'write',
        evidence_level: null,
        triggering_rule_ids: ['R-BOUNDARY-1'],
        triggering_pattern_id: null,
        source_event_id: null,
      },
    ],
    total_duration_ms: 1,
  }),
  graph_metrics_json: JSON.stringify({
    snapshot_id: 'snap-latest',
    timestamp_ns: over.ts,
    nodes: nodesLatest,
    edges: edgesLatest,
    metrics,
  }),
  phase: over.phase,
  provisional_ref: over.provisional_ref ?? null,
  run_id: 'run-1',
});

const evidence = (id, intentId, actionId, matchLevel, conf, fields, phase, ts) => ({
  evidence_id: id,
  intent_id: intentId,
  action_id: actionId,
  match_level: matchLevel,
  confidence: conf,
  matched_fields_json: JSON.stringify(fields),
  derivation_mode: 'Direct',
  phase,
  timestamp_ns: ts,
  run_id: 'run-1',
});

async function main() {
  const SQL = await initSqlJs();
  const db = new SQL.Database();
  db.run(`
    CREATE TABLE graph_snapshots (
      snapshot_id TEXT, timestamp_ns INTEGER,
      nodes_json TEXT, edges_json TEXT, metrics_json TEXT, run_id TEXT
    );
    CREATE TABLE responsibility_alerts (
      alert_id TEXT, timestamp_ns INTEGER, display_profile TEXT, decision TEXT,
      severity TEXT, confidence REAL, authority TEXT, reasoning TEXT, title TEXT,
      summary TEXT, action_hint TEXT, primary_agent_id TEXT,
      involved_agents_json TEXT, engine_section_json TEXT, timeline_json TEXT,
      graph_metrics_json TEXT, phase TEXT, provisional_ref TEXT, run_id TEXT
    );
    CREATE TABLE correlation_evidence (
      evidence_id TEXT, intent_id TEXT, action_id TEXT, match_level TEXT,
      confidence REAL, matched_fields_json TEXT, derivation_mode TEXT,
      phase TEXT, timestamp_ns INTEGER, run_id TEXT
    );
    CREATE TABLE responsibility_tracebacks (
      traceback_id TEXT, claim_id TEXT, chain_node_ids_json TEXT,
      chain_edge_ids_json TEXT, root_cause_node_id TEXT,
      explanation_summary TEXT, confidence REAL, run_id TEXT
    );
    CREATE TABLE responsibility_claims (
      claim_id TEXT, claim_type TEXT, subject_agent_id TEXT, policy_id TEXT,
      boundary_id TEXT, claim_state TEXT, severity TEXT, confidence REAL,
      evidence_refs_json TEXT, competing_explanations_json TEXT,
      traceback_ref TEXT, phase TEXT, run_id TEXT
    );
    CREATE TABLE intents (
      intent_id TEXT, pid INTEGER, process_start_time_ns INTEGER, message_id TEXT,
      tool_name TEXT, tool_args_json TEXT, intent_class TEXT, parse_state TEXT,
      primary_matching_eligible INTEGER, authority_source TEXT,
      timestamp_ns INTEGER, run_id TEXT
    );
    CREATE TABLE actions (
      action_id TEXT, pid INTEGER, process_start_time_ns INTEGER, action_type TEXT,
      normalized_args_json TEXT, derivation_mode TEXT, signal_class TEXT,
      timestamp_ns INTEGER, run_id TEXT
    );
    CREATE TABLE runs (
      run_id TEXT, started_at_ns INTEGER, ended_at_ns INTEGER,
      scenario_id TEXT, mode TEXT, collector_version TEXT
    );
    CREATE TABLE non_link_diagnostics (
      diagnostic_id TEXT, intent_id TEXT, action_id TEXT, reason TEXT,
      timestamp_ns INTEGER, run_id TEXT
    );
    CREATE TABLE context_pollution_events (
      pollution_id INTEGER, source_agent_id TEXT, contaminated_artifact TEXT,
      propagation_path_json TEXT, detected_at_ns INTEGER, run_id TEXT
    );
    CREATE TABLE policy_evaluations (
      policy_id TEXT, boundary TEXT, judgment_json TEXT, subject_agent_id TEXT,
      resource_json TEXT, evidence_refs_json TEXT, confidence REAL, reason TEXT, run_id TEXT
    );
    CREATE TABLE tls_sessions (
      tls_session_id TEXT, pid INTEGER, start_time_ns INTEGER, timestamp_ns INTEGER, run_id TEXT
    );
    CREATE TABLE process_events (
      process_event_id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER, start_time_ns INTEGER,
      parent_pid INTEGER, parent_start_time_ns INTEGER, comm TEXT, timestamp_ns INTEGER, run_id TEXT
    );
    CREATE TABLE provisional_identities (
      agent_id TEXT PRIMARY KEY, process_ids_json TEXT, primary_signal TEXT,
      confidence REAL, run_id TEXT
    );
    CREATE TABLE agent_identities (
      agent_id TEXT PRIMARY KEY, process_ids_json TEXT, message_ids_json TEXT,
      tls_sessions_json TEXT, confidence TEXT, provisional_refs_json TEXT, run_id TEXT
    );
    CREATE TABLE identity_process_map (
      agent_id TEXT, pid INTEGER, start_time_ns INTEGER, run_id TEXT, PRIMARY KEY(pid, start_time_ns)
    );
    CREATE TABLE subagent_boundaries (
      boundary_id TEXT PRIMARY KEY, parent_agent_id TEXT, child_agent_id TEXT,
      parent_pid INTEGER, parent_start_time_ns INTEGER, child_pid INTEGER,
      child_start_time_ns INTEGER, reason TEXT, run_id TEXT
    );
    CREATE TABLE delegation_records (
      delegation_id TEXT PRIMARY KEY, from_agent_id TEXT, to_agent_id TEXT,
      parent_pid INTEGER, parent_start_time_ns INTEGER, child_pid INTEGER,
      child_start_time_ns INTEGER, timestamp_ns INTEGER, confidence REAL, run_id TEXT
    );
  `);

  // Run metadata — a single row with run_id populated, rest NULL.
  db.run('INSERT INTO runs VALUES (?,?,?,?,?,?)', ['run-1', null, null, null, null, null]);

  // Four snapshots; the loader must pick the latest.
  db.run('INSERT INTO graph_snapshots VALUES (?,?,?,?,?,?)', [
    'snap-zero',
    800,
    JSON.stringify(nodesOld),
    JSON.stringify(edgesOld),
    JSON.stringify(metricsZero),
    'run-1',
  ]);
  db.run('INSERT INTO graph_snapshots VALUES (?,?,?,?,?,?)', [
    'snap-mid',
    850,
    JSON.stringify(nodesOld),
    JSON.stringify(edgesOld),
    JSON.stringify(metricsMid),
    'run-1',
  ]);
  db.run('INSERT INTO graph_snapshots VALUES (?,?,?,?,?,?)', [
    'snap-old',
    900,
    JSON.stringify(nodesOld),
    JSON.stringify(edgesOld),
    null,
    'run-1',
  ]);
  db.run('INSERT INTO graph_snapshots VALUES (?,?,?,?,?,?)', [
    'snap-latest',
    1100,
    JSON.stringify(nodesLatest),
    JSON.stringify(edgesLatest),
    JSON.stringify(metrics),
    'run-1',
  ]);

  db.run('INSERT INTO responsibility_alerts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [
    'alert-1',
    1100,
    'UnsafeByEngine',
    'Unsafe',
    'Warning',
    0.85,
    'Authoritative',
    'reasoning-1',
    'Suspicious shell resource use',
    'summary-1',
    null,
    'agent-a',
    '[{"agent_id":"agent-a","claimed_role":null}]',
    JSON.stringify({
      rule_verdicts: [],
      pattern_verdicts: [],
      classification_score: 0.9,
      classification_route: 'engine_unsafe',
      score_breakdown: {
        rule_score: 1,
        pattern_score: 0,
        boundary_score: 0.9,
        evidence_score: 0.8,
        role_score: 0,
        repetition_score: 0,
        total: 0.9,
      },
    }),
    JSON.stringify({ steps: [], total_duration_ms: 0 }),
    JSON.stringify({
      snapshot_id: 'snap-latest',
      timestamp_ns: 1100,
      nodes: nodesLatest,
      edges: edgesLatest,
      metrics,
    }),
    'Provisional { timestamp_ns: 1100 }',
    null,
    'run-1',
  ]);
  const authAlert = alert({
    alert_id: 'alert-2',
    ts: 1200,
    decision: 'Safe',
    severity: 'Info',
    display_profile: 'ObserverJudged',
    title: 'Benign pipeline',
    phase: 'Authoritative { timestamp_ns: 1200, overrides: null }',
    provisional_ref: 'alert-1',
  });
  db.run('INSERT INTO responsibility_alerts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [
    authAlert.alert_id,
    authAlert.timestamp_ns,
    authAlert.display_profile,
    authAlert.decision,
    authAlert.severity,
    authAlert.confidence,
    authAlert.authority,
    authAlert.reasoning,
    authAlert.title,
    authAlert.summary,
    authAlert.action_hint,
    authAlert.primary_agent_id,
    authAlert.involved_agents_json,
    authAlert.engine_section_json,
    authAlert.timeline_json,
    authAlert.graph_metrics_json,
    authAlert.phase,
    authAlert.provisional_ref,
    authAlert.run_id,
  ]);

  const fieldsFull = {
    canonical_path: '/tmp/x.sh',
    host: null,
    port: null,
    command: 'bash /tmp/x.sh',
    raw_args: ['/tmp/x.sh'],
  };
  for (const ev of [
    evidence(
      'ev-1',
      'int-1',
      'act-1',
      'L1Exact',
      0.95,
      fieldsFull,
      'Provisional { timestamp_ns: 1000 }',
      1000,
    ),
    evidence(
      'ev-2',
      'int-2',
      'act-1',
      'L3Containment',
      0.65,
      { canonical_path: null, host: null, port: null, command: null, raw_args: [] },
      'Provisional { timestamp_ns: 1050 }',
      1050,
    ),
    evidence(
      'ev-3',
      'int-1',
      'act-2',
      'L5Substring',
      0.5,
      { canonical_path: null, host: null, port: null, command: null, raw_args: ['x'] },
      'Authoritative { timestamp_ns: 1200, overrides: null }',
      1200,
    ),
  ]) {
    db.run('INSERT INTO correlation_evidence VALUES (?,?,?,?,?,?,?,?,?,?)', [
      ev.evidence_id,
      ev.intent_id,
      ev.action_id,
      ev.match_level,
      ev.confidence,
      ev.matched_fields_json,
      ev.derivation_mode,
      ev.phase,
      ev.timestamp_ns,
      ev.run_id,
    ]);
  }

  db.run('INSERT INTO responsibility_tracebacks VALUES (?,?,?,?,?,?,?,?)', [
    'tb-1',
    'claim-1',
    '["agent-a"]',
    '["e1"]',
    'agent-a',
    'traceback summary',
    0.9,
    'run-1',
  ]);

  // claims — real schema, traceback_ref is NULL (matches real db: all 22 rows NULL)
  db.run('INSERT INTO responsibility_claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', [
    'claim-1',
    'TaintedContextUse',
    'agent-a',
    'POLICY-1',
    'BOUNDARY-1',
    'Inferred',
    'Critical',
    0.9,
    JSON.stringify(['ev-1', 'ev-2']),
    JSON.stringify([
      {
        label: 'Alternative path',
        evidence_refs: ['ev-3'],
        rule_id: 'R-ALT',
        rationale: 'Context could flow via the shell',
        aligned_pattern: null,
      },
    ]),
    null, // traceback_ref — real db all NULL, chain via tracebacksByClaimId reverse index
    'Provisional { timestamp_ns: 1000 }',
    'run-1',
  ]);
  db.run('INSERT INTO responsibility_claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', [
    'claim-2',
    'PolicyViolation',
    'agent-a',
    'POLICY-1',
    null,
    'Confirmed',
    'Warning',
    0.72,
    JSON.stringify(['ev-3']),
    '[]',
    null,
    'Authoritative { timestamp_ns: 1200, overrides: null }',
    'run-1',
  ]);

  // intents — tool_args_json real shape Array<{Command|Path}> (not NormalizedArgs object)
  const intents = [
    [
      'int-1',
      42,
      1000,
      'msg-1',
      'Bash',
      JSON.stringify([{ Command: 'ls -la /tmp' }]),
      'FileSystem',
      'Eligible',
      1,
      'SseResponseToolUse',
      1000,
    ],
    [
      'int-2',
      42,
      1000,
      'msg-2',
      'Read',
      JSON.stringify([{ Path: '/tmp/x.sh' }]),
      'FileSystem',
      'Eligible',
      1,
      'TextFallback',
      1050,
    ],
    // orphan intent — no correlation_evidence link (real data has 1), shown greyed out
    [
      'int-3',
      7,
      2000,
      'msg-3',
      'Glob',
      JSON.stringify([{ Path: '/tmp/*.sh' }]),
      'FileSystem',
      'Ineligible',
      0,
      'TextFallback',
      1100,
    ],
  ];
  for (const it of intents) {
    db.run('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', [...it, 'run-1']);
  }

  // actions — normalized_args_json real shape Array<{Path|Command}>
  const actions = [
    [
      'act-1',
      42,
      1000,
      'FileWrite',
      JSON.stringify([{ Path: '/tmp/x.sh' }]),
      'Direct',
      'HighSignal',
      1000,
    ],
    [
      'act-2',
      42,
      1000,
      'FileRead',
      JSON.stringify([{ Path: '/tmp/x.sh' }]),
      'Direct',
      'LowSignal',
      1002,
    ],
    // orphan action — no evidence link (real data has 300), shown greyed out
    [
      'act-3',
      7,
      2000,
      'ProcessExec',
      JSON.stringify([{ Path: '/bin/bash', Command: 'bash' }]),
      'Derived',
      'HighSignal',
      1100,
    ],
  ];
  for (const ac of actions) {
    db.run('INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?)', [...ac, 'run-1']);
  }

  // ── 监控表（Task 1 新增）──
  // non_link_diagnostics：3 行，reason 分布 BelowConfidenceCutoff×2 + NoArgumentMatch×1
  for (const row of [
    ['nld-1', 'int-1', 'BelowConfidenceCutoff'],
    ['nld-2', 'int-2', 'BelowConfidenceCutoff'],
    ['nld-3', 'int-3', 'NoArgumentMatch'],
  ]) {
    db.run('INSERT INTO non_link_diagnostics VALUES (?,?,?,?,?,?)', [
      row[0],
      row[1],
      null,
      row[2],
      1000,
      'run-1',
    ]);
  }
  // 追加第 4 行（AmbiguousMatch，关联 int-2）
  db.run('INSERT INTO non_link_diagnostics VALUES (?,?,?,?,?,?)', [
    'nld-4',
    'int-2',
    null,
    'AmbiguousMatch',
    1100,
    'run-1',
  ]);
  // context_pollution_events：3 行，第 3 行 propagation_path 覆盖 ≥2 hop
  for (const [pid, source, artifact, path, ts] of [
    [1, 'agent-a', '/tmp/leaked.txt', [], 1000],
    [2, 'agent-b', '/tmp/context.json', [], 1100],
    [3, 'agent-a', '/tmp/script.sh', ['agent-a', 'agent-b', 'agent-c'], 1300],
  ]) {
    db.run('INSERT INTO context_pollution_events VALUES (?,?,?,?,?,?)', [
      pid,
      source,
      artifact,
      JSON.stringify(path),
      ts,
      'run-1',
    ]);
  }
  // policy_evaluations：3 行，其中 1 条 judgment_json.result=Violation
  const judgment = (result) =>
    JSON.stringify({ judgment_id: 'j-' + result, policy_id: 'pol-' + result, result });
  for (const [policyId, boundary, subject, result] of [
    ['pol-1', 'Capability', 'agent-a', 'Violation'],
    ['pol-2', 'Capability', 'agent-a', 'Compliant'],
    ['pol-3', 'Capability', 'agent-b', 'Compliant'],
    ['pol-4', 'Capability', 'agent-b', 'Compliant'],
    ['pol-5', 'DataFlow', 'agent-a', 'Allow'],
    ['pol-6', 'DataFlow', 'agent-c', 'Compliant'],
  ]) {
    db.run('INSERT INTO policy_evaluations VALUES (?,?,?,?,?,?,?,?,?)', [
      policyId,
      boundary,
      judgment(result),
      subject,
      null,
      '[]',
      0.85,
      '',
      'run-1',
    ]);
  }
  // tls_sessions：6 行跨 2 个时间桶（桶宽 60s = 60_000_000_000 ns），
  // s-5/s-6 用未映射 pid 44，trend 桶为 [3,3]
  for (const [sid, pid, st, ts] of [
    ['s-1', 42, 1000, 1000],
    ['s-2', 42, 1000, 5000],
    ['s-3', 43, 1000, 61_000_000_000],
    ['s-4', 43, 1000, 61_005_000_000],
    ['s-5', 44, 1000, 1200],
    ['s-6', 44, 1000, 61_010_000_000],
  ]) {
    db.run('INSERT INTO tls_sessions VALUES (?,?,?,?,?)', [sid, pid, st, ts, 'run-1']);
  }

  // ── M0 新增：进程/身份三表（2 组父子链 + 1 未识别孤儿 + 1 orphanPid）──
  // process_events：树 A agent-a（42→102→103），树 B agent-b（43→202→203），未识别 300
  for (const [peid, pid, st, pp, pst, comm, ts] of [
    [1, 42, 1000, null, null, 'claude', 1000],
    [2, 102, 1000, 42, 1000, 'bash', 1001],
    [3, 103, 1000, 102, 1000, 'sh', 1002],
    [4, 43, 1000, null, null, 'claude', 1000],
    [5, 202, 1000, 43, 1000, 'node', 2000],
    [6, 203, 1000, 202, 1000, 'script', 2001],
    [7, 300, 4000, null, null, 'orphan-tool', 4000],
  ]) {
    db.run(
      'INSERT INTO process_events (process_event_id, pid, start_time_ns, parent_pid, parent_start_time_ns, comm, timestamp_ns, run_id) VALUES (?,?,?,?,?,?,?,?)',
      [peid, pid, st, pp, pst, comm, ts, 'run-1'],
    );
  }
  // identity_process_map：agent-a 含 orphan pid 99（无 process_events 记录）
  for (const [agent, pid, st] of [
    ['agent-a', 42, 1000],
    ['agent-a', 102, 1000],
    ['agent-a', 103, 1000],
    ['agent-a', 99, 900],
    ['agent-b', 43, 1000],
    ['agent-b', 202, 1000],
    ['agent-b', 203, 1000],
  ]) {
    db.run('INSERT INTO identity_process_map VALUES (?,?,?,?)', [agent, pid, st, 'run-1']);
  }
  // provisional_identities：3 行；agent-a 与 agent_identities 交集（升级），agent-c 仅 provisional
  const procIds = (pids) => JSON.stringify(pids.map((pid) => ({ pid, start_time_ns: 1000 })));
  for (const [agent, pids, signal, conf] of [
    ['agent-a', [42, 102, 103], 'TlsSessionNew', 0.92],
    ['agent-b', [43, 202, 203], 'ProcessEvent', 0.85],
    ['agent-c', [400], 'TlsSessionNew', 0.6],
  ]) {
    db.run('INSERT INTO provisional_identities VALUES (?,?,?,?,?)', [
      agent,
      procIds(pids),
      signal,
      conf,
      'run-1',
    ]);
  }
  // agent_identities：2 行；agent-a 已升级（交集），agent-d 仅正式表
  for (const [agent, pids, msgs, sessions, conf, prov] of [
    ['agent-a', [42, 102, 103], ['msg-1'], ['s-1'], 'Authoritative', ['agent-a']],
    ['agent-d', [500], ['msg-9'], ['s-9'], 'Authoritative', ['agent-d']],
  ]) {
    db.run('INSERT INTO agent_identities VALUES (?,?,?,?,?,?,?)', [
      agent,
      procIds(pids),
      JSON.stringify(msgs),
      JSON.stringify(sessions),
      conf,
      JSON.stringify(prov),
      'run-1',
    ]);
  }
  // subagent_boundaries：2 行（委派树 agent-a→agent-b→agent-c）
  for (const [bid, parent, child, pp, pst, cp, cst, reason] of [
    ['bnd-1', 'agent-a', 'agent-b', 42, 1000, 43, 1000, 'delegation'],
    ['bnd-2', 'agent-b', 'agent-c', 43, 1000, 400, 5000, 'subagent spawn'],
  ]) {
    db.run('INSERT INTO subagent_boundaries VALUES (?,?,?,?,?,?,?,?,?)', [
      bid,
      parent,
      child,
      pp,
      pst,
      cp,
      cst,
      reason,
      'run-1',
    ]);
  }
  // delegation_records：2 行（带 timestamp_ns 供诞生时序）
  for (const [did, from, to, pp, pst, cp, cst, ts, conf] of [
    ['del-1', 'agent-a', 'agent-b', 42, 1000, 43, 1000, 1100, 0.85],
    ['del-2', 'agent-b', 'agent-c', 43, 1000, 400, 5000, 2100, 0.9],
  ]) {
    db.run('INSERT INTO delegation_records VALUES (?,?,?,?,?,?,?,?,?,?)', [
      did,
      from,
      to,
      pp,
      pst,
      cp,
      cst,
      ts,
      conf,
      'run-1',
    ]);
  }

  const bytes = db.export();
  mkdirSync(join(root, '__tests__', 'fixtures'), { recursive: true });
  writeFileSync(join(root, '__tests__', 'fixtures', 'db-sample.sqlite'), Buffer.from(bytes));
  console.log('fixture written:', bytes.length, 'bytes');
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
