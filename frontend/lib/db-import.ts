import type { Database, SqlJsStatic } from 'sql.js';
import type {
  AlertSeverity,
  BehaviorTimeline,
  ClaimState,
  ClaimType,
  CorrelationEvidence,
  EngineSection,
  GraphData,
  GraphMetrics,
  GraphSnapshot,
  NormalizedArgs,
  PhaseIndicator,
  ResponsibilityAlert,
  ResponsibilityClaim,
  ResponsibilityTraceback,
} from './types';
import { deriveGraphMetrics } from './graph-metrics';

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
  /** True when any of the three consumable tables yielded rows. */
  hasConsumableData: boolean;
};

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
    const counts = extractCountsFromDb(db);
    const resolvedMetrics = metrics ?? deriveGraphMetrics(graph);

    return {
      id: `db:${label}`,
      label,
      graph,
      alerts,
      evidence,
      metrics: resolvedMetrics,
      nodeCount: graph.nodes.length,
      edgeCount: graph.edges.length,
      alertCount: alerts.length,
      evidenceCount: evidence.length,
      ...counts,
      runId,
      hasConsumableData: graph.nodes.length > 0 || alerts.length > 0 || evidence.length > 0,
    };
  } finally {
    db.close();
  }
}
