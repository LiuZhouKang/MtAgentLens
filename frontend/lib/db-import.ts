import type { Database, SqlJsStatic } from 'sql.js';
import type {
  Action,
  AgentIdentity,
  AlertSeverity,
  BehaviorTimeline,
  ClaimState,
  ClaimType,
  ContextPollutionEvent,
  CorrelationEvidence,
  DatasetOverview,
  EngineSection,
  GraphData,
  GraphMetrics,
  GraphSnapshot,
  GraphSnapshotFrame,
  IdentityProcessMapEntry,
  Intent,
  NonLinkDiagnostic,
  NormalizedArgs,
  PhaseIndicator,
  PolicyEvaluation,
  ProcessEvent,
  ProvisionalIdentity,
  ResponsibilityAlert,
  ResponsibilityClaim,
  ResponsibilityTraceback,
  SubagentBoundary,
  TlsSession,
} from './types';
import type {
  MismatchExtract,
  PollutionExtract,
  PolicyExtract,
  SnapshotMetricsExtract,
  SnapshotMetricFrame,
  TlsExtract,
} from './monitoring/monitoring-types';
import { deriveGraphMetrics } from './graph-metrics';
import { deriveMonitoringState } from './monitoring/derive-monitoring';
import type { MonitoringState } from './monitoring/monitoring-types';

/**
 * SQLite (`.db`) import — the counterpart to `snapshot-import.ts` for the
 * collector's real artifact layout. The frontend's actual data lives in
 * `collector/collector.responsibility.db`, so instead of relying on the API
 * (which historically exposed a different, leaner contract) we load the db
 * directly in the browser with sql.js and extract exactly the tables the UI
 * consumes: graph_snapshots, responsibility_alerts, correlation_evidence.
 *
 * The SQL mirrors `scripts/data-server.py` (the reference contract the
 * frontend was built against) but reads the real file.
 */

export class DatabaseImportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'DatabaseImportError';
  }
}

export type DbImport = {
  /** Stable identity used as the data source key, e.g. "db:run-42.db". */
  id: string;
  label: string;
  graph: GraphData;
  alerts: ResponsibilityAlert[];
  evidence: CorrelationEvidence[];
  claims: ResponsibilityClaim[];
  tracebacks: ResponsibilityTraceback[];
  intents: Intent[];
  actions: Action[];
  overview: DatasetOverview;
  /** 运行监控派生结果（Task 3 deriveMonitoringState 产出）。 */
  monitoring: MonitoringState;
  metrics: GraphMetrics;
  nodeCount: number;
  edgeCount: number;
  alertCount: number;
  evidenceCount: number;
  tracebackCount: number;
  claimCount: number;
  intentCount: number;
  actionCount: number;
  runId: string | null;
  /** M0 全行深链资产（process/tls 已 cap）。 */
  dbAssets: DbAssets;
  /** True when any of the six consumable tables yielded rows. */
  hasConsumableData: boolean;
};

/** 带 cap 的全行提取产物——total 为真实行数，truncated 标记是否被 cap 截断。 */
export type CappedExtract<T> = { items: T[]; total: number; truncated: boolean };

/** M0 全行深链资产——M1–M4 四功能的原始数据源（均由纯函数派生，无造数）。 */
export interface DbAssets {
  pollutionEvents: ContextPollutionEvent[];
  policyEvaluations: PolicyEvaluation[];
  nonLinkDiagnostics: NonLinkDiagnostic[];
  agentIdentities: AgentIdentity[];
  provisionalIdentities: ProvisionalIdentity[];
  identityProcessMap: IdentityProcessMapEntry[];
  processEvents: ProcessEvent[];           // 已 cap
  processEventCount: number;               // totalCount
  processEventTruncated: boolean;
  tlsSessions: TlsSession[];               // 已 cap
  tlsSessionCount: number;
  tlsSessionTruncated: boolean;
  subagentBoundaries: SubagentBoundary[];
  snapshotFrames: GraphSnapshotFrame[];    // 去重 + 降采样
}

const EMPTY_METRICS: GraphMetrics = {
  node_counts: {},
  edge_counts: {},
  agent_count: 0,
  cross_agent_edges: 0,
  delegation_depth: 0,
  taint_chains: 0,
  avg_confidence: 0,
};

let sqlPromise: Promise<SqlJsStatic> | null = null;

/**
 * sql.js ships only a UMD/CJS build whose emscripten glue statically requires
 * `fs`/`path`/`crypto` inside a Node-only branch (dead in the browser). Bundlers
 * still resolve that require at build time, so we never give them a statically
 * analyzable import of sql.js:
 *
 *  - Node (vitest / jsdom, Next SSR): a runtime `import()` whose specifier is
 *    computed through a call — bundlers can't fold it, so it stays a dynamic
 *    import resolved from node_modules. This branch never runs in the browser.
 *  - Browser: the UMD build is served as a plain asset (`/sql-wasm.js`) and
 *    loaded via `<script>`, where its top-level `var initSqlJs` becomes
 *    `window.initSqlJs`; the wasm is located next to it (`/sql-wasm.wasm`).
 */
function isNode(): boolean {
  return (
    typeof process !== 'undefined' &&
    typeof process.versions === 'object' &&
    typeof (process.versions as { node?: string }).node === 'string'
  );
}

type InitSqlJs = (config?: { locateFile?: (file: string) => string }) => Promise<SqlJsStatic>;

/**
 * sql.js ships only a UMD/CJS build whose emscripten glue statically requires
 * `fs`/`path`/`crypto` inside a Node-only branch (dead in the browser). Bundlers
 * still resolve that require at build time, so the browser bundle never contains
 * an import of sql.js — the engine always arrives via `globalThis.initSqlJs`:
 *
 *  - Browser: the UMD build is a public asset (`/sql-wasm.js`); loaded as a
 *    classic `<script>`, its top-level `var initSqlJs` becomes
 *    `window.initSqlJs`. The wasm is located next to it (`/sql-wasm.wasm`).
 *  - Node (vitest / jsdom, Next SSR): `test/setup.ts` injects the real module
 *    into `globalThis.initSqlJs`. That setup file runs only under vitest, so
 *    Turbopack/Webpack never analyse it.
 */
let scriptPromise: Promise<void> | null = null;

async function injectSqlScript(): Promise<void> {
  const doc = globalThis.document;
  const g = globalThis as unknown as { initSqlJs?: InitSqlJs };
  if (g.initSqlJs || !doc || doc.getElementById('mtagentlens-sql-js')) return;
  if (!scriptPromise) {
    scriptPromise = new Promise<void>((resolve, reject) => {
      const script = doc.createElement('script');
      script.id = 'mtagentlens-sql-js';
      script.src = '/sql-wasm.js';
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => {
        scriptPromise = null; // Allow a retry on the next import attempt.
        reject(new DatabaseImportError('无法加载 SQLite 引擎（sql-wasm.js）'));
      };
      doc.head?.appendChild(script);
    });
  }
  await scriptPromise;
}

function getSql(): Promise<SqlJsStatic> {
  if (!sqlPromise) {
    sqlPromise = (async () => {
      const g = globalThis as unknown as { initSqlJs?: InitSqlJs };
      if (!g.initSqlJs) await injectSqlScript();
      if (!g.initSqlJs) throw new DatabaseImportError('SQLite 引擎加载失败');
      // In Node the wasm ships next to the module (read via fs); in the
      // browser it is served from /sql-wasm.wasm.
      return g.initSqlJs(isNode() ? undefined : { locateFile: () => '/sql-wasm.wasm' });
    })();
  }
  return sqlPromise;
}

export async function openDatabase(bytes: Uint8Array): Promise<Database> {
  const SQL = await getSql();
  return new SQL.Database(bytes);
}

/** SQLite files begin with the 16-byte header "SQLite format 3\0". */
const SQLITE_HEADER = 'SQLite format 3\x00';

function isSqlite(bytes: Uint8Array): boolean {
  if (bytes.length < 16) return false;
  for (let i = 0; i < 16; i += 1) {
    if (bytes[i] !== SQLITE_HEADER.charCodeAt(i)) return false;
  }
  return true;
}

function queryRows(db: Database, sql: string, params?: unknown[]): Record<string, unknown>[] {
  const stmt = db.prepare(sql);
  if (params) stmt.bind(params as never);
  const rows: Record<string, unknown>[] = [];
  try {
    while (stmt.step()) {
      rows.push(stmt.getAsObject());
    }
  } finally {
    stmt.free();
  }
  return rows;
}

/** Parse the outer-tagged Rust Debug phase string, e.g. "Provisional { timestamp_ns: 123 }". */
export function parsePhaseIndicator(raw: unknown): PhaseIndicator | null {
  if (typeof raw !== 'string') return null;
  const text = raw.trim();
  const provisional = /^Provisional\s*\{\s*timestamp_ns:\s*(\d+)\s*\}$/.exec(text);
  if (provisional) return { Provisional: { timestamp_ns: Number(provisional[1]) } };

  const authoritative =
    /^Authoritative\s*\{\s*timestamp_ns:\s*(\d+)\s*(?:,\s*overrides:\s*(.+))?\s*\}$/.exec(text);
  if (authoritative) {
    const overridesRaw = authoritative[2]?.trim();
    return {
      Authoritative: {
        timestamp_ns: Number(authoritative[1]),
        overrides:
          overridesRaw && overridesRaw !== 'null' ? parsePhaseIndicator(overridesRaw) : null,
      },
    };
  }
  return null;
}

/** Parse the correlation_evidence.matched_fields_json column. */
export function parseMatchedFields(raw: unknown): NormalizedArgs {
  if (typeof raw !== 'string') {
    return { canonical_path: null, host: null, port: null, command: null, raw_args: [] };
  }
  try {
    const parsed = JSON.parse(raw) as Partial<NormalizedArgs> | null;
    if (parsed && typeof parsed === 'object') {
      return {
        canonical_path: parsed.canonical_path ?? null,
        host: parsed.host ?? null,
        port: parsed.port ?? null,
        command: parsed.command ?? null,
        raw_args: Array.isArray(parsed.raw_args) ? parsed.raw_args : [],
      };
    }
  } catch {
    // Malformed JSON → fall through to the empty shape.
  }
  return { canonical_path: null, host: null, port: null, command: null, raw_args: [] };
}

function parseJsonObject<T extends object>(raw: unknown): T | null {
  if (typeof raw !== 'string' || !raw) return null;
  try {
    const value = JSON.parse(raw);
    return value && typeof value === 'object' ? (value as T) : null;
  } catch {
    return null;
  }
}

function parseJsonArray<T>(raw: unknown): T[] {
  const value = parseJsonObject<unknown[]>(raw);
  return Array.isArray(value) ? (value as T[]) : [];
}

/** 按 NormalizedArgs 形态解析 policy.resource_json；非该形态 → null（不幻觉）。 */
export function parseNormalizedArgsLike(raw: unknown): NormalizedArgs | null {
  const obj = parseJsonObject<Record<string, unknown>>(raw);
  if (!obj || typeof obj !== 'object') return null;
  const hasAnyKey = ['canonical_path', 'host', 'port', 'command', 'raw_args'].some((k) => k in obj);
  if (!hasAnyKey) return null;
  if ('raw_args' in obj && !Array.isArray(obj.raw_args)) return null;
  return {
    canonical_path: typeof obj.canonical_path === 'string' ? obj.canonical_path : null,
    host: typeof obj.host === 'string' ? obj.host : null,
    port: typeof obj.port === 'number' ? obj.port : null,
    command: typeof obj.command === 'string' ? obj.command : null,
    raw_args: Array.isArray(obj.raw_args) ? (obj.raw_args as string[]) : [],
  };
}

/** 均匀降采样：超过 maxFrames 时等距取帧（含首尾）。首尾恒保留。 */
export function pickRepresentativeFrames<T>(frames: T[], maxFrames: number): T[] {
  if (maxFrames <= 0 || frames.length <= maxFrames) return frames;
  const step = (frames.length - 1) / (maxFrames - 1);
  const keep = new Set<number>();
  for (let i = 0; i < maxFrames; i += 1) keep.add(Math.round(i * step));
  return frames.filter((_, i) => keep.has(i));
}

function phaseTimestampNs(phase: PhaseIndicator): number {
  if ('Provisional' in phase) return phase.Provisional.timestamp_ns;
  return phase.Authoritative.timestamp_ns;
}

/** Load responsibility_claims — timestamp_ns derived from the phase column. */
export function extractClaimsFromDb(db: Database): ResponsibilityClaim[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      `SELECT claim_id, claim_type, subject_agent_id, policy_id, boundary_id,
              claim_state, severity, confidence, evidence_refs_json,
              competing_explanations_json, traceback_ref, phase
       FROM responsibility_claims`,
    );
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => {
    const phase = parsePhaseIndicator(r.phase) ?? {
      Provisional: { timestamp_ns: 0 },
    };
    return {
      claim_id: String(r.claim_id ?? ''),
      claim_type: (r.claim_type as ClaimType) ?? 'TaintedContextUse',
      subject_agent_id: r.subject_agent_id ? String(r.subject_agent_id) : null,
      policy_id: r.policy_id ? String(r.policy_id) : null,
      boundary_id: r.boundary_id ? String(r.boundary_id) : null,
      claim_state: (r.claim_state as ClaimState) ?? 'Inferred',
      severity: (r.severity as AlertSeverity) ?? 'Warning',
      confidence: Number(r.confidence ?? 0),
      evidence_refs: parseJsonArray<string>(r.evidence_refs_json),
      competing_explanations:
        parseJsonArray<ResponsibilityClaim['competing_explanations'][number]>(
          r.competing_explanations_json,
        ),
      traceback_ref: r.traceback_ref ? String(r.traceback_ref) : null,
      phase,
      timestamp_ns: phaseTimestampNs(phase),
    };
  });
}

/** Load responsibility_tracebacks — chain ids parsed as string arrays. */
export function extractTracebacksFromDb(db: Database): ResponsibilityTraceback[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      `SELECT traceback_id, claim_id, chain_node_ids_json, chain_edge_ids_json,
              root_cause_node_id, explanation_summary, confidence
       FROM responsibility_tracebacks`,
    );
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    traceback_id: String(r.traceback_id ?? ''),
    claim_id: String(r.claim_id ?? ''),
    chain_node_ids: parseJsonArray<string>(r.chain_node_ids_json),
    chain_edge_ids: parseJsonArray<string>(r.chain_edge_ids_json),
    root_cause_node_id: String(r.root_cause_node_id ?? ''),
    explanation_summary: String(r.explanation_summary ?? ''),
    confidence: Number(r.confidence ?? 0),
  }));
}

/** Load intents — pid/start_time_ns nested into process_id; args stay as raw JSON arrays. */
export function extractIntentsFromDb(db: Database): Intent[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      `SELECT intent_id, pid, process_start_time_ns, message_id, tool_name,
              tool_args_json, intent_class, parse_state, primary_matching_eligible,
              authority_source, timestamp_ns
       FROM intents`,
    );
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    intent_id: String(r.intent_id ?? ''),
    process_id: {
      pid: Number(r.pid ?? 0),
      start_time_ns: Number(r.process_start_time_ns ?? 0),
    },
    message_id: String(r.message_id ?? ''),
    tool_name: String(r.tool_name ?? ''),
    tool_args: parseJsonArray(r.tool_args_json),
    intent_class: String(r.intent_class ?? ''),
    parse_state: String(r.parse_state ?? ''),
    primary_matching_eligible: Number(r.primary_matching_eligible ?? 0) !== 0,
    authority_source: String(r.authority_source ?? ''),
    timestamp_ns: Number(r.timestamp_ns ?? 0),
  }));
}

/** Load actions — args stay as raw JSON arrays (Array<{Path|Command}> in real dbs). */
export function extractActionsFromDb(db: Database): Action[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      `SELECT action_id, pid, process_start_time_ns, action_type,
              normalized_args_json, derivation_mode, signal_class, timestamp_ns
       FROM actions`,
    );
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    action_id: String(r.action_id ?? ''),
    process_id: {
      pid: Number(r.pid ?? 0),
      start_time_ns: Number(r.process_start_time_ns ?? 0),
    },
    action_type: String(r.action_type ?? ''),
    normalized_args: parseJsonArray(r.normalized_args_json),
    derivation_mode: String(r.derivation_mode ?? ''),
    signal_class: String(r.signal_class ?? ''),
    timestamp_ns: Number(r.timestamp_ns ?? 0),
  }));
}

/** Load a per-table DatasetOverview — counts + GROUP BY distributions. Every
 *  table query is defensive: a missing table counts as zero (small/older dbs). */
export function extractDatasetOverviewFromDb(db: Database): DatasetOverview {
  const count = (table: string): number => {
    try {
      const rows = queryRows(db, `SELECT COUNT(*) AS n FROM ${table}`);
      return Number(rows[0]?.n ?? 0);
    } catch {
      return 0; // Table absent.
    }
  };
  const groupCount = (table: string, column: string): Record<string, number> => {
    const result: Record<string, number> = {};
    try {
      const rows = queryRows(
        db,
        `SELECT ${column} AS k, COUNT(*) AS n FROM ${table} GROUP BY ${column}`,
      );
      for (const r of rows) {
        const key = r.k === null || r.k === undefined ? 'null' : String(r.k);
        result[key] = Number(r.n ?? 0);
      }
    } catch {
      // Table or column absent.
    }
    return result;
  };
  const runRow = (() => {
    try {
      return queryRows(
        db,
        `SELECT run_id, started_at_ns, ended_at_ns, scenario_id, mode, collector_version
         FROM runs LIMIT 1`,
      )[0];
    } catch {
      return undefined;
    }
  })();

  return {
    run: runRow
      ? {
          run_id: runRow.run_id ? String(runRow.run_id) : null,
          started_at_ns: runRow.started_at_ns != null ? Number(runRow.started_at_ns) : null,
          ended_at_ns: runRow.ended_at_ns != null ? Number(runRow.ended_at_ns) : null,
          scenario_id: runRow.scenario_id ? String(runRow.scenario_id) : null,
          mode: runRow.mode ? String(runRow.mode) : null,
          collector_version: runRow.collector_version ? String(runRow.collector_version) : null,
        }
      : null,
    snapshots: { count: count('graph_snapshots') },
    graph: { nodes: count('graph_nodes'), edges: count('graph_edges') },
    alerts: { count: count('responsibility_alerts') },
    evidence: {
      count: count('correlation_evidence'),
      by_match_level: groupCount('correlation_evidence', 'match_level'),
    },
    claims: {
      count: count('responsibility_claims'),
      by_type: groupCount('responsibility_claims', 'claim_type'),
      by_severity: groupCount('responsibility_claims', 'severity'),
    },
    tracebacks: { count: count('responsibility_tracebacks') },
    intents: {
      count: count('intents'),
      by_tool_name: groupCount('intents', 'tool_name'),
      by_authority_source: groupCount('intents', 'authority_source'),
    },
    actions: {
      count: count('actions'),
      by_action_type: groupCount('actions', 'action_type'),
      by_signal_class: groupCount('actions', 'signal_class'),
    },
    pollution: { count: count('context_pollution_events') },
    policy_evaluations: (() => {
      const c = extractPolicyComplianceFromDb(db);
      return { count: c.total, violations: c.violations };
    })(),
    processes: { count: count('process_events') },
    tls_sessions: { count: count('tls_sessions') },
    identity_process_map: { count: count('identity_process_map') },
    provisional_identities: { count: count('provisional_identities') },
    agent_identities: { count: count('agent_identities') },
    message_bindings: { count: count('message_bindings') },
    delegation_records: { count: count('delegation_records') },
    subagent_boundaries: { count: count('subagent_boundaries') },
    refinements: {
      claim: count('claim_refinements'),
      classification: count('classification_refinements'),
      evidence: count('evidence_refinements'),
      graph: count('graph_refinements'),
      identity: count('identity_refinements'),
    },
    non_link_diagnostics: { count: count('non_link_diagnostics') },
  };
}

const EMPTY_GRAPH: { graph: GraphData; metrics: null; runId: null } = {
  graph: { nodes: [], edges: [] },
  metrics: null,
  runId: null,
};

/** Load the latest graph snapshot (nodes + edges + metrics) from graph_snapshots. */
export function extractGraphFromDb(
  db: Database,
): { graph: GraphData; metrics: GraphMetrics | null; runId: string | null } {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      'SELECT nodes_json, edges_json, metrics_json, run_id FROM graph_snapshots ORDER BY timestamp_ns DESC LIMIT 1',
    );
  } catch {
    return EMPTY_GRAPH; // Table absent — nothing consumable.
  }
  const row = rows[0];
  if (!row) return EMPTY_GRAPH;

  let nodes: GraphData['nodes'] = [];
  let edges: GraphData['edges'] = [];
  let metrics: GraphMetrics | null = null;
  try {
    nodes = JSON.parse(String(row.nodes_json ?? '[]')) as GraphData['nodes'];
  } catch {
    nodes = [];
  }
  try {
    edges = JSON.parse(String(row.edges_json ?? '[]')) as GraphData['edges'];
  } catch {
    edges = [];
  }
  if (row.metrics_json) {
    try {
      metrics = JSON.parse(String(row.metrics_json)) as GraphMetrics;
    } catch {
      metrics = null;
    }
  }
  return {
    graph: { nodes, edges },
    metrics,
    runId: row.run_id ? String(row.run_id) : null,
  };
}

// ═══ M0 全行提取器（12 表深链，除已读 6 表外全量消费） ═══

/** context_pollution_events 全行。 */
export function extractPollutionEventsFromDb(db: Database): ContextPollutionEvent[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(db, 'SELECT pollution_id, source_agent_id, contaminated_artifact, propagation_path_json, detected_at_ns, run_id FROM context_pollution_events');
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    pollution_id: Number(r.pollution_id ?? 0),
    source_agent_id: String(r.source_agent_id ?? ''),
    contaminated_artifact: String(r.contaminated_artifact ?? ''),
    propagation_path: parseJsonArray<string>(r.propagation_path_json),
    detected_at_ns: Number(r.detected_at_ns ?? 0),
    run_id: r.run_id ? String(r.run_id) : null,
  }));
}

/** policy_evaluations 全行。judgment_json 解析为 object；resource_json 非 NormalizedArgs 形态 → null。 */
export function extractPolicyEvaluationsFromDb(db: Database): PolicyEvaluation[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(db, 'SELECT policy_id, boundary, judgment_json, subject_agent_id, resource_json, evidence_refs_json, confidence, reason, run_id FROM policy_evaluations');
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    policy_id: String(r.policy_id ?? ''),
    boundary: String(r.boundary ?? ''),
    judgment: parseJsonObject<{ result?: string; [k: string]: unknown }>(r.judgment_json),
    subject_agent_id: String(r.subject_agent_id ?? ''),
    resource: parseNormalizedArgsLike(r.resource_json),
    evidence_refs: parseJsonArray<string>(r.evidence_refs_json),
    confidence: Number(r.confidence ?? 0),
    reason: String(r.reason ?? ''),
    run_id: r.run_id ? String(r.run_id) : null,
  }));
}

/** non_link_diagnostics 全行。 */
export function extractNonLinkDiagnosticsFromDb(db: Database): NonLinkDiagnostic[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(db, 'SELECT diagnostic_id, intent_id, action_id, reason, timestamp_ns, run_id FROM non_link_diagnostics');
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    diagnostic_id: String(r.diagnostic_id ?? ''),
    intent_id: r.intent_id ? String(r.intent_id) : null,
    action_id: r.action_id ? String(r.action_id) : null,
    reason: String(r.reason ?? ''),
    timestamp_ns: Number(r.timestamp_ns ?? 0),
    run_id: r.run_id ? String(r.run_id) : null,
  }));
}

/** agent_identities 全行。JSON 数组列逐列解析。 */
export function extractAgentIdentitiesFromDb(db: Database): AgentIdentity[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(db, 'SELECT agent_id, process_ids_json, message_ids_json, tls_sessions_json, confidence, provisional_refs_json, run_id FROM agent_identities');
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    agent_id: String(r.agent_id ?? ''),
    process_ids: parseJsonArray<{ pid: number; start_time_ns: number }>(r.process_ids_json),
    message_ids: parseJsonArray<string>(r.message_ids_json),
    tls_sessions: parseJsonArray<string>(r.tls_sessions_json),
    confidence: String(r.confidence ?? ''),
    provisional_refs: parseJsonArray<string>(r.provisional_refs_json),
    run_id: r.run_id ? String(r.run_id) : null,
  }));
}

/** provisional_identities 全行。 */
export function extractProvisionalIdentitiesFromDb(db: Database): ProvisionalIdentity[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(db, 'SELECT agent_id, process_ids_json, primary_signal, confidence, run_id FROM provisional_identities');
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    agent_id: String(r.agent_id ?? ''),
    process_ids: parseJsonArray<{ pid: number; start_time_ns: number }>(r.process_ids_json),
    primary_signal: String(r.primary_signal ?? ''),
    confidence: Number(r.confidence ?? 0),
    run_id: r.run_id ? String(r.run_id) : null,
  }));
}

/** identity_process_map 全行。 */
export function extractIdentityProcessMapFromDb(db: Database): IdentityProcessMapEntry[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(db, 'SELECT agent_id, pid, start_time_ns FROM identity_process_map');
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    agent_id: String(r.agent_id ?? ''),
    pid: Number(r.pid ?? 0),
    start_time_ns: Number(r.start_time_ns ?? 0),
  }));
}

/**
 * process_events 全行 + JOIN identity_process_map 身份标注。
 * 已识别进程优先入 cap（血缘树完整），再按时间升序；cap 命中用 total/truncated 诚实标注。
 */
export function extractProcessEventsFromDb(db: Database, cap = 25000): CappedExtract<ProcessEvent> {
  const total = (() => {
    try {
      const rows = queryRows(db, 'SELECT COUNT(*) AS n FROM process_events');
      return Number(rows[0]?.n ?? 0);
    } catch {
      return 0; // Table absent.
    }
  })();
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      `SELECT pe.process_event_id, pe.pid, pe.start_time_ns, pe.parent_pid,
              pe.parent_start_time_ns, pe.comm, pe.timestamp_ns, ipm.agent_id
       FROM process_events pe
       LEFT JOIN identity_process_map ipm
         ON ipm.pid = pe.pid AND ipm.start_time_ns = pe.start_time_ns
       ORDER BY (ipm.agent_id IS NOT NULL) DESC, pe.timestamp_ns ASC
       LIMIT ?`,
      [cap],
    );
  } catch {
    return { items: [], total, truncated: total > cap }; // Table absent.
  }
  return {
    items: rows.map((r) => ({
      process_event_id: Number(r.process_event_id ?? 0),
      pid: Number(r.pid ?? 0),
      start_time_ns: Number(r.start_time_ns ?? 0),
      parent_pid: r.parent_pid != null ? Number(r.parent_pid) : null,
      parent_start_time_ns: r.parent_start_time_ns != null ? Number(r.parent_start_time_ns) : null,
      comm: String(r.comm ?? ''),
      timestamp_ns: Number(r.timestamp_ns ?? 0),
      agent_id: r.agent_id ? String(r.agent_id) : null,
    })),
    total,
    truncated: total > cap,
  };
}

/** tls_sessions 全行 + JOIN 身份标注，cap 语义同 process_events。 */
export function extractTlsSessionsFromDb(db: Database, cap = 25000): CappedExtract<TlsSession> {
  const total = (() => {
    try {
      const rows = queryRows(db, 'SELECT COUNT(*) AS n FROM tls_sessions');
      return Number(rows[0]?.n ?? 0);
    } catch {
      return 0; // Table absent.
    }
  })();
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      `SELECT t.tls_session_id, t.pid, t.start_time_ns, t.timestamp_ns, ipm.agent_id
       FROM tls_sessions t
       LEFT JOIN identity_process_map ipm
         ON ipm.pid = t.pid AND ipm.start_time_ns = t.start_time_ns
       ORDER BY (ipm.agent_id IS NOT NULL) DESC, t.timestamp_ns ASC
       LIMIT ?`,
      [cap],
    );
  } catch {
    return { items: [], total, truncated: total > cap }; // Table absent.
  }
  return {
    items: rows.map((r) => ({
      tls_session_id: String(r.tls_session_id ?? ''),
      pid: Number(r.pid ?? 0),
      start_time_ns: Number(r.start_time_ns ?? 0),
      timestamp_ns: Number(r.timestamp_ns ?? 0),
      agent_id: r.agent_id ? String(r.agent_id) : null,
    })),
    total,
    truncated: total > cap,
  };
}

/** subagent_boundaries 全行。 */
export function extractSubagentBoundariesFromDb(db: Database): SubagentBoundary[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(db, 'SELECT boundary_id, parent_agent_id, child_agent_id, parent_pid, parent_start_time_ns, child_pid, child_start_time_ns, reason, run_id FROM subagent_boundaries');
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    boundary_id: String(r.boundary_id ?? ''),
    parent_agent_id: String(r.parent_agent_id ?? ''),
    child_agent_id: String(r.child_agent_id ?? ''),
    parent_pid: Number(r.parent_pid ?? 0),
    parent_start_time_ns: Number(r.parent_start_time_ns ?? 0),
    child_pid: Number(r.child_pid ?? 0),
    child_start_time_ns: Number(r.child_start_time_ns ?? 0),
    reason: String(r.reason ?? ''),
    run_id: r.run_id ? String(r.run_id) : null,
  }));
}

/**
 * graph_snapshots 全帧：metrics 连续相同帧去重（保留每段首个），null metrics 帧保留（图仍解析），
 * 超过 maxFrames 均匀降采样。kept 帧的 nodes/edges 提取时即解析（db 关闭后无法懒加载），坏 JSON → 空图。
 */
export function extractSnapshotFramesFromDb(db: Database, maxFrames = 30): GraphSnapshotFrame[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      'SELECT snapshot_id, timestamp_ns, nodes_json, edges_json, metrics_json FROM graph_snapshots ORDER BY timestamp_ns ASC',
    );
  } catch {
    return []; // Table absent.
  }
  const frames: GraphSnapshotFrame[] = rows.map((r) => {
    let nodes: GraphData['nodes'] = [];
    let edges: GraphData['edges'] = [];
    try {
      nodes = JSON.parse(String(r.nodes_json ?? '[]')) as GraphData['nodes'];
    } catch {
      nodes = [];
    }
    try {
      edges = JSON.parse(String(r.edges_json ?? '[]')) as GraphData['edges'];
    } catch {
      edges = [];
    }
    return {
      snapshot_id: String(r.snapshot_id ?? ''),
      timestamp_ns: Number(r.timestamp_ns ?? 0),
      graph: { nodes, edges },
      metrics: parseJsonObject<GraphMetrics>(r.metrics_json),
    };
  });
  const deduped = frames.filter((f, i) => {
    if (i === 0) return true;
    const prev = frames[i - 1].metrics;
    if (f.metrics === null || prev === null) return true;
    return JSON.stringify(f.metrics) !== JSON.stringify(prev);
  });
  return pickRepresentativeFrames(deduped, maxFrames);
}

const EMPTY_ENGINE: EngineSection = {
  rule_verdicts: [],
  pattern_verdicts: [],
  classification_score: 0,
  classification_route: '',
  score_breakdown: {
    rule_score: 0,
    pattern_score: 0,
    boundary_score: 0,
    evidence_score: 0,
    role_score: 0,
    repetition_score: 0,
    total: 0,
  },
};

const EMPTY_TIMELINE: BehaviorTimeline = { steps: [], total_duration_ms: 0 };

function mapAlertRow(r: Record<string, unknown>): ResponsibilityAlert {
  const involved = parseJsonArray<{ agent_id: string; claimed_role: string | null }>(
    r.involved_agents_json,
  );
  const primaryId = r.primary_agent_id ? String(r.primary_agent_id) : '';
  const primary = involved.find((agent) => agent.agent_id === primaryId) ?? null;
  const graphJson = parseJsonObject<{
    snapshot_id?: string;
    timestamp_ns?: number;
    metrics?: GraphMetrics;
  }>(r.graph_metrics_json);
  const ts = Number(r.timestamp_ns ?? 0);
  const phase = parsePhaseIndicator(r.phase) ?? { Provisional: { timestamp_ns: ts } };

  // graph_snapshot is kept lean: the full graph is loaded separately from
  // graph_snapshots, and every consumer strips graph_snapshot from alerts to
  // save memory anyway. Duplicating nodes/edges here would add ~1.5MB/alert.
  const snapshot: GraphSnapshot = {
    snapshot_id: graphJson?.snapshot_id ?? '',
    timestamp_ns: graphJson?.timestamp_ns ?? ts,
    nodes: [],
    edges: [],
    metrics: graphJson?.metrics ?? EMPTY_METRICS,
  };

  return {
    alert_id: String(r.alert_id ?? ''),
    timestamp_ns: ts,
    display_profile: (r.display_profile as ResponsibilityAlert['display_profile']) ?? 'UnsafeByEngine',
    decision: (r.decision as ResponsibilityAlert['decision']) ?? 'Uncertain',
    severity: (r.severity as ResponsibilityAlert['severity']) ?? 'Warning',
    confidence: Number(r.confidence ?? 0),
    authority: (r.authority as ResponsibilityAlert['authority']) ?? 'Unavailable',
    reasoning: String(r.reasoning ?? ''),
    title: String(r.title ?? ''),
    summary: String(r.summary ?? ''),
    action_hint: r.action_hint ? String(r.action_hint) : null,
    primary_agent: { agent_id: primaryId, claimed_role: primary?.claimed_role ?? null },
    involved_agents: involved,
    engine: parseJsonObject<EngineSection>(r.engine_section_json) ?? EMPTY_ENGINE,
    timeline: parseJsonObject<BehaviorTimeline>(r.timeline_json) ?? EMPTY_TIMELINE,
    graph_snapshot: snapshot,
    phase,
    provisional_ref: r.provisional_ref ? String(r.provisional_ref) : null,
  };
}

/** Load responsibility_alerts, mirroring data-server.py's lean alert shape. */
export function extractAlertsFromDb(db: Database): ResponsibilityAlert[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      `SELECT alert_id, timestamp_ns, display_profile, decision, severity, confidence,
              authority, reasoning, title, summary, action_hint, primary_agent_id,
              involved_agents_json, engine_section_json, timeline_json,
              graph_metrics_json, phase, provisional_ref, run_id
       FROM responsibility_alerts
       ORDER BY timestamp_ns ASC`,
    );
  } catch {
    return []; // Table absent.
  }
  return rows.map(mapAlertRow);
}

/** Load correlation_evidence — columns map 1:1 onto CorrelationEvidence. */
export function extractEvidenceFromDb(db: Database): CorrelationEvidence[] {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      `SELECT evidence_id, intent_id, action_id, match_level, confidence,
              matched_fields_json, derivation_mode, phase, timestamp_ns
       FROM correlation_evidence
       ORDER BY timestamp_ns ASC`,
    );
  } catch {
    return []; // Table absent.
  }
  return rows.map((r) => ({
    evidence_id: String(r.evidence_id ?? ''),
    intent_id: String(r.intent_id ?? ''),
    action_id: String(r.action_id ?? ''),
    match_level: (r.match_level as CorrelationEvidence['match_level']) ?? 'L5Substring',
    confidence: Number(r.confidence ?? 0),
    matched_fields: parseMatchedFields(r.matched_fields_json),
    derivation_mode: String(r.derivation_mode ?? ''),
    phase: parsePhaseIndicator(r.phase) ?? { Provisional: { timestamp_ns: Number(r.timestamp_ns ?? 0) } },
    timestamp_ns: Number(r.timestamp_ns ?? 0),
  }));
}

const sumValues = (record: Record<string, number>): number =>
  Object.values(record).reduce((sum, n) => sum + n, 0);

/** 快照帧：仅读 metrics_json（绝不解析 nodes/edges_json）。null metrics 行跳过。 */
export function extractSnapshotMetricsFromDb(
  db: Database,
): SnapshotMetricsExtract {
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(
      db,
      'SELECT snapshot_id, timestamp_ns, metrics_json FROM graph_snapshots ORDER BY timestamp_ns ASC',
    );
  } catch {
    return { available: false, frames: [] }; // Table absent.
  }
  const frames: SnapshotMetricFrame[] = [];
  for (const r of rows) {
    const m = parseJsonObject<{
      node_counts?: Record<string, number>;
      edge_counts?: Record<string, number>;
      agent_count?: number;
      cross_agent_edges?: number;
      delegation_depth?: number;
      taint_chains?: number;
      avg_confidence?: number;
    }>(r.metrics_json);
    if (!m) continue; // null/坏 JSON 帧跳过（如 fixture snap-old）
    frames.push({
      snapshot_id: String(r.snapshot_id ?? ''),
      timestamp_ns: Number(r.timestamp_ns ?? 0),
      nodeCount: sumValues(m.node_counts ?? {}),
      edgeCount: sumValues(m.edge_counts ?? {}),
      agentCount: Number(m.agent_count ?? 0),
      crossAgentEdges: Number(m.cross_agent_edges ?? 0),
      delegationDepth: Number(m.delegation_depth ?? 0),
      taintChains: Number(m.taint_chains ?? 0),
      avgConfidence: Number(m.avg_confidence ?? 0),
    });
  }
  return { available: true, frames };
}

/** 意图-动作失配：nld 按 reason + 证据按 match_level（L5Substring 弱 / 其余强）。 */
export function extractMismatchAggregateFromDb(db: Database): MismatchExtract {
  const byReason: Record<string, number> = {};
  let count = 0;
  try {
    const rows = queryRows(
      db,
      'SELECT reason AS k, COUNT(*) AS n FROM non_link_diagnostics GROUP BY reason',
    );
    for (const r of rows) {
      const n = Number(r.n ?? 0);
      byReason[String(r.k ?? '')] = n;
      count += n;
    }
  } catch {
    return { available: false, count: 0, byReason: {}, weak: 0, strong: 0 };
  }
  let weak = 0;
  let strong = 0;
  try {
    const rows = queryRows(
      db,
      'SELECT match_level AS k, COUNT(*) AS n FROM correlation_evidence GROUP BY match_level',
    );
    for (const r of rows) {
      const n = Number(r.n ?? 0);
      if (String(r.k) === 'L5Substring') weak += n;
      else strong += n;
    }
  } catch {
    // correlation_evidence 缺表 → weak/strong 归零（证据不是本视角的主信号）
  }
  return { available: true, count, byReason, weak, strong };
}

/** 上下文污染：按 source_agent_id 聚合。 */
export function extractPollutionAggregateFromDb(db: Database): PollutionExtract {
  const bySource: Record<string, number> = {};
  let count = 0;
  try {
    const rows = queryRows(
      db,
      'SELECT source_agent_id AS k, COUNT(*) AS n FROM context_pollution_events GROUP BY source_agent_id',
    );
    for (const r of rows) {
      const n = Number(r.n ?? 0);
      bySource[String(r.k ?? '')] = n;
      count += n;
    }
  } catch {
    return { available: false, count: 0, bySource: {} };
  }
  return { available: true, count, bySource };
}

/** 策略合规：逐行解析 judgment_json.result === 'Violation'。无时间戳，不做时间趋势。 */
export function extractPolicyComplianceFromDb(db: Database): PolicyExtract {
  const byBoundary: Record<string, number> = {};
  let total = 0;
  let violations = 0;
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(db, 'SELECT boundary, judgment_json FROM policy_evaluations');
  } catch {
    return { available: false, total: 0, violations: 0, byBoundary: {} };
  }
  for (const r of rows) {
    total++;
    const b = r.boundary == null || r.boundary === '' ? 'null' : String(r.boundary);
    byBoundary[b] = (byBoundary[b] ?? 0) + 1;
    const judgment = parseJsonObject<{ result?: string }>(r.judgment_json);
    if (judgment?.result === 'Violation') violations++;
  }
  return { available: true, total, violations, byBoundary };
}

/** 网络会话：total + byPid + 60s 时间桶趋势（timestamp_ns）。 */
export function extractTlsSessionAggregateFromDb(db: Database): TlsExtract {
  const byPid: Record<string, number> = {};
  let total = 0;
  let rows: Record<string, unknown>[];
  try {
    rows = queryRows(db, 'SELECT pid, timestamp_ns FROM tls_sessions ORDER BY timestamp_ns ASC');
  } catch {
    return { available: false, total: 0, byPid: {}, trend: [] };
  }
  const BUCKET_NS = 60_000_000_000;
  const buckets = new Map<number, number>();
  for (const r of rows) {
    total++;
    const pid = String(r.pid ?? '');
    byPid[pid] = (byPid[pid] ?? 0) + 1;
    const ts = Number(r.timestamp_ns ?? 0);
    if (ts > 0) {
      const t = Math.floor(ts / BUCKET_NS) * BUCKET_NS;
      buckets.set(t, (buckets.get(t) ?? 0) + 1);
    }
  }
  const trend = [...buckets.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([t, count]) => ({ t, count }));
  return { available: true, total, byPid, trend };
}

export function extractCountsFromDb(
  db: Database,
): { tracebackCount: number; claimCount: number; intentCount: number; actionCount: number } {
  const count = (table: string): number => {
    try {
      const rows = queryRows(db, `SELECT COUNT(*) AS n FROM ${table}`);
      return Number(rows[0]?.n ?? 0);
    } catch {
      return 0; // Table absent (older/smaller dbs) — count as zero.
    }
  };
  return {
    tracebackCount: count('responsibility_tracebacks'),
    claimCount: count('responsibility_claims'),
    intentCount: count('intents'),
    actionCount: count('actions'),
  };
}

/**
 * Load a `.db`/`.sqlite` file into a consumable snapshot. Throws
 * DatabaseImportError when the bytes are not a valid SQLite database.
 */
export async function loadDatabase(bytes: Uint8Array, fileName: string): Promise<DbImport> {
  const label = (fileName.replace(/^.*[\\/]/, '') || 'run.db').trim();
  if (!isSqlite(bytes)) {
    throw new DatabaseImportError('不是有效的 SQLite 数据库文件');
  }
  let db: Database;
  try {
    db = await openDatabase(bytes);
  } catch {
    throw new DatabaseImportError('不是有效的 SQLite 数据库文件');
  }

  try {
    const { graph, metrics, runId } = extractGraphFromDb(db);
    const alerts = extractAlertsFromDb(db);
    const evidence = extractEvidenceFromDb(db);
    const claims = extractClaimsFromDb(db);
    const tracebacks = extractTracebacksFromDb(db);
    const intents = extractIntentsFromDb(db);
    const actions = extractActionsFromDb(db);
    const overview = extractDatasetOverviewFromDb(db);
    const counts = extractCountsFromDb(db);
    const processExtract = extractProcessEventsFromDb(db);
    const tlsExtract = extractTlsSessionsFromDb(db);
    const dbAssets: DbAssets = {
      pollutionEvents: extractPollutionEventsFromDb(db),
      policyEvaluations: extractPolicyEvaluationsFromDb(db),
      nonLinkDiagnostics: extractNonLinkDiagnosticsFromDb(db),
      agentIdentities: extractAgentIdentitiesFromDb(db),
      provisionalIdentities: extractProvisionalIdentitiesFromDb(db),
      identityProcessMap: extractIdentityProcessMapFromDb(db),
      processEvents: processExtract.items,
      processEventCount: processExtract.total,
      processEventTruncated: processExtract.truncated,
      tlsSessions: tlsExtract.items,
      tlsSessionCount: tlsExtract.total,
      tlsSessionTruncated: tlsExtract.truncated,
      subagentBoundaries: extractSubagentBoundariesFromDb(db),
      snapshotFrames: extractSnapshotFramesFromDb(db),
    };
    const resolvedMetrics = metrics ?? deriveGraphMetrics(graph);

    const monitoring = deriveMonitoringState({
      sourceLabel: label,
      derivedAtMs: Date.now(),
      snapshots: extractSnapshotMetricsFromDb(db),
      alerts,
      pollution: extractPollutionAggregateFromDb(db),
      mismatch: extractMismatchAggregateFromDb(db),
      policy: extractPolicyComplianceFromDb(db),
      tls: extractTlsSessionAggregateFromDb(db),
      intents,
      actions,
    });

    return {
      id: `db:${label}`,
      label,
      graph,
      alerts,
      evidence,
      claims,
      tracebacks,
      intents,
      actions,
      overview,
      monitoring,
      metrics: resolvedMetrics,
      nodeCount: graph.nodes.length,
      edgeCount: graph.edges.length,
      alertCount: alerts.length,
      evidenceCount: evidence.length,
      ...counts,
      runId,
      dbAssets,
      hasConsumableData:
        graph.nodes.length > 0 ||
        alerts.length > 0 ||
        evidence.length > 0 ||
        claims.length > 0 ||
        intents.length > 0 ||
        actions.length > 0 ||
        dbAssets.processEvents.length > 0 ||
        dbAssets.tlsSessions.length > 0,
    };
  } finally {
    db.close();
  }
}
