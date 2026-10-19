# 数据库深链消费四功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已完成的「数据库全表消费第一波」（commit a6cd724）之上，对 responsibility.db 剩余 12 张表做全行深链消费，织入四个前端功能：告警取证链（M1）/ 身份血缘（M2）/ 原始事件浏览器（M3）/ 图时间演化（M4），先完成 M0 数据地基（11 个全行提取器 + store 扩展 + fixture 造数）。

**Architecture:** 全部纯函数位于 `lib/`（extractor 在 `db-import.ts`，派生模型在各自 lib），遵循现有「lib 纯函数 + 单测 + 组件消费 store」模式。M0 一次性把 12 张表全行提取进 `DbAssets`（存于 store），M1–M4 各自从 `store.dbAssets` 消费。数据量防线：process_events/tls_sessions 带 cap + totalCount + truncated 标注，graph 帧去重 + ≤30 降采样。

**Tech Stack:** Next.js 16 / React 19 / TypeScript 5 / Zustand store / sql.js（浏览器内读 db，`globalThis.initSqlJs` 注入，绝不 import sql.js）/ vitest / @tanstack/react-virtual（VirtualList）。

## 计划期裁决（spec 批准后、实施前核验出的 6 处偏离，均已实测确认）

| # | spec 原文 | 偏离 | 实测依据 |
|---|-----------|------|---------|
| 1 | 新增 `NonLinkDiagnostic`（schema.rs 形状） | 现有 `lib/types.ts:280` 已有一个**不同形状**的 `NonLinkDiagnostic`（含 schema_version/candidate_id/stage 等），仅 `lib/api.ts:9,84` 的 `/api/runs/:id/evidence` `non_links` 类型引用它。**重命名现有为 `ApiNonLinkDiagnostic`**，新类型占用 `NonLinkDiagnostic`。 | grep 全仓：仅 api.ts 两处引用现有类型；consumers（use-run-evidence/tests）只 mock `non_links: []` 不 import 类型 |
| 2 | `snapshotFrameIndex` 默认 0 | **applyDbImport 初始化为 `Math.max(0, snapshotFrames.length - 1)`（最新帧索引）**。store 字段初始值仍为 0（空态），仅 db 导入时指向最新帧。理由：store.graph 已是最新帧（`extractGraphFromDb ORDER BY DESC LIMIT 1`），帧索引指向最新帧才能让 scrubber 初始位置与用户已看到的图一致（对象恒常），且「时间演化」时间轴天然从左到右为最早→最新。 | command-center-page.tsx:30 `useAppStore((s) => s.graph)` 初始=最新帧 |
| 3 | `EngineVerdict.targets`（pattern 时） | 现有 `PatternVerdict`（types.ts:116-121）**只有 template_id/match_confidence/llm_agreed/llm_comment，无 targets 字段** → pattern 的 targets 恒 `[]`；rule 的 `matched_resource` 作 `[target]`。诚实留空，不硬造。 | types.ts 已读 |
| 4 | `PollutionChain.dt_ns: number`（writer 首写时延） | `context_pollution_events` 表**只有 detected_at_ns 一条时间列，无逐 hop 时间戳** → dt_ns 无法计算，类型改 **`dt_ns: number | null`**，恒 null。 | schema.rs:208-215 已读 |
| 5 | `buildForensicChain` 输入含 `intents: Intent[]`，诊断按「alert timeline 的意图」匹配 | alert.timeline.steps **无 intent_id 字段**（types.ts:128-140），intents 无法直接关联 alert。改为输入 **`intentIds: string[]` + `alertEvidenceRefs: string[]`**（组件层由 `collectAlertEvidenceRefs` + evidence 桥接），去掉 `intents`。诊断按 `intent_id ∈ intentIds`，claimRefs 按 `claims[].evidence_refs ∩ alertEvidenceRefs`。这与 spec §4.1 自注「evidence 由组件层复用 collectAlertEvidenceRefs 桥接」一致。 | types.ts BehaviorTimelineEntry 已读 |
| 6 | M4「stage 的 graphData 来自 snapshotFrames[index].graph」 | `MtAgentLensGraphStage` 目前内部 `useAppStore((s) => s.graph)`（mtagentlens-graph-stage.tsx:30）。给 stage 加可选 **`graphOverride?: GraphData`** prop（有则用它替代 store.graph），态势页传 `snapshotFrames[snapshotFrameIndex].graph`。**不改全局 store.graph**，遵守 spec §9「不改 store 已有字段语义」。 | mtagentlens-graph-stage.tsx:28-31 已读 |

## Global Constraints

- **四门全绿（每里程碑）**：`npm run type-check`（0 错误）+ `npm run lint`（0 警告）+ `npx vitest run`（全过）+ `npm run build`。命令均在 `frontend/` 下执行。
- **不幻觉**：所有展示由纯函数从导入数据派生；算不出 → 留空/标注（null/[]/空态文案），绝不硬编码 demo 值。
- **数据量防线**：全量读入浏览器内存的数据有 cap + 「已展示 X / total」标注；graph 帧去重 + ≤30 均匀降采样。
- **惰性纪律**：graph_snapshots 帧**仅在提取时解析一次**（loadDatabase 内 `db.close()` 后无法懒加载）；`intents`/`actions`/`correlation_evidence` 已全量读，不重复读。
- **不改 Rust collector / 后端 API**；不改 3-dock 侧边栏隐喻；不改 store 已有字段语义（只新增 `dbAssets`/`snapshotFrameIndex`/`setSnapshotFrameIndex`）。
- **真实数据空表**：B 的家族树/升级、A 的任一空源 → 空态，不伪造。
- **语言**：代码/变量/commit 英文，交互与注释（需解释时）中文；现有文件沿用其注释语言风格。
- **sql.js 铁律**：浏览器/打包路径永不 `import sql.js`；Node/vitest 脚本（`scripts/gen-db-fixture.mjs`、`test/setup.ts`）可直接 import。
- **提交纪律**：仅里程碑提交；每个里程碑四门全绿后**停下验收**，commit message 由用户亲自定稿；commit 不含 "agentsight"，无 conventional 前缀。
- **Worktree 隔离不可用**（CLAUDE.md 铁律）——全部内联执行，不用 `Agent` + `isolation: "worktree"`。

---

## 里程碑 0 · 数据地基（11 个全行提取器 + DbAssets + store + fixture）

### Task 0.1: `lib/types.ts` — 新增 9 个 DB 全行类型 + 重命名现有 NonLinkDiagnostic

**Files:**
- Modify: `lib/types.ts`（`NonLinkDiagnostic` 位于 ~280 行附近；新类型追加到文件末尾 `GraphData` 之后）
- Modify: `lib/api.ts`（类型 import + 使用处）
- Test: 无独立测试（纯类型，由后续任务的编译 + 单测覆盖）

**Interfaces:**
- Consumes: 既有 `NormalizedArgs`、`GraphData`、`GraphMetrics`（types.ts 已导出）
- Produces: `ContextPollutionEvent`、`PolicyEvaluation`、`NonLinkDiagnostic`、`AgentIdentity`、`ProvisionalIdentity`、`IdentityProcessMapEntry`、`ProcessEvent`、`TlsSession`、`SubagentBoundary`、`GraphSnapshotFrame`——供 Task 0.2 提取器与 M1–M4 lib 使用

- [ ] **Step 1: 重命名现有 API 形态 NonLinkDiagnostic**

`lib/types.ts` 中把现有 `export interface NonLinkDiagnostic { ... }`（含 `schema_version`/`candidate_id`/`stage`/`details` 的那一个）改名为：

```ts
/** API `/api/runs/:id/evidence` non_links 响应元素（causal db 形状，与 db 全行形状不同）。 */
export interface ApiNonLinkDiagnostic {
  schema_version: number;
  diagnostic_id: string;
  timestamp_ns: number;
  agent_id: string | null;
  intent_id: string | null;
  candidate_id: string | null;
  reason: string;
  stage: string;
  intent_class: string | null;
  parse_state: string | null;
  match_level_considered: string | null;
  confidence_observed: number | null;
  details: unknown | null;
}
```

`lib/api.ts` 中同步：`import type { ... NonLinkDiagnostic ... }` 改为 `ApiNonLinkDiagnostic`；第 84 行 `non_links: NonLinkDiagnostic[]` 改为 `non_links: ApiNonLinkDiagnostic[]`。

- [ ] **Step 2: 新增 9 个 DB 全行类型 + GraphSnapshotFrame**

`lib/types.ts` 文件末尾（`GraphData` 之后）追加（与 spec §3.1 逐字一致）：

```ts
// ═══ DB 全行深链类型（responsibility.db 12 表全行，M0 提取器产物） ═══

/** context_pollution_events 全行 */
export interface ContextPollutionEvent {
  pollution_id: number;
  source_agent_id: string;
  contaminated_artifact: string;
  propagation_path: string[];      // propagation_path_json，agent 序列
  detected_at_ns: number;
  run_id: string | null;
}

/** policy_evaluations 全行 — judgment_json 解析为 { result, ... } */
export interface PolicyEvaluation {
  policy_id: string;
  boundary: string;
  subject_agent_id: string;
  judgment: { result?: string; [k: string]: unknown } | null;
  resource: NormalizedArgs | null;  // resource_json；非 NormalizedArgs 形态时 null
  evidence_refs: string[];
  confidence: number;
  reason: string;
  run_id: string | null;
}

/** non_link_diagnostics 全行 */
export interface NonLinkDiagnostic {
  diagnostic_id: string;
  intent_id: string | null;
  action_id: string | null;
  reason: string;
  timestamp_ns: number;
  run_id: string | null;
}

/** agent_identities 全行 */
export interface AgentIdentity {
  agent_id: string;
  process_ids: Array<{ pid: number; start_time_ns: number }>;
  message_ids: string[];
  tls_sessions: string[];
  confidence: string;              // 'Authoritative' 等
  provisional_refs: string[];
  run_id: string | null;
}

/** provisional_identities 全行 */
export interface ProvisionalIdentity {
  agent_id: string;
  process_ids: Array<{ pid: number; start_time_ns: number }>;
  primary_signal: string;          // 'TlsSessionNew' 等
  confidence: number;
  run_id: string | null;
}

/** identity_process_map 全行 */
export interface IdentityProcessMapEntry {
  agent_id: string;
  pid: number;
  start_time_ns: number;
}

/** process_events 全行 + 身份标注（JOIN identity_process_map） */
export interface ProcessEvent {
  process_event_id: number;
  pid: number;
  start_time_ns: number;
  parent_pid: number | null;
  parent_start_time_ns: number | null;
  comm: string;
  timestamp_ns: number;
  agent_id: string | null;         // JOIN 标注；无映射时 null
}

/** tls_sessions 全行 + 身份标注 */
export interface TlsSession {
  tls_session_id: string;
  pid: number;
  start_time_ns: number;
  timestamp_ns: number;
  agent_id: string | null;         // JOIN 标注
}

/** subagent_boundaries 全行 */
export interface SubagentBoundary {
  boundary_id: string;
  parent_agent_id: string;
  child_agent_id: string;
  parent_pid: number;
  parent_start_time_ns: number;
  child_pid: number;
  child_start_time_ns: number;
  reason: string;
  run_id: string | null;
}

/** graph_snapshots 演化帧（kept 帧提取时即解析 nodes/edges） */
export interface GraphSnapshotFrame {
  snapshot_id: string;
  timestamp_ns: number;
  graph: GraphData;                // 提取时即解析（loadDatabase 关闭 db 后无法再懒加载）
  metrics: GraphMetrics | null;
}
```

- [ ] **Step 3: 验证编译**

Run: `cd frontend && npm run type-check`
Expected: 0 错误（若 api.ts 有未用 import 会报 lint，先跑 type-check，lint 在 Task 0.7 四门统一验）

- [ ] **Step 4: 轻量自检**

`grep -rn "NonLinkDiagnostic" lib components __tests__`：现在 `NonLinkDiagnostic` 只应是新接口；`ApiNonLinkDiagnostic` 只被 api.ts 引用。

### Task 0.2: `lib/db-import.ts` — 2 个 helper + 11 个全行提取器

**Files:**
- Modify: `lib/db-import.ts`（追加；保留所有现有 extract 函数不动）
- Test: `__tests__/db-import.test.ts`（本任务末尾新增 6 个 describe 块；Task 0.6 统一更新既有断言）

**Interfaces:**
- Consumes: Task 0.1 类型；现有 `queryRows`/`parseJsonArray`/`parseJsonObject`（db-import.ts 私有）、`Database` 类型
- Produces（供 Task 0.3 组装 DbAssets；供 M4 复用）：
  - `parseNormalizedArgsLike(raw: unknown): NormalizedArgs | null`（导出）
  - `pickRepresentativeFrames<T>(frames: T[], maxFrames: number): T[]`（导出，M4 snapshot-evolution 复用）
  - `CappedExtract<T> = { items: T[]; total: number; truncated: boolean }`（导出）
  - 11 个 `extract*FromDb(db: Database, ...): ...`（导出）

- [ ] **Step 1: 追加 helper（parseNormalizedArgsLike + pickRepresentativeFrames）**

在 `parseJsonArray` 之后追加：

```ts
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
```

- [ ] **Step 2: 追加 11 个提取器**（`extractGraphFromDb` 之后、`extractSnapshotMetricsFromDb` 之前插入；`CappedExtract` 类型放文件顶部 export type 区）：

```ts
/** 带 cap 的全行提取产物——total 为真实行数，truncated 标记是否被 cap 截断。 */
export type CappedExtract<T> = { items: T[]; total: number; truncated: boolean };
```

```ts
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
    return JSON.stringify(f.metrics) !== JSON.stringify(prev.metrics);
  });
  return pickRepresentativeFrames(deduped, maxFrames);
}
```

- [ ] **Step 3: db-import.ts 顶部类型 import 补齐**

`db-import.ts` 顶部 `import type { ... } from './types'` 追加：`AgentIdentity`、`ContextPollutionEvent`、`GraphSnapshotFrame`、`IdentityProcessMapEntry`、`NonLinkDiagnostic`、`PolicyEvaluation`、`ProcessEvent`、`ProvisionalIdentity`、`SubagentBoundary`、`TlsSession`。`NormalizedArgs` 已在现有 import 中（供 parseNormalizedArgsLike）。

- [ ] **Step 4: 写失败测试（新增 6 个 describe 到 `__tests__/db-import.test.ts`）**

在文件 `extractDatasetOverviewFromDb` 测试之后追加（沿用现有 `fixtureBytes`/`openDatabase`）：

```ts
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
```

```ts
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
    db.run('CREATE TABLE process_events (process_event_id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL, start_time_ns INTEGER NOT NULL, parent_pid INTEGER, parent_start_time_ns INTEGER, comm TEXT NOT NULL, timestamp_ns INTEGER NOT NULL, run_id TEXT NOT NULL)');
    db.run('CREATE TABLE identity_process_map (agent_id TEXT NOT NULL, pid INTEGER NOT NULL, start_time_ns INTEGER NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(pid, start_time_ns))');
    // 3 条未识别（时间靠前）+ 2 条已识别（时间靠后）
    for (const pid of [1, 2, 3]) {
      db.run('INSERT INTO process_events (pid, start_time_ns, parent_pid, parent_start_time_ns, comm, timestamp_ns, run_id) VALUES (?,?,?,?,?,?,?)', [pid, 1000, null, null, 'u' + pid, 1000 + pid, 'run-x']);
    }
    for (const pid of [9, 10]) {
      db.run('INSERT INTO process_events (pid, start_time_ns, parent_pid, parent_start_time_ns, comm, timestamp_ns, run_id) VALUES (?,?,?,?,?,?,?)', [pid, 1000, null, null, 'x' + pid, 2000 + pid, 'run-x']);
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
```

```ts
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
    db.run('CREATE TABLE graph_snapshots (snapshot_id TEXT, timestamp_ns INTEGER, nodes_json TEXT, edges_json TEXT, metrics_json TEXT, run_id TEXT)');
    const m = JSON.stringify({ node_counts: { Agent: 1 }, edge_counts: {}, agent_count: 1, cross_agent_edges: 0, delegation_depth: 0, taint_chains: 0, avg_confidence: 0.5 });
    for (const [sid, ts] of [['a', 1], ['b', 2], ['c', 3]]) {
      db.run('INSERT INTO graph_snapshots VALUES (?,?,?,?,?,?)', [sid, ts, '[]', '[]', m, 'run-x']);
    }
    const frames = extractSnapshotFramesFromDb(db);
    db.close();
    expect(frames.map((f) => f.snapshot_id)).toEqual(['a']);
  });

  it('超过 maxFrames 均匀降采样，首尾恒保留', async () => {
    const db = await openDatabase(fixtureBytes());
    db.run('DROP TABLE graph_snapshots');
    db.run('CREATE TABLE graph_snapshots (snapshot_id TEXT, timestamp_ns INTEGER, nodes_json TEXT, edges_json TEXT, metrics_json TEXT, run_id TEXT)');
    const metrics = (n: number) => JSON.stringify({ node_counts: { Agent: n }, edge_counts: {}, agent_count: n, cross_agent_edges: 0, delegation_depth: 0, taint_chains: 0, avg_confidence: 0.5 });
    for (let i = 1; i <= 10; i += 1) {
      db.run('INSERT INTO graph_snapshots VALUES (?,?,?,?,?,?)', ['f' + i, i, '[]', '[]', metrics(i), 'run-x']);
    }
    const frames = extractSnapshotFramesFromDb(db, 4);
    db.close();
    expect(frames).toHaveLength(4);
    expect(frames[0].snapshot_id).toBe('f1');
    expect(frames[3].snapshot_id).toBe('f10');
    // 均匀等距：首尾之外取第 4、7 帧（round((10-1)/3 * i) = 0,3,6,9 → f1,f4,f7,f10）
    expect(frames.map((f) => f.snapshot_id)).toEqual(['f1', 'f4', 'f7', 'f10']);
  });

  it('坏 nodes_json 帧 → 空图（不抛错）', async () => {
    const db = await openDatabase(fixtureBytes());
    db.run('DROP TABLE graph_snapshots');
    db.run('CREATE TABLE graph_snapshots (snapshot_id TEXT, timestamp_ns INTEGER, nodes_json TEXT, edges_json TEXT, metrics_json TEXT, run_id TEXT)');
    db.run('INSERT INTO graph_snapshots VALUES (?,?,?,?,?,?)', ['bad', 1, '{broken', '[]', null, 'run-x']);
    const frames = extractSnapshotFramesFromDb(db);
    db.close();
    expect(frames).toHaveLength(1);
    expect(frames[0].graph.nodes).toEqual([]);
    expect(frames[0].metrics).toBeNull();
  });
});
```

- [ ] **Step 5: db-import.test.ts 顶部 import 补齐**

`__tests__/db-import.test.ts` 的 `import { ... } from '@/lib/db-import'` 追加：`extractPollutionEventsFromDb`、`extractPolicyEvaluationsFromDb`、`extractNonLinkDiagnosticsFromDb`、`extractAgentIdentitiesFromDb`、`extractProvisionalIdentitiesFromDb`、`extractIdentityProcessMapFromDb`、`extractProcessEventsFromDb`、`extractTlsSessionsFromDb`、`extractSubagentBoundariesFromDb`、`extractSnapshotFramesFromDb`。

- [ ] **Step 6: 运行测试验证失败**

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts 2>&1 | tail -30`
Expected: FAIL — `extractPollutionEventsFromDb is not a function` / fixture 表缺失导致 count 断言失败。这是 Task 0.5 fixture 造数前的预期红。若只看本任务功能，先跑通除「fixture 行数断言」外的用例（它们会红到 Task 0.5 造数后转绿）。

- [ ] **Step 7: 运行验证编译通过**

Run: `cd frontend && npm run type-check`
Expected: 0 错误（本任务代码 + Task 0.1 类型已就绪）。

### Task 0.3: `DbAssets` + `DbImport` 扩展 + `loadDatabase` 接线

**Files:**
- Modify: `lib/db-import.ts`（`DbImport` 类型 + `loadDatabase` 函数）
- Test: `__tests__/db-import.test.ts`（本任务追加 1 个用例）

**Interfaces:**
- Consumes: Task 0.2 的 11 提取器
- Produces: `DbAssets` 接口、`DbImport.dbAssets` 字段、扩展后的 `hasConsumableData` 语义——供 Task 0.4 store 与 M1–M4 消费

- [ ] **Step 1: 定义 DbAssets 并扩展 DbImport**

`lib/db-import.ts` 中 `DbImport` 类型追加字段，并在其上方新增：

```ts
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
```

`DbImport` 新增：

```ts
  /** M0 全行深链资产（process/tls 已 cap）。 */
  dbAssets: DbAssets;
```

- [ ] **Step 2: loadDatabase 接线**

`loadDatabase` 中，在 `const counts = extractCountsFromDb(db);` 之后、`return {...}` 之前，组装：

```ts
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
```

`return {...}` 追加 `dbAssets,` 并把 `hasConsumableData` 改为：

```ts
      hasConsumableData:
        graph.nodes.length > 0 ||
        alerts.length > 0 ||
        evidence.length > 0 ||
        claims.length > 0 ||
        intents.length > 0 ||
        actions.length > 0 ||
        dbAssets.processEvents.length > 0 ||
        dbAssets.tlsSessions.length > 0,
```

> **注（已知限制，不在本任务修）**：`loadRun` 对「仅 process/tls 有数据、graph/alerts 全空」的 db 会 applyDbImport 成功，但态势页 `command-center-page.tsx` 的 `hasData = graph.nodes.length > 0 || alerts.length > 0` 仍为 false → LoadingScreen。真实 db 恒有图与告警，此情形极罕见；overview 页仍可消费进程谱系。M4 可选补强（hasData 纳入 processEvents）。

- [ ] **Step 3: 写失败测试**

`__tests__/db-import.test.ts` 的 `loadDatabase produces the full consumable snapshot` 用例中追加断言：

```ts
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
```

并新增用例：

```ts
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
```

- [ ] **Step 4: 更新「空库」用例——补 DROP 新表**

`__tests__/db-import.test.ts` 现有「handles a database with no consumable tables gracefully」用例（DROP 6 表处）——新语义下 `processEvents`/`tlsSessions` 非空会让 `hasConsumableData` 变 true，**必须补 DROP**：

```ts
    db.run(
      'DROP TABLE graph_snapshots; DROP TABLE responsibility_alerts; DROP TABLE correlation_evidence;' +
        ' DROP TABLE responsibility_claims; DROP TABLE intents; DROP TABLE actions;' +
        ' DROP TABLE process_events; DROP TABLE tls_sessions; DROP TABLE provisional_identities;' +
        ' DROP TABLE agent_identities; DROP TABLE identity_process_map;' +
        ' DROP TABLE subagent_boundaries; DROP TABLE delegation_records;' +
        ' DROP TABLE context_pollution_events; DROP TABLE policy_evaluations;' +
        ' DROP TABLE non_link_diagnostics;',
    );
```

- [ ] **Step 5: 运行测试**

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts 2>&1 | tail -30`
Expected: 本任务用例仍红（fixture 未造数）——`toHaveLength(7)` 等得到 0。这是 Task 0.5 前的预期红。

### Task 0.4: `lib/store.ts` — dbAssets / snapshotFrameIndex / setSnapshotFrameIndex

**Files:**
- Modify: `lib/store.ts`（`AppStore` 接口、`applyDbImport`、`applySnapshot`、`create<AppStore>` 初始态）
- Test: `__tests__/store-db.test.ts`（追加用例）；`__tests__/store-data-source.test.ts` 不动（无 dbAssets 断言，兼容）

**Interfaces:**
- Consumes: `DbAssets`（db-import）、`DbImport`
- Produces: store 字段 `dbAssets: DbAssets | null`、`snapshotFrameIndex: number`、action `setSnapshotFrameIndex(index: number): void`——供 M1–M4 组件消费

- [ ] **Step 1: 扩展 AppStore 接口**

`lib/store.ts` 的 `interface AppStore` 在 `monitoring: MonitoringState | null;` 之后追加：

```ts
  /** M0 全行深链资产（db 导入时填充；sample/demo/file → null，四功能空态兜底）。 */
  dbAssets: DbAssets | null;
  /** 图时间演化当前帧索引（db 导入时指向最新帧，保持态势页初始视图不变）。 */
  snapshotFrameIndex: number;
```

在 `setTimelinePosition` action 声明后追加：

```ts
  setSnapshotFrameIndex: (index: number) => void;
```

`import type { DbImport } from './db-import'` 改为同时 import `DbAssets`：

```ts
import { loadDatabase as loadDatabaseFromBytes, type DbImport, type DbAssets } from './db-import';
```

- [ ] **Step 2: applyDbImport 填充 dbAssets + 初始化帧索引**

`applyDbImport` 的 `set({...})` 追加：

```ts
    dbAssets: dbImport.dbAssets,
    snapshotFrameIndex: Math.max(0, dbImport.dbAssets.snapshotFrames.length - 1),
```

> **裁决 #2 落地**：帧索引指向最新帧，保证态势页初始视图（store.graph = 最新帧）与 scrubber 当前位置一致。

- [ ] **Step 3: applySnapshot 清空 dbAssets**

`applySnapshot` 的 `set({...})` 在 `monitoring: null,` 后追加：

```ts
    dbAssets: null,
    snapshotFrameIndex: 0,
```

`loadRun` 的 **API 回退分支** `set({...})` 同样在 `monitoring: null,` 后追加 `dbAssets: null, snapshotFrameIndex: 0,`（共两处：applySnapshot 一处、loadRun 回退一处）。

- [ ] **Step 4: create<AppStore> 初始态**

`create<AppStore>((set, get) => ({...}))` 初始态在 `monitoring: null,` 后追加：

```ts
  dbAssets: null,
  snapshotFrameIndex: 0,
```

在 `setTimelinePosition: (pos) => set({ timelinePosition: pos }),` 后追加：

```ts
  setSnapshotFrameIndex: (index) => set({ snapshotFrameIndex: index }),
```

- [ ] **Step 5: 写失败测试（追加到 `__tests__/store-db.test.ts`）**

```ts
  test('loadDatabase 填充 dbAssets 并初始化 snapshotFrameIndex 到最新帧', async () => {
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);

    const s = useAppStore.getState();
    expect(s.dbAssets).not.toBeNull();
    expect(s.dbAssets?.processEvents).toHaveLength(7);
    expect(s.dbAssets?.snapshotFrames).toHaveLength(4);
    // 帧索引指向最新帧（frames.length-1），与 store.graph（最新帧）一致
    expect(s.snapshotFrameIndex).toBe(3);
  });

  test('setSnapshotFrameIndex 更新帧索引', async () => {
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);
    useAppStore.getState().setSnapshotFrameIndex(1);
    expect(useAppStore.getState().snapshotFrameIndex).toBe(1);
  });

  test('db 导入后切 demo 清空 dbAssets 并复位帧索引', async () => {
    const dbImport = await loadDatabaseFromBytes(new Uint8Array(fixtureBytes()), 'run-42.db');
    useAppStore.getState().loadDatabase(dbImport);
    expect(useAppStore.getState().dbAssets).not.toBeNull();

    useAppStore.getState().loadDemo();
    expect(useAppStore.getState().dbAssets).toBeNull();
    expect(useAppStore.getState().snapshotFrameIndex).toBe(0);
  });
```

- [ ] **Step 6: 运行测试**

Run: `cd frontend && npx vitest run __tests__/store-db.test.ts 2>&1 | tail -30`
Expected: 本任务新用例红（fixture 未造数，processEvents 为 0）。这是 Task 0.5 前预期红。

### Task 0.5: `scripts/gen-db-fixture.mjs` — 6 新表 + 行数扩展

**Files:**
- Modify: `scripts/gen-db-fixture.mjs`
- Run（产物）：重新生成 `__tests__/fixtures/db-sample.sqlite`
- Test: 无独立测试（生成器脚本；由 Task 0.6 的测试全量验证产物）

**Interfaces:**
- Consumes: 现有 fixture 结构（4 帧 + alert/evidence/claims/intents/actions/runs/nld/pollution/policy/tls）
- Produces: 扩展后 fixture——pollution 3 / policy 6 / nld 4 / tls 6 / process_events 7 / provisional 3 / agent_identities 2 / identity_map 7 / subagent 2 / delegation 2

- [ ] **Step 1: DDL 追加 6 个新表**

`db.run(...)` 的 CREATE TABLE 块末尾（`tls_sessions` 之后）追加（与 schema.rs 列一致，去 NOT NULL 约束保持 fixture 宽松）：

```sql
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
```

- [ ] **Step 2: process_events / identity_process_map 造数**

在「tls_sessions：4 行」块之后追加：

```js
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
    ['agent-a', 42, 1000], ['agent-a', 102, 1000], ['agent-a', 103, 1000],
    ['agent-a', 99, 900],
    ['agent-b', 43, 1000], ['agent-b', 202, 1000], ['agent-b', 203, 1000],
  ]) {
    db.run('INSERT INTO identity_process_map VALUES (?,?,?,?)', [agent, pid, st, 'run-1']);
  }
  // provisional_identities：3 行；agent-a 与 agent_identities 交集（升级），agent-c 仅 provisional
  const procIds = (pids) => JSON.stringify(pids.map((pid) => ({ pid, start_time_ns: pid === 99 ? 900 : 1000 })));
  for (const [agent, pids, signal, conf] of [
    ['agent-a', [42, 102, 103], 'TlsSessionNew', 0.92],
    ['agent-b', [43, 202, 203], 'ProcessEvent', 0.85],
    ['agent-c', [400], 'TlsSessionNew', 0.6],
  ]) {
    db.run(
      'INSERT INTO provisional_identities VALUES (?,?,?,?,?)',
      [agent, procIds(pids), signal, conf, 'run-1'],
    );
  }
  // agent_identities：2 行；agent-a 已升级（交集），agent-d 仅正式表
  for (const [agent, pids, msgs, sessions, conf, prov] of [
    ['agent-a', [42, 102, 103], ['msg-1'], ['s-1'], 'Authoritative', ['agent-a']],
    ['agent-d', [500], ['msg-9'], ['s-9'], 'Authoritative', ['agent-d']],
  ]) {
    db.run(
      'INSERT INTO agent_identities VALUES (?,?,?,?,?,?,?)',
      [agent, procIds(pids), JSON.stringify(msgs), JSON.stringify(sessions), conf, JSON.stringify(prov), 'run-1'],
    );
  }
  // subagent_boundaries：2 行（委派树 agent-a→agent-b→agent-c）
  for (const [bid, parent, child, pp, pst, cp, cst, reason] of [
    ['bnd-1', 'agent-a', 'agent-b', 42, 1000, 43, 1000, 'delegation'],
    ['bnd-2', 'agent-b', 'agent-c', 43, 1000, 400, 5000, 'subagent spawn'],
  ]) {
    db.run(
      'INSERT INTO subagent_boundaries VALUES (?,?,?,?,?,?,?,?,?)',
      [bid, parent, child, pp, pst, cp, cst, reason, 'run-1'],
    );
  }
  // delegation_records：2 行（带 timestamp_ns 供诞生时序）
  for (const [did, from, to, pp, pst, cp, cst, ts, conf] of [
    ['del-1', 'agent-a', 'agent-b', 42, 1000, 43, 1000, 1100, 0.85],
    ['del-2', 'agent-b', 'agent-c', 43, 1000, 400, 5000, 2100, 0.9],
  ]) {
    db.run(
      'INSERT INTO delegation_records VALUES (?,?,?,?,?,?,?,?,?,?)',
      [did, from, to, pp, pst, cp, cst, ts, conf, 'run-1'],
    );
  }
```

> **process_ids_json 造数**：`procIds(99)` 需要 start_time_ns 900 分支——上面 `procIds` 已按 `pid === 99 ? 900 : 1000` 处理，但 provisional/agent_identities 的 process_ids 不含 99（99 只在 identity_process_map）。保持简单：`procIds` 只用 1000。上面代码中 agent-a 的 pids 为 [42,102,103] 全走 1000 分支，正确。

- [ ] **Step 3: 行数扩展（pollution / policy / nld / tls）**

`context_pollution_events` 块改为 3 行（第三行含 2-hop propagation_path）：

```js
  // context_pollution_events：3 行，第 3 行 propagation_path 覆盖 ≥2 hop
  for (const [pid, source, artifact, path, ts] of [
    [1, 'agent-a', '/tmp/leaked.txt', [], 1000],
    [2, 'agent-b', '/tmp/context.json', [], 1100],
    [3, 'agent-a', '/tmp/script.sh', ['agent-a', 'agent-b', 'agent-c'], 1300],
  ]) {
    db.run(
      'INSERT INTO context_pollution_events VALUES (?,?,?,?,?,?)',
      [pid, source, artifact, JSON.stringify(path), ts, 'run-1'],
    );
  }
```

`policy_evaluations` 块改为 6 行（含 1 Violation + 1 Allow，总量 6，Violation 仍仅 1）：

```js
  for (const [policyId, boundary, subject, result] of [
    ['pol-1', 'Capability', 'agent-a', 'Violation'],
    ['pol-2', 'Capability', 'agent-a', 'Compliant'],
    ['pol-3', 'Capability', 'agent-b', 'Compliant'],
    ['pol-4', 'Capability', 'agent-b', 'Compliant'],
    ['pol-5', 'DataFlow', 'agent-a', 'Allow'],
    ['pol-6', 'DataFlow', 'agent-c', 'Compliant'],
  ]) {
    db.run(
      'INSERT INTO policy_evaluations VALUES (?,?,?,?,?,?,?,?,?)',
      [policyId, boundary, judgment(result), subject, null, '[]', 0.85, '', 'run-1'],
    );
  }
```

`non_link_diagnostics` 块追加第 4 行（保持既有 3 行不动）：

```js
  // 追加第 4 行（AmbiguousMatch，关联 int-2）
  db.run('INSERT INTO non_link_diagnostics VALUES (?,?,?,?,?,?)', [
    'nld-4', 'int-2', null, 'AmbiguousMatch', 1100, 'run-1',
  ]);
```

`tls_sessions` 块改为 6 行（s-5/s-6 用未映射 pid 44，trend 桶为 [3,3]）：

```js
  for (const [sid, pid, st, ts] of [
    ['s-1', 42, 1000, 1000],
    ['s-2', 42, 1000, 5000],
    ['s-3', 43, 1000, 61_000_000_000],
    ['s-4', 43, 1000, 61_005_000_000],
    ['s-5', 44, 1000, 1200],
    ['s-6', 44, 1000, 61_010_000_000],
  ]) {
    db.run(
      'INSERT INTO tls_sessions VALUES (?,?,?,?,?)',
      [sid, pid, st, ts, 'run-1'],
    );
  }
```

> **既有 tls_sessions 块的 st 参数**：原块写死 `1000` 作 start_time_ns，上面已改为显式 `st` 变量（值仍 1000），行为不变。

- [ ] **Step 4: 重新生成 fixture**

Run: `cd frontend && node scripts/gen-db-fixture.mjs`
Expected: `fixture written: <N> bytes`（N 变大）。

- [ ] **Step 5: 运行测试**

Run: `cd frontend && npx vitest run __tests__/db-import.test.ts __tests__/store-db.test.ts 2>&1 | tail -40`
Expected: 本计划新增用例全部转绿；**既有用例开始红**（Task 0.6 修复）：
- `db-import.test.ts`「extracts DatasetOverview counts」overview.pollution/policy/processes/tls/identity_map/provisional/nld 计数断言
- `db-import.test.ts`「handles a database with no consumable tables」（已在上一步补 DROP，应绿）

### Task 0.6: 更新既有断言（db-import / monitoring-extractors / derive-monitoring）

**Files:**
- Modify: `__tests__/db-import.test.ts`（overview 计数 + loadDatabase 断言）
- Modify: `__tests__/monitoring-extractors.test.ts`（nld/pollution/policy/tls 计数 + trend）
- Modify: `__tests__/derive-monitoring.test.ts`（注释同步，断言不动——已核验状态不变）
- Test: 全部为测试自身的期望值更新

**Interfaces:**
- Consumes: Task 0.5 扩展后 fixture
- Produces: 全绿基线（四门之一）

- [ ] **Step 1: db-import.test.ts「extracts DatasetOverview counts」用例更新**

`__tests__/db-import.test.ts` 的 overview 用例中，以下断言按新 fixture 行数更新（旧值 → 新值）：

```ts
    expect(overview.pollution.count).toBe(3);            // 2 → 3
    expect(overview.policy_evaluations.count).toBe(6);   // 3 → 6
    expect(overview.processes.count).toBe(7);            // 0 → 7
    expect(overview.tls_sessions.count).toBe(6);         // 4 → 6
    expect(overview.identity_process_map.count).toBe(7); // 0 → 7
    expect(overview.provisional_identities.count).toBe(3); // 0 → 3
    expect(overview.non_link_diagnostics.count).toBe(4); // 3 → 4
```

并追加 3 条新表计数断言（插在 `provisional_identities` 行后）：

```ts
    expect(overview.agent_identities.count).toBe(2);
    expect(overview.delegation_records.count).toBe(2);
    expect(overview.subagent_boundaries.count).toBe(2);
```

- [ ] **Step 2: db-import.test.ts「loadDatabase produces the full consumable snapshot」用例更新**

该用例现有断言 `expect(result.hasConsumableData).toBe(true);` 保持。`overview` 相关断言已在 Task 0.3 Step 3 追加。无需再改。

- [ ] **Step 3: monitoring-extractors.test.ts 计数更新**

`__tests__/monitoring-extractors.test.ts` 中以下断言更新（旧 → 新）：

```ts
    expect(r.count).toBe(4);    // mismatch（nld）：3 → 4
    ...
    expect(r.count).toBe(3);    // pollution：2 → 3
    ...
    expect(r.total).toBe(6);    // policy：3 → 6
    expect(r.violations).toBe(1); // 仅 pol-1 Violation（不变）
    ...
    expect(r.total).toBe(6);    // tls：4 → 6
    expect(r.trend.map((b) => b.count)).toEqual([3, 3]); // [2, 2] → [3, 3]（s-5/s-6 各入一桶）
```

> 逐行定位：`monitoring-extractors.test.ts` 中 mismatch `r.count`（第 38 行附近）、pollution `r.count`（第 49 行）、policy `r.total`（第 58 行）、tls `r.total`（第 68 行）、tls trend（第 70 行）。用 `grep -n "toBe\|toEqual" __tests__/monitoring-extractors.test.ts` 逐一定位替换。

- [ ] **Step 4: derive-monitoring.test.ts 注释同步（断言不动）**

`__tests__/derive-monitoring.test.ts` 中：
- 第 40 行注释 `// pollutionCount 2` → `// pollutionCount 3`
- 第 42 行注释 `// 1/3≈0.33 > 0.05` → `// 1/6≈0.17 > 0.05`

断言值**不改**（已实测核验：pollution 3>0 → 仍 warning；nld 4/intents 3 → density 1.33 ≤10 → 仍 normal；policy 1/6≈0.167 >0.05 → 仍 warning）。

- [ ] **Step 5: 全量测试验证**

Run: `cd frontend && npx vitest run 2>&1 | tail -30`
Expected: 全绿（此时 fixture 已扩展，所有消费方断言对齐）。若仍有红，用 `tail -80` 看具体失败项——多为 Step 1/3 遗漏的计数。

### Task 0.7: M0 收尾 — 四门全绿 + 验收

**Files:**
- Test: 全部

- [ ] **Step 1: 四门全绿**

Run（依次，全须 0 错误/0 警告/全过）：

```bash
cd frontend && npm run type-check
cd frontend && npm run lint
cd frontend && npx vitest run
cd frontend && npm run build
```

Expected: type-check 0 错误；lint 0 错误 0 警告；vitest 全过；build 通过。

- [ ] **Step 2: 提交（停下验收）**

```bash
git add frontend/lib/types.ts frontend/lib/api.ts frontend/lib/db-import.ts frontend/lib/store.ts \
        frontend/scripts/gen-db-fixture.mjs frontend/__tests__/fixtures/db-sample.sqlite \
        frontend/__tests__/db-import.test.ts frontend/__tests__/store-db.test.ts \
        frontend/__tests__/monitoring-extractors.test.ts frontend/__tests__/derive-monitoring.test.ts \
        docs/superpowers/archive/plans/2026-08-07-db-four-features.md
git commit -m "<commit message 由用户亲自定稿>"
```

**停下，向用户汇报 M0 完成**（提取器清单、fixture 行数、四门结果），等待用户定 commit message 后再进 M1。

---

## 里程碑 1 · 告警取证链（investigations 工作台）

### Task 1.1: `lib/forensic-chain.ts` — buildForensicChain 四区组装

**Files:**
- Create: `lib/forensic-chain.ts`
- Test: `__tests__/forensic-chain.test.ts`（新建）

**Interfaces:**
- Consumes: Task 0.1 类型（`NonLinkDiagnostic`/`PolicyEvaluation`/`ContextPollutionEvent`）、既有 `ResponsibilityAlert`/`ResponsibilityClaim`（types.ts）、`collectAlertEvidenceRefs`（lib/investigation.ts:49，组件层使用）
- Produces（供 Task 1.2 组件）：
  - `EngineVerdict { kind: 'rule' | 'pattern'; id: string; score: number; reason: string; targets: string[] }`
  - `PollutionChain { source_agent_id: string; contaminated_artifact: string; hops: string[]; detected_at_ns: number; dt_ns: number | null }`
  - `PolicyVerdictRow { boundary: string; result: string | null; confidence: number; reason: string }`
  - `ForensicChain { verdicts; pollutionChains; policyRows; diagnostics; claimRefs }`
  - `buildForensicChain(input: { alert; pollutionEvents; policyEvaluations; nonLinkDiagnostics; claims; intentIds: string[]; alertEvidenceRefs: string[] }): ForensicChain`

- [ ] **Step 1: 写失败测试 `__tests__/forensic-chain.test.ts`**

```ts
import { describe, expect, it } from 'vitest';
import { buildForensicChain } from '@/lib/forensic-chain';
import type {
  ContextPollutionEvent,
  NonLinkDiagnostic,
  PolicyEvaluation,
  ResponsibilityAlert,
  ResponsibilityClaim,
} from '@/lib/types';

const baseAlert = (over: Partial<ResponsibilityAlert> = {}): ResponsibilityAlert =>
  ({
    alert_id: 'alert-1',
    timestamp_ns: 1100,
    display_profile: 'UnsafeByEngine',
    decision: 'Unsafe',
    severity: 'Warning',
    confidence: 0.85,
    authority: 'Authoritative',
    reasoning: 'r',
    title: 't',
    summary: 's',
    action_hint: null,
    primary_agent: { agent_id: 'agent-a', claimed_role: null },
    involved_agents: [
      { agent_id: 'agent-a', claimed_role: null },
      { agent_id: 'agent-b', claimed_role: null },
    ],
    engine: {
      rule_verdicts: [
        { rule_id: 'R-BOUNDARY-1', description: 'Boundary crossing', matched_resource: '/tmp/x.sh', llm_agreed: null, llm_comment: null },
      ],
      pattern_verdicts: [
        { template_id: 'PT-DELEG', match_confidence: 0.8, llm_agreed: null, llm_comment: null },
      ],
      classification_score: 0.9,
      classification_route: 'engine_unsafe',
      score_breakdown: { rule_score: 1, pattern_score: 0, boundary_score: 0.9, evidence_score: 0.8, role_score: 0, repetition_score: 0, total: 0.9 },
    },
    timeline: { steps: [], total_duration_ms: 0 },
    graph_snapshot: { snapshot_id: '', timestamp_ns: 0, nodes: [], edges: [], metrics: { node_counts: {}, edge_counts: {}, agent_count: 0, cross_agent_edges: 0, delegation_depth: 0, taint_chains: 0, avg_confidence: 0 } },
    phase: { Provisional: { timestamp_ns: 1100 } },
    provisional_ref: null,
    ...over,
  });

const pollution = (over: Partial<ContextPollutionEvent>): ContextPollutionEvent => ({
  pollution_id: 1, source_agent_id: 'agent-a', contaminated_artifact: '/tmp/leaked.txt',
  propagation_path: [], detected_at_ns: 1000, run_id: 'run-1', ...over,
});

const policy = (over: Partial<PolicyEvaluation>): PolicyEvaluation => ({
  policy_id: 'pol-1', boundary: 'Capability', subject_agent_id: 'agent-a',
  judgment: { result: 'Violation' }, resource: null, evidence_refs: ['ev-1'],
  confidence: 0.85, reason: '', run_id: 'run-1', ...over,
});

const diag = (over: Partial<NonLinkDiagnostic>): NonLinkDiagnostic => ({
  diagnostic_id: 'nld-1', intent_id: 'int-1', action_id: null,
  reason: 'BelowConfidenceCutoff', timestamp_ns: 1000, run_id: 'run-1', ...over,
});

const claim = (over: Partial<ResponsibilityClaim> = {}): ResponsibilityClaim => ({
  claim_id: 'claim-1', claim_type: 'TaintedContextUse', subject_agent_id: 'agent-a',
  policy_id: 'POLICY-1', boundary_id: 'BOUNDARY-1', claim_state: 'Inferred',
  severity: 'Critical', confidence: 0.9, evidence_refs: ['ev-1', 'ev-2'],
  competing_explanations: [], traceback_ref: null,
  phase: { Provisional: { timestamp_ns: 1000 } }, timestamp_ns: 1000, ...over,
});

describe('buildForensicChain', () => {
  it('四区组装：判定（rule/pattern）、污染（交集）、策略（交集）、诊断（intentIds）、claimRefs（证据交集）', () => {
    const chain = buildForensicChain({
      alert: baseAlert(),
      pollutionEvents: [pollution({}), pollution({ source_agent_id: 'agent-x' })],
      policyEvaluations: [policy({}), policy({ subject_agent_id: 'agent-x' })],
      nonLinkDiagnostics: [diag({}), diag({ intent_id: 'int-99' })],
      claims: [claim(), claim({ evidence_refs: ['ev-9'] })],
      intentIds: ['int-1'],
      alertEvidenceRefs: ['ev-1', 'ev-2'],
    });

    // 判定：rule 用 matched_resource 作 targets，reason=description；pattern 无 reason/targets
    expect(chain.verdicts).toHaveLength(2);
    expect(chain.verdicts[0]).toEqual({
      kind: 'rule', id: 'R-BOUNDARY-1', score: 1,
      reason: 'Boundary crossing', targets: ['/tmp/x.sh'],
    });
    expect(chain.verdicts[1]).toEqual({
      kind: 'pattern', id: 'PT-DELEG', score: 0.8, reason: '', targets: [],
    });

    // 污染：仅 involved_agents 交集内的 source 命中
    expect(chain.pollutionChains).toHaveLength(1);
    expect(chain.pollutionChains[0].source_agent_id).toBe('agent-a');
    // dt_ns 无可算来源 → null（不幻觉）
    expect(chain.pollutionChains[0].dt_ns).toBeNull();

    // 策略：仅 involved_agents 交集内的 subject 命中
    expect(chain.policyRows).toHaveLength(1);
    expect(chain.policyRows[0].boundary).toBe('Capability');
    expect(chain.policyRows[0].result).toBe('Violation');

    // 诊断：仅 intent_id ∈ intentIds
    expect(chain.diagnostics).toHaveLength(1);
    expect(chain.diagnostics[0].diagnostic_id).toBe('nld-1');

    // claimRefs：claims evidence_refs ∩ alertEvidenceRefs
    expect(chain.claimRefs).toEqual(['ev-1', 'ev-2']);
  });

  it('空源诚实：无 pollution / 无交集 → 空数组', () => {
    const chain = buildForensicChain({
      alert: baseAlert(),
      pollutionEvents: [],
      policyEvaluations: [],
      nonLinkDiagnostics: [],
      claims: [],
      intentIds: [],
      alertEvidenceRefs: [],
    });
    expect(chain.verdicts).toHaveLength(2); // 判定恒来自 alert.engine
    expect(chain.pollutionChains).toEqual([]);
    expect(chain.policyRows).toEqual([]);
    expect(chain.diagnostics).toEqual([]);
    expect(chain.claimRefs).toEqual([]);
  });

  it('pattern 无描述字段 → reason 空串；rule 无 matched_resource → targets 空数组', () => {
    const chain = buildForensicChain({
      alert: baseAlert({
        engine: {
          ...baseAlert().engine,
          rule_verdicts: [{ rule_id: 'R-X', description: '', matched_resource: '', llm_agreed: null, llm_comment: null }],
          pattern_verdicts: [],
        },
      }),
      pollutionEvents: [], policyEvaluations: [], nonLinkDiagnostics: [], claims: [],
      intentIds: [], alertEvidenceRefs: [],
    });
    expect(chain.verdicts[0].targets).toEqual([]);
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/forensic-chain.test.ts 2>&1 | tail -15`
Expected: FAIL — `Cannot find module '@/lib/forensic-chain'`。

- [ ] **Step 3: 写实现 `lib/forensic-chain.ts`**

```ts
import type {
  ContextPollutionEvent,
  NonLinkDiagnostic,
  PolicyEvaluation,
  ResponsibilityAlert,
  ResponsibilityClaim,
} from './types';

// ═══ 告警取证链（M1）——纯函数派生，无造数 ═══

export interface EngineVerdict {
  kind: 'rule' | 'pattern';
  id: string;                // rule_id / template_id
  score: number;
  reason: string;            // 从 verdict 结构尽力提取；无 → ''
  targets: string[];         // rule 的 matched_resource；PatternVerdict 无此字段 → 恒 []
}

export interface PollutionChain {
  source_agent_id: string;
  contaminated_artifact: string;
  hops: string[];            // propagation_path；写者 → 中间 → 读者
  detected_at_ns: number;
  /** 表无逐 hop 时间戳，时延不可算 → 恒 null（不幻觉）。 */
  dt_ns: number | null;
}

export interface PolicyVerdictRow {
  boundary: string;
  result: string | null;     // 'Violation' | 'Allow' | judgment 缺省 → null
  confidence: number;
  reason: string;
}

export interface ForensicChain {
  verdicts: EngineVerdict[];             // alert.engine.rule_verdicts + pattern_verdicts
  pollutionChains: PollutionChain[];     // 按 involved_agents ∩ source_agent_id
  policyRows: PolicyVerdictRow[];        // 按 subject_agent_id ∈ involved_agents
  diagnostics: NonLinkDiagnostic[];      // 按 intent_id ∈ intentIds
  claimRefs: string[];                   // claims evidence_refs ∩ alertEvidenceRefs
}

/** involved_agents 的 agent_id 集合（含 primary）。弱关联匹配用。 */
function involvedAgentSet(alert: ResponsibilityAlert): Set<string> {
  const set = new Set<string>();
  if (alert.primary_agent?.agent_id) set.add(alert.primary_agent.agent_id);
  for (const agent of alert.involved_agents ?? []) {
    if (agent.agent_id) set.add(agent.agent_id);
  }
  return set;
}

export function buildForensicChain(input: {
  alert: ResponsibilityAlert;
  pollutionEvents: ContextPollutionEvent[];
  policyEvaluations: PolicyEvaluation[];
  nonLinkDiagnostics: NonLinkDiagnostic[];
  claims: ResponsibilityClaim[];
  /** alert 证据关联的 intent_id（组件层由 collectAlertEvidenceRefs + evidence 桥接）。 */
  intentIds: string[];
  /** alert 证据引用（组件层 collectAlertEvidenceRefs 计算）。 */
  alertEvidenceRefs: string[];
}): ForensicChain {
  const { alert, pollutionEvents, policyEvaluations, nonLinkDiagnostics, claims, intentIds, alertEvidenceRefs } = input;
  const involved = involvedAgentSet(alert);
  const intentIdSet = new Set(intentIds);
  const evidenceRefSet = new Set(alertEvidenceRefs);

  const verdicts: EngineVerdict[] = [
    ...(alert.engine?.rule_verdicts ?? []).map((v) => ({
      kind: 'rule' as const,
      id: v.rule_id,
      score: 1, // 命中即 1（rule_verdicts 无 score 字段）
      reason: v.description ?? '',
      targets: v.matched_resource ? [v.matched_resource] : [],
    })),
    ...(alert.engine?.pattern_verdicts ?? []).map((v) => ({
      kind: 'pattern' as const,
      id: v.template_id,
      score: v.match_confidence ?? 0,
      reason: '', // PatternVerdict 无描述字段
      targets: [],
    })),
  ];

  const pollutionChains: PollutionChain[] = pollutionEvents
    .filter((p) => involved.has(p.source_agent_id))
    .map((p) => ({
      source_agent_id: p.source_agent_id,
      contaminated_artifact: p.contaminated_artifact,
      hops: p.propagation_path,
      detected_at_ns: p.detected_at_ns,
      dt_ns: null,
    }));

  const policyRows: PolicyVerdictRow[] = policyEvaluations
    .filter((p) => involved.has(p.subject_agent_id))
    .map((p) => ({
      boundary: p.boundary,
      result: p.judgment?.result ?? null,
      confidence: p.confidence,
      reason: p.reason,
    }));

  const diagnostics = nonLinkDiagnostics.filter((d) => d.intent_id != null && intentIdSet.has(d.intent_id));

  const claimRefSet = new Set<string>();
  for (const c of claims) {
    for (const ref of c.evidence_refs ?? []) {
      if (evidenceRefSet.has(ref)) claimRefSet.add(ref);
    }
  }

  return {
    verdicts,
    pollutionChains,
    policyRows,
    diagnostics,
    claimRefs: Array.from(claimRefSet).sort(),
  };
}
```

- [ ] **Step 4: 运行验证通过**

Run: `cd frontend && npx vitest run __tests__/forensic-chain.test.ts 2>&1 | tail -15`
Expected: PASS（3 用例）。

- [ ] **Step 5: 轻量自检**

`grep -n "dt_ns\|targets" lib/forensic-chain.ts`：dt_ns 恒 null 分支有注释说明；targets 的空数组分支有注释。无未处理的 `!` 断言。

### Task 1.2: `components/investigation/forensic-chain.tsx` + workbench 集成

**Files:**
- Create: `components/investigation/forensic-chain.tsx`
- Modify: `components/investigation/investigation-workbench.tsx`（「责任声明」SectionPanel 之后、约 280 行后插入取证链面板）
- Test: `__tests__/investigation-workbench.test.tsx`（追加 1 个用例验证取证链面板渲染；需 mock 无 db 数据时的空态）

**Interfaces:**
- Consumes: `buildForensicChain`（Task 1.1）、store 的 `dbAssets`/`claims`/`evidence`、`collectAlertEvidenceRefs`（lib/investigation.ts:49）、`SectionPanel`（components/ui/linear/section-panel.tsx）
- Produces: 取证链四子区渲染（引擎判定 grid / 污染传播链 / 策略矩阵 / 失配诊断列表）

- [ ] **Step 1: 写失败测试**

`__tests__/investigation-workbench.test.tsx` 追加（沿用该文件现有 mock 模式——若已有 `vi.mock('@/lib/store')` 则复用，先读该文件确认 store 取值方式再写）：

```tsx
  it('渲染取证链面板（引擎判定 + 污染链 + 策略矩阵 + 诊断），空源显示空态', async () => {
    // 前提：workbench 已 mock store；dbAssets 为空数组时取证链四区显示「无」而非伪造
    render(<InvestigationWorkbench />);
    // 取证链面板标题（SectionPanel title="取证链"）在 mock 数据下渲染
    expect(await screen.findByText('取证链')).toBeTruthy();
    // 空源空态：污染链区显示「无」而非列表
    expect(screen.getByText(/无污染传播/i)).toBeTruthy();
  });
```

> **注意**：workbench 测试当前依赖 demo/sample 数据（无 dbAssets），取证链在 dbAssets 为 null 时应渲染空态。组件必须处理 `dbAssets === null`（显示「导入含进程/策略表的 .db 后此处有数据」空态）。若现有测试文件结构与本例不符，先 Read 该文件，按其 mock 惯例写断言。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/investigation-workbench.test.tsx 2>&1 | tail -15`
Expected: FAIL — 找不到「取证链」文案。

- [ ] **Step 3: 写实现 `components/investigation/forensic-chain.tsx`**

组件职责（纯展示，数据由 props 传入；所有派生在 `buildForensicChain`）：

```tsx
'use client';

import { useMemo } from 'react';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { collectAlertEvidenceRefs } from '@/lib/investigation';
import { buildForensicChain } from '@/lib/forensic-chain';
import type {
  ContextPollutionEvent,
  CorrelationEvidence,
  NonLinkDiagnostic,
  PolicyEvaluation,
  ResponsibilityAlert,
  ResponsibilityClaim,
} from '@/lib/types';

export type ForensicChainProps = {
  alert: ResponsibilityAlert;
  pollutionEvents: ContextPollutionEvent[];
  policyEvaluations: PolicyEvaluation[];
  nonLinkDiagnostics: NonLinkDiagnostic[];
  claims: ResponsibilityClaim[];
  evidence: CorrelationEvidence[];
  fallbackGraph: { nodes: unknown[]; edges: unknown[] };
};
```

> 组件内部：`alertEvidenceRefs = collectAlertEvidenceRefs(alert, fallbackGraph)`；`intentIds = evidence.filter(e => alertEvidenceRefs.includes(e.evidence_id)).map(e => e.intent_id)`；再 `buildForensicChain`。四个子区渲染：
> 1. **引擎判定**：verdicts.map 卡片（kind 徽章 + id mono + score 进度条 + reason；rule 显示 targets 摘要）
> 2. **污染传播链**：hops 线性链（`写者 → 中间 → 读者` 编号+连接线），头部 source agent + artifact，尾部 detected 时间；`hops` 空时显示 artifact 单点
> 3. **策略判定**：boundary × result 矩阵行（Violation 红 / Allow 绿 / null 灰 + confidence + reason）
> 4. **失配诊断**：diagnostics.map 列表（reason + intent 关联 + timestamp）
> 空态纪律：某源数组为空 → 该子区显示「无」，不伪造。dbAssets 整体 null（非 db 数据源）→ 面板顶部小字「导入含进程/策略表的 .db 后此处有数据」。
> 样式遵循现有 SectionPanel 容器与 `ln-*` 类；判定卡用 `grid`，污染链用 flex + `—→` 连接符，矩阵用 rows。

- [ ] **Step 4: workbench 集成**

`investigation-workbench.tsx` 中，在「责任声明」SectionPanel（`</SectionPanel>` 收尾于 ~280 行）与「判决」SectionPanel（~286 行）之间插入：

```tsx
          <SectionPanel eyebrow="取证链" title={chain ? `${chain.verdicts.length} 判定 / ${chain.policyRows.length} 策略` : '待 db 数据'} bodyClassName="ln-panel-body">
            <ForensicChain
              alert={alert}
              pollutionEvents={dbAssets?.pollutionEvents ?? []}
              policyEvaluations={dbAssets?.policyEvaluations ?? []}
              nonLinkDiagnostics={dbAssets?.nonLinkDiagnostics ?? []}
              claims={claims}
              evidence={evidence}
              fallbackGraph={graph}
            />
          </SectionPanel>
```

配套：workbench 顶部新增 `import ForensicChain from '@/components/investigation/forensic-chain';`、`import { useAppStore } from '@/lib/store';`（若未引入）取 `dbAssets`；`alert`/`claims`/`evidence`/`graph` 为 workbench 现有 props/state（按该文件实际命名对齐）。

- [ ] **Step 5: 运行测试**

Run: `cd frontend && npx vitest run __tests__/investigation-workbench.test.tsx __tests__/forensic-chain.test.ts 2>&1 | tail -15`
Expected: PASS。

- [ ] **Step 6: 四门全绿 + 验收**

Run: `cd frontend && npm run type-check && npm run lint && npx vitest run && npm run build`
Expected: 四门全绿。然后停下，向用户汇报 M1，等 commit message。

---

## 里程碑 2 · 身份血缘（overview）

### Task 2.1: `lib/identity-lineage.ts` — 进程谱系树 + 家族树

**Files:**
- Create: `lib/identity-lineage.ts`
- Test: `__tests__/identity-lineage.test.ts`（新建）

**Interfaces:**
- Consumes: Task 0.1 类型（`ProvisionalIdentity`/`AgentIdentity`/`IdentityProcessMapEntry`/`ProcessEvent`/`SubagentBoundary`）
- Produces（供 Task 2.2 组件）：
  - `ProcessTreeNode { pid; start_time_ns; comm; agentId: string | null; children: ProcessTreeNode[] }`
  - `AgentProcessTree { agentId; primarySignal: string | null; confidence: number | null; status: 'provisional' | 'authoritative'; roots: ProcessTreeNode[]; orphanPids: number[] }`
  - `AgentFamilyNode { agentId; children: Array<{ childAgentId; reason; childPid }> }`
  - `buildAgentProcessTrees(input): Map<string, AgentProcessTree>`
  - `buildAgentFamilyTree(subagentBoundaries): Map<string, AgentFamilyNode>`

- [ ] **Step 1: 写失败测试 `__tests__/identity-lineage.test.ts`**

用 Task 0.5 fixture 的真实数据形态构造输入：

```ts
import { describe, expect, it } from 'vitest';
import { buildAgentFamilyTree, buildAgentProcessTrees } from '@/lib/identity-lineage';
import type { AgentIdentity, IdentityProcessMapEntry, ProcessEvent, ProvisionalIdentity, SubagentBoundary } from '@/lib/types';

const provisional: ProvisionalIdentity[] = [
  { agent_id: 'agent-a', process_ids: [{ pid: 42, start_time_ns: 1000 }, { pid: 102, start_time_ns: 1000 }, { pid: 103, start_time_ns: 1000 }], primary_signal: 'TlsSessionNew', confidence: 0.92, run_id: 'run-1' },
  { agent_id: 'agent-b', process_ids: [{ pid: 43, start_time_ns: 1000 }, { pid: 202, start_time_ns: 1000 }, { pid: 203, start_time_ns: 1000 }], primary_signal: 'ProcessEvent', confidence: 0.85, run_id: 'run-1' },
  { agent_id: 'agent-c', process_ids: [{ pid: 400, start_time_ns: 5000 }], primary_signal: 'TlsSessionNew', confidence: 0.6, run_id: 'run-1' },
];
const authoritative: AgentIdentity[] = [
  { agent_id: 'agent-a', process_ids: [{ pid: 42, start_time_ns: 1000 }, { pid: 102, start_time_ns: 1000 }, { pid: 103, start_time_ns: 1000 }], message_ids: ['msg-1'], tls_sessions: ['s-1'], confidence: 'Authoritative', provisional_refs: ['agent-a'], run_id: 'run-1' },
];
const map: IdentityProcessMapEntry[] = [
  { agent_id: 'agent-a', pid: 42, start_time_ns: 1000 },
  { agent_id: 'agent-a', pid: 102, start_time_ns: 1000 },
  { agent_id: 'agent-a', pid: 103, start_time_ns: 1000 },
  { agent_id: 'agent-a', pid: 99, start_time_ns: 900 }, // orphanPid：无 process_events 记录
  { agent_id: 'agent-b', pid: 43, start_time_ns: 1000 },
  { agent_id: 'agent-b', pid: 202, start_time_ns: 1000 },
  { agent_id: 'agent-b', pid: 203, start_time_ns: 1000 },
];
const events: ProcessEvent[] = [
  { process_event_id: 1, pid: 42, start_time_ns: 1000, parent_pid: null, parent_start_time_ns: null, comm: 'claude', timestamp_ns: 1000, agent_id: 'agent-a' },
  { process_event_id: 2, pid: 102, start_time_ns: 1000, parent_pid: 42, parent_start_time_ns: 1000, comm: 'bash', timestamp_ns: 1001, agent_id: 'agent-a' },
  { process_event_id: 3, pid: 103, start_time_ns: 1000, parent_pid: 102, parent_start_time_ns: 1000, comm: 'sh', timestamp_ns: 1002, agent_id: 'agent-a' },
  { process_event_id: 4, pid: 43, start_time_ns: 1000, parent_pid: null, parent_start_time_ns: null, comm: 'claude', timestamp_ns: 1000, agent_id: 'agent-b' },
  { process_event_id: 5, pid: 202, start_time_ns: 1000, parent_pid: 43, parent_start_time_ns: 1000, comm: 'node', timestamp_ns: 2000, agent_id: 'agent-b' },
  { process_event_id: 6, pid: 203, start_time_ns: 1000, parent_pid: 202, parent_start_time_ns: 1000, comm: 'script', timestamp_ns: 2001, agent_id: 'agent-b' },
  { process_event_id: 7, pid: 300, start_time_ns: 4000, parent_pid: null, parent_start_time_ns: null, comm: 'orphan-tool', timestamp_ns: 4000, agent_id: null },
];

describe('buildAgentProcessTrees', () => {
  it('按 agent 组进程树（父子嵌套 + 跨 agent 父挂根）+ status/primarySignal/orphanPids', () => {
    const trees = buildAgentProcessTrees({ provisionalIdentities: provisional, agentIdentities: authoritative, identityProcessMap: map, processEvents: events });

    // agent-a：升级（provisional ∩ authoritative）→ status authoritative
    const a = trees.get('agent-a')!;
    expect(a.status).toBe('authoritative');
    expect(a.primarySignal).toBe('TlsSessionNew');
    expect(a.confidence).toBe(0.92);
    expect(a.orphanPids).toEqual([99]);
    // 树根 = pid 42（claude），子 = 102（bash），孙 = 103（sh）
    expect(a.roots).toHaveLength(1);
    expect(a.roots[0]).toMatchObject({ pid: 42, comm: 'claude' });
    expect(a.roots[0].children).toHaveLength(1);
    expect(a.roots[0].children[0]).toMatchObject({ pid: 102, comm: 'bash' });
    expect(a.roots[0].children[0].children[0].pid).toBe(103);

    // agent-c：仅 provisional → status provisional，无进程事件记录 → roots 空 + orphanPids 空
    const c = trees.get('agent-c')!;
    expect(c.status).toBe('provisional');
    expect(c.roots).toEqual([]);
    expect(c.orphanPids).toEqual([]);

    // agent-b：status provisional（不在 authoritative）
    expect(trees.get('agent-b')!.status).toBe('provisional');
    expect(trees.get('agent-b')!.roots[0].pid).toBe(43);
  });

  it('跨 agent 父进程挂根：父不在该 agent 的 identity_map 时仍组树（父子关系来自 process_events）', () => {
    // 构造：agent-z 的 pid 900，父 901（agent-z 无父记录但 process_events 有父子）
    const trees = buildAgentProcessTrees({
      provisionalIdentities: [{ agent_id: 'agent-z', process_ids: [{ pid: 900, start_time_ns: 1 }], primary_signal: 'ProcessEvent', confidence: 0.5, run_id: 'run-1' }],
      agentIdentities: [],
      identityProcessMap: [{ agent_id: 'agent-z', pid: 900, start_time_ns: 1 }],
      processEvents: [
        { process_event_id: 1, pid: 900, start_time_ns: 1, parent_pid: null, parent_start_time_ns: null, comm: 'child', timestamp_ns: 1, agent_id: 'agent-z' },
      ],
    });
    expect(trees.get('agent-z')!.roots).toHaveLength(1);
    expect(trees.get('agent-z')!.roots[0].pid).toBe(900);
  });

  it('空数据 → 空 Map', () => {
    const trees = buildAgentProcessTrees({ provisionalIdentities: [], agentIdentities: [], identityProcessMap: [], processEvents: [] });
    expect(trees.size).toBe(0);
  });
});

describe('buildAgentFamilyTree', () => {
  it('由 subagent_boundaries 组 parent→child 委派树', () => {
    const boundaries: SubagentBoundary[] = [
      { boundary_id: 'bnd-1', parent_agent_id: 'agent-a', child_agent_id: 'agent-b', parent_pid: 42, parent_start_time_ns: 1000, child_pid: 43, child_start_time_ns: 1000, reason: 'delegation', run_id: 'run-1' },
      { boundary_id: 'bnd-2', parent_agent_id: 'agent-b', child_agent_id: 'agent-c', parent_pid: 43, parent_start_time_ns: 1000, child_pid: 400, child_start_time_ns: 5000, reason: 'subagent spawn', run_id: 'run-1' },
    ];
    const tree = buildAgentFamilyTree(boundaries);
    expect(tree.get('agent-a')!.children).toEqual([{ childAgentId: 'agent-b', reason: 'delegation', childPid: 43 }]);
    expect(tree.get('agent-b')!.children).toEqual([{ childAgentId: 'agent-c', reason: 'subagent spawn', childPid: 400 }]);
  });

  it('空边界 → 空 Map', () => {
    expect(buildAgentFamilyTree([]).size).toBe(0);
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/identity-lineage.test.ts 2>&1 | tail -15`
Expected: FAIL — `Cannot find module '@/lib/identity-lineage'`。

- [ ] **Step 3: 写实现 `lib/identity-lineage.ts`**

```ts
import type {
  AgentIdentity,
  IdentityProcessMapEntry,
  ProcessEvent,
  ProvisionalIdentity,
  SubagentBoundary,
} from './types';

// ═══ 身份血缘（M2）——纯函数派生进程谱系树与委派家族树，无造数 ═══

export interface ProcessTreeNode {
  pid: number;
  start_time_ns: number;
  comm: string;
  agentId: string | null;
  children: ProcessTreeNode[];
}

export interface AgentProcessTree {
  agentId: string;
  primarySignal: string | null;    // provisional.primary_signal
  confidence: number | null;       // provisional.confidence
  status: 'provisional' | 'authoritative'; // 是否出现在 agent_identities
  roots: ProcessTreeNode[];        // 该 agent 的进程树
  orphanPids: number[];            // identity_map 有但 process_events 无记录的 pid
}

export interface AgentFamilyNode {
  agentId: string;
  children: Array<{ childAgentId: string; reason: string; childPid: number }>;
}

/** pid + start_time_ns 组合键。 */
function processKey(pid: number, start_time_ns: number): string {
  return `${pid}:${start_time_ns}`;
}

export function buildAgentProcessTrees(input: {
  provisionalIdentities: ProvisionalIdentity[];
  agentIdentities: AgentIdentity[];
  identityProcessMap: IdentityProcessMapEntry[];
  processEvents: ProcessEvent[];
}): Map<string, AgentProcessTree> {
  const { provisionalIdentities, agentIdentities, identityProcessMap, processEvents } = input;
  const authoritativeSet = new Set(agentIdentities.map((a) => a.agent_id));

  // process_events 索引：进程键 → 事件（父子关系唯一来源）
  const eventByKey = new Map<string, ProcessEvent>();
  for (const e of processEvents) eventByKey.set(processKey(e.pid, e.start_time_ns), e);

  // 每个 agent 的 identity_map pid 集合
  const mapByAgent = new Map<string, { pid: number; start_time_ns: number }[]>();
  for (const m of identityProcessMap) {
    const list = mapByAgent.get(m.agent_id) ?? [];
    list.push({ pid: m.pid, start_time_ns: m.start_time_ns });
    mapByAgent.set(m.agent_id, list);
  }

  const trees = new Map<string, AgentProcessTree>();
  // 遍历 provisional（含 agent_identities 的升级交集通过 status 体现；authoritative-only 也纳入）
  const allAgents = new Set<string>([
    ...provisionalIdentities.map((p) => p.agent_id),
    ...agentIdentities.map((a) => a.agent_id),
  ]);

  for (const agentId of allAgents) {
    const prov = provisionalIdentities.find((p) => p.agent_id === agentId);
    const mapPids = mapByAgent.get(agentId) ?? [];

    // 该 agent 有 process_events 记录的 pid（保留 identity_map 的归属优先）
    const ownedKeys = new Set(mapPids.map((m) => processKey(m.pid, m.start_time_ns)));
    const orphanPids: number[] = [];
    const rootKeys = new Set<string>();

    for (const m of mapPids) {
      const key = processKey(m.pid, m.start_time_ns);
      const ev = eventByKey.get(key);
      if (!ev) {
        orphanPids.push(m.pid); // identity_map 有、process_events 无 → orphan
        continue;
      }
      const parentKey = ev.parent_pid != null ? processKey(ev.parent_pid, ev.parent_start_time_ns ?? 0) : null;
      // 父进程不在该 agent 的 identity_map（跨 agent / 无归属）→ 本进程作根
      if (parentKey == null || !ownedKeys.has(parentKey)) rootKeys.add(key);
    }

    const buildNode = (key: string): ProcessTreeNode | null => {
      const ev = eventByKey.get(key);
      if (!ev) return null;
      const children: ProcessTreeNode[] = [];
      // 子进程 = 以本进程为父、且属于该 agent 的 identity_map pid
      for (const m of mapPids) {
        const ckey = processKey(m.pid, m.start_time_ns);
        const cev = eventByKey.get(ckey);
        if (cev && cev.parent_pid === ev.pid && cev.parent_start_time_ns === ev.start_time_ns) {
          const child = buildNode(ckey);
          if (child) children.push(child);
        }
      }
      children.sort((a, b) => a.start_time_ns - b.start_time_ns || a.pid - b.pid);
      return {
        pid: ev.pid,
        start_time_ns: ev.start_time_ns,
        comm: ev.comm,
        agentId: ev.agent_id,
        children,
      };
    };

    const roots: ProcessTreeNode[] = [];
    for (const key of rootKeys) {
      const node = buildNode(key);
      if (node) roots.push(node);
    }
    roots.sort((a, b) => a.start_time_ns - b.start_time_ns || a.pid - b.pid);

    trees.set(agentId, {
      agentId,
      primarySignal: prov?.primary_signal ?? null,
      confidence: prov?.confidence ?? null,
      status: authoritativeSet.has(agentId) ? 'authoritative' : 'provisional',
      roots,
      orphanPids,
    });
  }

  return trees;
}
```

> **排序实现说明**：上面 `roots.sort((a, b) => a.timestampNsSort(b))` 为示意——`ProcessTreeNode` 无该方法。实际排序直接 `roots.sort((a, b) => a.start_time_ns - b.start_time_ns || a.pid - b.pid)`；children 在 `buildNode` 递归完成后同样按 `start_time_ns` 排序（在 push 完 children 后 `children.sort(...)`）。

```ts
export function buildAgentFamilyTree(subagentBoundaries: SubagentBoundary[]): Map<string, AgentFamilyNode> {
  const tree = new Map<string, AgentFamilyNode>();
  for (const b of subagentBoundaries) {
    const node = tree.get(b.parent_agent_id) ?? { agentId: b.parent_agent_id, children: [] };
    node.children.push({ childAgentId: b.child_agent_id, reason: b.reason, childPid: b.child_pid });
    tree.set(b.parent_agent_id, node);
    // 无子节点的子 agent 也登记为空节点（渲染时可展开显示「无委派」）
    if (!tree.has(b.child_agent_id)) tree.set(b.child_agent_id, { agentId: b.child_agent_id, children: [] });
  }
  return tree;
}
```

- [ ] **Step 4: 运行验证通过**

Run: `cd frontend && npx vitest run __tests__/identity-lineage.test.ts 2>&1 | tail -15`
Expected: PASS（4 用例）。

### Task 2.2: `agent-process-tree.tsx` / `agent-family-tree.tsx` + overview 集成

**Files:**
- Create: `components/overview/agent-process-tree.tsx`
- Create: `components/overview/agent-family-tree.tsx`
- Modify: `components/overview/overview-page-client.tsx`（`MonitoringSection`/`OverviewDatasetPanel` 附近插入「进程谱系」面板，~549-552 行区域）
- Test: `__tests__/overview-page-client.test.tsx`（追加 1 个用例，验证空态渲染）

**Interfaces:**
- Consumes: `buildAgentProcessTrees`/`buildAgentFamilyTree`（Task 2.1）、store 的 `dbAssets`、`SectionPanel`
- Produces: 进程谱系面板（agent 列表 → 点选展开进程树）+ 委派家族树面板

- [ ] **Step 1: 写失败测试**

`__tests__/overview-page-client.test.tsx` 追加（沿用该文件 mock 惯例；overview 页 mock store，dbAssets 为 null → 空态）：

```tsx
  it('dbAssets 为空时进程谱系面板显示空态而非报错', async () => {
    render(<OverviewPageClient />);
    expect(await screen.findByText(/进程谱系/i)).toBeTruthy();
    expect(screen.getByText(/导入含进程表的 \.db 后此处有数据/i)).toBeTruthy();
  });
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/overview-page-client.test.tsx 2>&1 | tail -15`
Expected: FAIL — 找不到「进程谱系」。

- [ ] **Step 3: 写实现组件**

`components/overview/agent-process-tree.tsx`：

```tsx
'use client';

import { useMemo, useState } from 'react';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { buildAgentProcessTrees, type ProcessTreeNode } from '@/lib/identity-lineage';
import type { AgentIdentity, IdentityProcessMapEntry, ProcessEvent, ProvisionalIdentity } from '@/lib/types';

export type AgentProcessTreeProps = {
  provisionalIdentities: ProvisionalIdentity[];
  agentIdentities: AgentIdentity[];
  identityProcessMap: IdentityProcessMapEntry[];
  processEvents: ProcessEvent[];
  /** cap 截断时由调用方传入，用于「已展示 X / total」标注。 */
  processEventCount?: number;
  processEventTruncated?: boolean;
};
```

> 组件内部：`useMemo` 调 `buildAgentProcessTrees`；顶部 agent 列表（每行：provisional 徽章 / authoritative 徽章 + primarySignal + confidence）；点选展开该 agent 的进程树（**默认折叠，展开按需渲染**，防嵌套爆炸）；`ProcessTree` 子组件递归渲染 `roots`（节点显示 `pid comm` + agent 徽章）；有 `orphanPids.length > 0` 时显示「N 个孤儿 pid」角标；`processEventTruncated` 时头部显示「已展示 processEventCount / total」。空态：`dbAssets === null` 或全空 → SectionPanel 内「导入含进程表的 .db 后此处有数据」。

`components/overview/agent-family-tree.tsx`：

```tsx
'use client';

import { useMemo, useState } from 'react';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { buildAgentFamilyTree } from '@/lib/identity-lineage';
import type { SubagentBoundary } from '@/lib/types';

export type AgentFamilyTreeProps = {
  subagentBoundaries: SubagentBoundary[];
};
```

> 组件内部：`useMemo` 调 `buildAgentFamilyTree`；从根（无父节点）开始递归展开 parent→child 委派树（reason + childPid 标注），按需展开；**空态（真实 run 恒空）**：SectionPanel 内「此数据源无委派边界」。

- [ ] **Step 4: overview 集成**

`overview-page-client.tsx` 在 `OverviewDatasetPanel` 附近（`<MonitoringSection />` 与 `<OverviewDatasetPanel overview={overview} />` 之间）插入：

```tsx
            <AgentProcessTree
              provisionalIdentities={dbAssets?.provisionalIdentities ?? []}
              agentIdentities={dbAssets?.agentIdentities ?? []}
              identityProcessMap={dbAssets?.identityProcessMap ?? []}
              processEvents={dbAssets?.processEvents ?? []}
              processEventCount={dbAssets?.processEventCount}
              processEventTruncated={dbAssets?.processEventTruncated}
            />
            <AgentFamilyTree subagentBoundaries={dbAssets?.subagentBoundaries ?? []} />
```

配套：overview 页顶部新增 `import AgentProcessTree from '@/components/overview/agent-process-tree';`、`import AgentFamilyTree from '@/components/overview/agent-family-tree';`、`import { useAppStore } from '@/lib/store';` 取 `dbAssets`（若未引入 store）。

- [ ] **Step 5: 运行测试 + 四门**

Run: `cd frontend && npx vitest run __tests__/identity-lineage.test.ts __tests__/overview-page-client.test.tsx 2>&1 | tail -15`
Expected: PASS。然后 `npm run type-check && npm run lint && npx vitest run && npm run build` 全绿后停下验收。

---

## 里程碑 3 · 原始事件浏览器（侧边栏「事件」dock）

### Task 3.1: `lib/raw-event-browser.ts` — 归一化 + 多维度筛选

**Files:**
- Create: `lib/raw-event-browser.ts`
- Test: `__tests__/raw-event-browser.test.ts`（新建）

**Interfaces:**
- Consumes: Task 0.1 类型（`ProcessEvent`/`TlsSession`）、既有 `Intent`/`Action`（types.ts）
- Produces（供 Task 3.2 drawer 组件）：
  - `RawEventKind = 'process' | 'tls' | 'intent' | 'action'`
  - `RawEventRow { id; kind; timestampNs; agentId: string | null; title; detail; raw: unknown }`
  - `RawEventFilters { kinds: RawEventKind[]; agentId: string | null; text: string; sinceNs: number | null }`
  - `buildRawEventRows(input): RawEventRow[]`（按 timestampNs 升序）
  - `filterRawEventRows(rows, filters): RawEventRow[]`
  - `summarizeRawEvents(rows): Record<RawEventKind, number>`
  - `availableKindsForAgent(rows, agentId): RawEventKind[]`

- [ ] **Step 1: 写失败测试 `__tests__/raw-event-browser.test.ts`**

```ts
import { describe, expect, it } from 'vitest';
import { availableKindsForAgent, buildRawEventRows, filterRawEventRows, summarizeRawEvents } from '@/lib/raw-event-browser';
import type { Action, Intent, ProcessEvent, TlsSession } from '@/lib/types';

const processEvents: ProcessEvent[] = [
  { process_event_id: 1, pid: 42, start_time_ns: 1000, parent_pid: null, parent_start_time_ns: null, comm: 'claude', timestamp_ns: 1000, agent_id: 'agent-a' },
  { process_event_id: 2, pid: 300, start_time_ns: 4000, parent_pid: null, parent_start_time_ns: null, comm: 'orphan-tool', timestamp_ns: 4000, agent_id: null },
];
const tlsSessions: TlsSession[] = [
  { tls_session_id: 's-1', pid: 42, start_time_ns: 1000, timestamp_ns: 1000, agent_id: 'agent-a' },
  { tls_session_id: 's-5', pid: 44, start_time_ns: 1000, timestamp_ns: 1200, agent_id: null },
];
const intents: Intent[] = [
  { intent_id: 'int-1', process_id: { pid: 42, start_time_ns: 1000 }, message_id: 'msg-1', tool_name: 'Bash', tool_args: [{ Command: 'ls -la' }], intent_class: 'FileSystem', parse_state: 'Eligible', primary_matching_eligible: true, authority_source: 'SseResponseToolUse', timestamp_ns: 1000 },
];
const actions: Action[] = [
  { action_id: 'act-1', process_id: { pid: 42, start_time_ns: 1000 }, action_type: 'FileWrite', normalized_args: [{ Path: '/tmp/x.sh' }], derivation_mode: 'Direct', signal_class: 'HighSignal', timestamp_ns: 1002 },
];

describe('buildRawEventRows', () => {
  it('4 kind 归一化并按 timestampNs 升序', () => {
    const rows = buildRawEventRows({ processEvents, tlsSessions, intents, actions });
    expect(rows.map((r) => r.kind)).toEqual(['process', 'tls', 'intent', 'action', 'process']);
    expect(rows[0].id).toBe('process:42:1000');
    expect(rows[0].title).toBe('claude');
    expect(rows[0].agentId).toBe('agent-a');
    // tls 行 title = tls_session_id
    expect(rows[1].title).toBe('s-1');
    // intent 行 detail 含 tool_args 摘要（截断）
    expect(rows[3].title).toBe('Bash');
    // 未识别进程 agentId null
    expect(rows[4].agentId).toBeNull();
  });

  it('summarizeRawEvents 统计各 kind 数量', () => {
    const rows = buildRawEventRows({ processEvents, tlsSessions, intents, actions });
    expect(summarizeRawEvents(rows)).toEqual({ process: 2, tls: 1, intent: 1, action: 1 });
  });
});

describe('filterRawEventRows', () => {
  const rows = buildRawEventRows({ processEvents, tlsSessions, intents, actions });

  it('按 kind 过滤（空 = 全部）', () => {
    expect(filterRawEventRows(rows, { kinds: [], agentId: null, text: '', sinceNs: null })).toHaveLength(5);
    expect(filterRawEventRows(rows, { kinds: ['tls'], agentId: null, text: '', sinceNs: null })).toHaveLength(1);
  });

  it('按 agentId 过滤', () => {
    const out = filterRawEventRows(rows, { kinds: [], agentId: 'agent-a', text: '', sinceNs: null });
    expect(out.every((r) => r.agentId === 'agent-a')).toBe(true);
  });

  it('按文本过滤（title/detail/comm/args 命中）', () => {
    expect(filterRawEventRows(rows, { kinds: [], agentId: null, text: 'x.sh', sinceNs: null })).toHaveLength(1); // act-1 detail 含 /tmp/x.sh
    expect(filterRawEventRows(rows, { kinds: [], agentId: null, text: 'claude', sinceNs: null })).toHaveLength(1);
  });

  it('按时间范围过滤（sinceNs）', () => {
    expect(filterRawEventRows(rows, { kinds: [], agentId: null, text: '', sinceNs: 1200 })).toHaveLength(2); // ts 1200 + 4000
  });

  it('多维组合过滤', () => {
    const out = filterRawEventRows(rows, { kinds: ['process'], agentId: 'agent-a', text: '', sinceNs: 1000 });
    expect(out).toHaveLength(1);
    expect(out[0].kind).toBe('process');
  });
});

describe('availableKindsForAgent', () => {
  it('级联收窄：选 agent 后仅返回该 agent 实际有的 kind', () => {
    const rows = buildRawEventRows({ processEvents, tlsSessions, intents, actions });
    expect(availableKindsForAgent(rows, 'agent-a')).toEqual(['process', 'tls', 'intent', 'action']);
    expect(availableKindsForAgent(rows, null)).toEqual(['process', 'tls', 'intent', 'action']);
    expect(availableKindsForAgent(rows, 'agent-b')).toEqual([]); // agent-b 无事件
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/raw-event-browser.test.ts 2>&1 | tail -15`
Expected: FAIL — `Cannot find module '@/lib/raw-event-browser'`。

- [ ] **Step 3: 写实现 `lib/raw-event-browser.ts`**

```ts
import type { Action, Intent, ProcessEvent, TlsSession } from './types';

// ═══ 原始事件浏览器（M3）——纯函数归一化 + 筛选，无造数 ═══

export type RawEventKind = 'process' | 'tls' | 'intent' | 'action';

export interface RawEventRow {
  id: string;
  kind: RawEventKind;
  timestampNs: number;
  agentId: string | null;
  title: string;     // comm / tls_session_id / tool_name / action_type
  detail: string;    // args/路径 摘要（截断）
  raw: unknown;      // 展开时显示的结构化 JSON
}

export interface RawEventFilters {
  kinds: RawEventKind[];         // 空 = 全部
  agentId: string | null;        // 级联：选 agent → kinds 可用集合收窄
  text: string;                  // 防抖全文（title/detail/comm/args）
  sinceNs: number | null;        // 时间范围
}

const DETAIL_MAX = 80;

function truncateDetail(s: string): string {
  return s.length > DETAIL_MAX ? `${s.slice(0, DETAIL_MAX)}…` : s;
}

function argsToDetail(raw: unknown): string {
  try {
    const s = JSON.stringify(raw);
    return truncateDetail(s && s !== '[]' ? s : '');
  } catch {
    return '';
  }
}

export function buildRawEventRows(input: {
  processEvents: ProcessEvent[];
  tlsSessions: TlsSession[];
  intents: Intent[];
  actions: Action[];
}): RawEventRow[] {
  const { processEvents, tlsSessions, intents, actions } = input;
  const rows: RawEventRow[] = [];

  for (const p of processEvents) {
    rows.push({
      id: `process:${p.pid}:${p.start_time_ns}`,
      kind: 'process',
      timestampNs: p.timestamp_ns,
      agentId: p.agent_id,
      title: p.comm,
      detail: p.parent_pid != null ? `父 ${p.parent_pid} → pid ${p.pid}` : `pid ${p.pid}`,
      raw: p,
    });
  }

  for (const t of tlsSessions) {
    rows.push({
      id: `tls:${t.tls_session_id}`,
      kind: 'tls',
      timestampNs: t.timestamp_ns,
      agentId: t.agent_id,
      title: t.tls_session_id,
      detail: `pid ${t.pid} · TLS 会话`,
      raw: t,
    });
  }

  for (const i of intents) {
    rows.push({
      id: `intent:${i.intent_id}`,
      kind: 'intent',
      timestampNs: i.timestamp_ns,
      agentId: null, // intents 无 agent 标注（identity_process_map JOIN 只做 process/tls）
      title: i.tool_name,
      detail: argsToDetail(i.tool_args),
      raw: i,
    });
  }

  for (const a of actions) {
    rows.push({
      id: `action:${a.action_id}`,
      kind: 'action',
      timestampNs: a.timestamp_ns,
      agentId: null,
      title: a.action_type,
      detail: argsToDetail(a.normalized_args),
      raw: a,
    });
  }

  return rows.sort((a, b) => a.timestampNs - b.timestampNs);
}

export function filterRawEventRows(rows: RawEventRow[], filters: RawEventFilters): RawEventRow[] {
  const { kinds, agentId, text, sinceNs } = filters;
  const kindSet = new Set(kinds);
  const q = text.trim().toLowerCase();
  return rows.filter((r) => {
    if (kindSet.size > 0 && !kindSet.has(r.kind)) return false;
    if (agentId != null && r.agentId !== agentId) return false;
    if (sinceNs != null && r.timestampNs < sinceNs) return false;
    if (q) {
      const hay = `${r.title} ${r.detail} ${JSON.stringify(r.raw)}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

export function summarizeRawEvents(rows: RawEventRow[]): Record<RawEventKind, number> {
  const counts: Record<RawEventKind, number> = { process: 0, tls: 0, intent: 0, action: 0 };
  for (const r of rows) counts[r.kind] += 1;
  return counts;
}

export function availableKindsForAgent(rows: RawEventRow[], agentId: string | null): RawEventKind[] {
  const kinds = new Set<RawEventKind>();
  for (const r of rows) {
    if (agentId == null || r.agentId === agentId) kinds.add(r.kind);
  }
  // 稳定顺序（与 dock 徽章一致）
  return (['process', 'tls', 'intent', 'action'] as RawEventKind[]).filter((k) => kinds.has(k));
}
```

- [ ] **Step 4: 运行验证通过**

Run: `cd frontend && npx vitest run __tests__/raw-event-browser.test.ts 2>&1 | tail -15`
Expected: PASS（6 用例）。

### Task 3.2: `components/command-center/raw-event-browser.tsx` + incident-rail 集成

**Files:**
- Create: `components/command-center/raw-event-browser.tsx`
- Modify: `components/command-center/incident-rail.tsx`（事件 dock 的「下一步动作」BentoCard 区、~1135-1143 行，追加「原始事件流」按钮 + drawer 状态）
- Test: `__tests__/incident-rail-model.test.ts`（纯逻辑不受影响，无需改）；新增 `__tests__/raw-event-browser-drawer.test.tsx`（drawer 打开/关闭 + 筛选条渲染 + 空态）

**Interfaces:**
- Consumes: `buildRawEventRows`/`filterRawEventRows`/`summarizeRawEvents`/`availableKindsForAgent`（Task 3.1）、store 的 `dbAssets`、`VirtualList`（components/ui/virtual-list.tsx）、`AnimatePresence`/`motion`（incident-rail 已用）
- Produces: 右侧 720px drawer 覆盖层（事件流）

- [ ] **Step 1: 写失败测试 `__tests__/raw-event-browser-drawer.test.tsx`**

```tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import RawEventBrowser from '@/components/command-center/raw-event-browser';

// 组件直接收 props（dbAssets 事件数组），不依赖 store——测试最小
describe('RawEventBrowser drawer', () => {
  it('空数据源渲染空态而非报错', () => {
    render(<RawEventBrowser
      processEvents={[]} tlsSessions={[]} intents={[]} actions={[]}
      onClose={() => {}}
    />);
    expect(screen.getByText(/原始事件流/i)).toBeTruthy();
    expect(screen.getByText(/暂无事件/i)).toBeTruthy();
  });

  it('渲染 4 个 kind 徽章 + 计数；真实数据 0 的 kind 徽章隐藏', () => {
    const { container } = render(<RawEventBrowser
      processEvents={[
        { process_event_id: 1, pid: 42, start_time_ns: 1000, parent_pid: null, parent_start_time_ns: null, comm: 'claude', timestamp_ns: 1000, agent_id: null },
      ]}
      tlsSessions={[]} intents={[]} actions={[]}
      onClose={() => {}}
    />);
    // process 徽章有、其余 3 类徽章隐藏（诚实）
    expect(screen.getByRole('button', { name: /进程/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /TLS/ })).toBeNull();
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/raw-event-browser-drawer.test.tsx 2>&1 | tail -15`
Expected: FAIL — `Cannot find module '@/components/command-center/raw-event-browser'`。

- [ ] **Step 3: 写实现 drawer 组件**

`components/command-center/raw-event-browser.tsx` 职责与要点（样式沿用 incident-rail 的 `theaterSurfaceStyle`/`BentoCard` 视觉语言）：

```tsx
'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import VirtualList from '@/components/ui/virtual-list';
import { buildRawEventRows, filterRawEventRows, summarizeRawEvents, availableKindsForAgent, type RawEventKind } from '@/lib/raw-event-browser';
import type { Action, Intent, ProcessEvent, TlsSession } from '@/lib/types';

export type RawEventBrowserProps = {
  processEvents: ProcessEvent[];
  tlsSessions: TlsSession[];
  intents: Intent[];
  actions: Action[];
  onClose: () => void;
};
```

> 组件内部：
> - `useMemo(buildRawEventRows)` 全量行（升序）。
> - 状态：`activeKinds: RawEventKind[]`（空=全部）、`agentId: string | null`（null=全部）、`text`（输入框）、`sinceNs: number | null`（时间范围下拉：全部/1min/10min/1h，基于行内最大 ts）、`visibleCount`（初始 300，滚动近底 +300）、`expandedId: string | null`。
> - **级联纪律**：`availableKindsForAgent(rows, agentId)` 计算当前可选 kind；agent 选中后，`activeKinds` 自动收窄到该 agent 实际有的 kind（`useEffect` 同步，防「选 agent 后 kind 筛选结果为空」）。
> - 筛选条：kind pills（仅渲染有计数的 kind，`summarizeRawEvents`）+ agent 下拉（`availableKindsForAgent` 驱动）+ 文本输入（**350ms 防抖**，`useRef` 定时器）+ 时间范围。
> - 列表：`VirtualList` 渲染 `filtered.slice(0, visibleCount)`；`onScroll` 近底（`scrollHeight - scrollTop - clientHeight < 100`）`visibleCount += 300`；头部「已展示 X / total」。
> - 展开行：点击切换 `expandedId`，`<pre>` mono 显示 `JSON.stringify(row.raw, null, 2)`。
> - **过滤状态持久化**：`useEffect` 写 `localStorage['mta.raw-event-browser']`（activeKinds/agentId/text/sinceNs），初始化读回（try/catch 防御隐私模式）。
> - 抽屉：`motion.aside` 右侧 fixed `width: 720px`，`AnimatePresence` 进出场；顶部标题「原始事件流」+ 关闭按钮（`aria-label="关闭事件流"`）+ 计数徽章。
> - 空态：全行 0 → 居中「暂无事件」；filtered 0 →「无匹配事件（调整筛选）」。

- [ ] **Step 4: incident-rail 集成**

`incident-rail.tsx`：
1. 顶部 import 区追加 `import RawEventBrowser from '@/components/command-center/raw-event-browser';` + `import { useAppStore } from '@/lib/store';`（若未引入）。
2. 组件内部状态 `const [eventsOpen, setEventsOpen] = useState(false);`（放在 `activeTool` 状态附近）。
3. 事件 dock 的「下一步动作」BentoCard 三按钮区（~1135-1143 行）追加一个按钮（放在「主体画像」之前或之后均可）：

```tsx
                                  <button
                                    type="button"
                                    onClick={() => setEventsOpen(true)}
                                    className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white"
                                  >
                                    原始事件流 <Codicon name="list-unordered" size={12} />
                                  </button>
```

4. 在 `AnimatePresence` 内、dock 面板之后（或组件根部）渲染 drawer：

```tsx
      <AnimatePresence>
        {eventsOpen && (
          <RawEventBrowser
            processEvents={dbAssets?.processEvents ?? []}
            tlsSessions={dbAssets?.tlsSessions ?? []}
            intents={intents}
            actions={actions}
            onClose={() => setEventsOpen(false)}
          />
        )}
      </AnimatePresence>
```

配套：`dbAssets`/`intents`/`actions` 从 `useAppStore` 取（`const dbAssets = useAppStore((s) => s.dbAssets);` 等）。intents/actions 也可从 store 取（`s.intents`/`s.actions`）。**保留 3-dock 隐喻**：drawer 是覆盖层，不改 dock 项。

- [ ] **Step 5: 运行测试 + 四门**

Run: `cd frontend && npx vitest run __tests__/raw-event-browser.test.ts __tests__/raw-event-browser-drawer.test.tsx 2>&1 | tail -15`
Expected: PASS。然后四门全绿后停下验收。

---

## 里程碑 4 · 图时间演化（态势页 3D stage）

### Task 4.1: `lib/snapshot-evolution.ts` — 帧差分 + 摘要

**Files:**
- Create: `lib/snapshot-evolution.ts`
- Test: `__tests__/snapshot-evolution.test.ts`（新建）

**Interfaces:**
- Consumes: `GraphSnapshotFrame`（Task 0.1）、`pickRepresentativeFrames`（db-import.ts，M0 已导出）
- Produces（供 Task 4.2 scrubber）：
  - `FrameDiff { addedNodes; removedNodes; addedEdges; removedEdges }`
  - `buildFrameDiff(prev: GraphSnapshotFrame, next: GraphSnapshotFrame): FrameDiff`
  - `summarizeFrameChange(diff): string`（`'+N 节点 / −M 边'`）

- [ ] **Step 1: 写失败测试 `__tests__/snapshot-evolution.test.ts`**

```ts
import { describe, expect, it } from 'vitest';
import { pickRepresentativeFrames } from '@/lib/db-import';
import { buildFrameDiff, summarizeFrameChange } from '@/lib/snapshot-evolution';
import type { GraphMetrics, GraphSnapshotFrame } from '@/lib/types';

const metrics: GraphMetrics = { node_counts: {}, edge_counts: {}, agent_count: 0, cross_agent_edges: 0, delegation_depth: 0, taint_chains: 0, avg_confidence: 0 };

const frame = (id: string, nodeIds: string[], edgeKeys: string[], ts: number): GraphSnapshotFrame => ({
  snapshot_id: id,
  timestamp_ns: ts,
  graph: {
    nodes: nodeIds.map((n) => ({ node_id: n, node_type: 'Agent', agent_id: n, resource: null, evidence_refs: [], timestamp_ns: ts, phase: { Provisional: { timestamp_ns: ts } } })),
    edges: edgeKeys.map((k, i) => ({ edge_id: `e${i}`, edge_type: 'IntentCausesAction', source: k.split('>')[0], target: k.split('>')[1], evidence_refs: [], confidence: 0.8, phase: { Provisional: { timestamp_ns: ts } }, signal: null })),
  },
  metrics,
});

describe('buildFrameDiff', () => {
  it('前后帧节点/边增删正确', () => {
    const prev = frame('a', ['n1', 'n2'], ['n1>n2'], 1);
    const next = frame('b', ['n1', 'n3'], ['n1>n3', 'n1>n2'], 2);
    const diff = buildFrameDiff(prev, next);
    expect(diff.addedNodes).toEqual(['n3']);
    expect(diff.removedNodes).toEqual(['n2']);
    expect(diff.addedEdges).toEqual(['n1>n3']);
    expect(diff.removedEdges).toEqual([]);
  });

  it('空 prev 帧（图首次出现）全部为新增', () => {
    const prev = frame('a', [], [], 1);
    const next = frame('b', ['n1'], [], 2);
    const diff = buildFrameDiff(prev, next);
    expect(diff.addedNodes).toEqual(['n1']);
    expect(diff.removedNodes).toEqual([]);
  });
});

describe('summarizeFrameChange', () => {
  it('生成 delta 文案', () => {
    const prev = frame('a', ['n1', 'n2'], ['n1>n2'], 1);
    const next = frame('b', ['n1', 'n3'], ['n1>n3'], 2);
    expect(summarizeFrameChange(buildFrameDiff(prev, next))).toBe('+1 节点 / −1 边');
  });
});

describe('pickRepresentativeFrames（复用 db-import 导出）', () => {
  it('n ≤ max 原样返回', () => {
    const f = [1, 2, 3].map((n) => `f${n}`);
    expect(pickRepresentativeFrames(f, 30)).toEqual(f);
  });
  it('n > max 均匀降采样，含首尾', () => {
    const f = Array.from({ length: 10 }, (_, i) => `f${i + 1}`);
    expect(pickRepresentativeFrames(f, 4)).toEqual(['f1', 'f4', 'f7', 'f10']);
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/snapshot-evolution.test.ts 2>&1 | tail -15`
Expected: FAIL — `Cannot find module '@/lib/snapshot-evolution'`（pickRepresentativeFrames 的用例已可过，因 M0 已实现）。

- [ ] **Step 3: 写实现 `lib/snapshot-evolution.ts`**

```ts
import type { GraphSnapshotFrame } from './types';
// pickRepresentativeFrames 在 M0 随 extractSnapshotFramesFromDb 落地于 db-import.ts，此处复用不重定义。

// ═══ 图时间演化（M4）——纯函数帧差分，无造数 ═══

export interface FrameDiff {
  addedNodes: string[];
  removedNodes: string[];
  addedEdges: string[];
  removedEdges: string[];
}

/** 边标识：source>target。 */
const edgeKey = (edge: { source: string; target: string }): string => `${edge.source}>${edge.target}`;

export function buildFrameDiff(prev: GraphSnapshotFrame, next: GraphSnapshotFrame): FrameDiff {
  const prevNodes = new Set(prev.graph.nodes.map((n) => n.node_id));
  const nextNodes = new Set(next.graph.nodes.map((n) => n.node_id));
  const prevEdges = new Set(prev.graph.edges.map(edgeKey));
  const nextEdges = new Set(next.graph.edges.map(edgeKey));

  const addedNodes = next.graph.nodes.map((n) => n.node_id).filter((id) => !prevNodes.has(id));
  const removedNodes = prev.graph.nodes.map((n) => n.node_id).filter((id) => !nextNodes.has(id));
  const addedEdges = next.graph.edges.map(edgeKey).filter((k) => !prevEdges.has(k));
  const removedEdges = prev.graph.edges.map(edgeKey).filter((k) => !nextEdges.has(k));

  return { addedNodes, removedNodes, addedEdges, removedEdges };
}

export function summarizeFrameChange(diff: FrameDiff): string {
  const nodeDelta = diff.addedNodes.length - diff.removedNodes.length;
  const edgeDelta = diff.addedEdges.length - diff.removedEdges.length;
  const nodeTxt = `${nodeDelta >= 0 ? '+' : ''}${nodeDelta} 节点`;
  const edgeTxt = `${edgeDelta >= 0 ? '+' : ''}${edgeDelta} 边`;
  return `${nodeTxt} / ${edgeTxt}`;
}
```

- [ ] **Step 4: 运行验证通过**

Run: `cd frontend && npx vitest run __tests__/snapshot-evolution.test.ts 2>&1 | tail -15`
Expected: PASS（5 用例）。

### Task 4.2: `graph-time-scrubber.tsx` + 态势页集成（stage graphOverride）

**Files:**
- Create: `components/graph/graph-time-scrubber.tsx`
- Modify: `components/ui/mtagentlens-graph-stage.tsx`（加可选 `graphOverride?: GraphData` prop）
- Modify: `components/command-center/command-center-page.tsx`（stage 下方渲染 scrubber + 传 graphOverride）
- Test: `__tests__/command-center-page.test.tsx`（追加 scrubber 用例：frames>1 显示、0/1 帧隐藏——沿用现有 mock 模式）

**Interfaces:**
- Consumes: `buildFrameDiff`/`summarizeFrameChange`（Task 4.1）、store 的 `dbAssets`/`snapshotFrameIndex`/`setSnapshotFrameIndex`、`MtAgentLensGraphStage`
- Produces: 时间滑杆（播放/暂停 + 步进 + 帧索引 + 时间戳 + delta 徽章）

- [ ] **Step 1: 写失败测试**

`__tests__/command-center-page.test.tsx` 追加（沿用该文件 mock `mtagentlens-graph-stage` 的惯例）：

```tsx
  it('snapshotFrames > 1 时渲染时间 scrubber', async () => {
    // 通过 mock store 注入 snapshotFrames 2 帧 + snapshotFrameIndex 1
    render(<CommandCenterPage />);
    expect(await screen.findByRole('group', { name: /时间演化/i })).toBeTruthy();
    expect(screen.getByText(/2 \/ 2/)).toBeTruthy(); // 帧索引「最新 / 总数」
  });

  it('snapshotFrames ≤ 1 时隐藏 scrubber', async () => {
    render(<CommandCenterPage />);
    expect(screen.queryByRole('group', { name: /时间演化/i })).toBeNull();
  });
```

> 若现有测试 mock 的 store 不含 dbAssets/snapshotFrameIndex，需在 mock 里补（`dbAssets: null, snapshotFrameIndex: 0`）。先 Read `command-center-page.test.tsx` 按其 mock 形态扩展。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run __tests__/command-center-page.test.tsx 2>&1 | tail -15`
Expected: FAIL — 找不到「时间演化」。

- [ ] **Step 3: stage 加 graphOverride prop**

`components/ui/mtagentlens-graph-stage.tsx`：
- `Props` 类型追加 `graphOverride?: GraphData`（顶部 `import type { GraphData } from '@/lib/types';`，若未引入）。
- 组件内 `const graph = useAppStore((s) => s.graph);` 改为：

```tsx
  const storeGraph = useAppStore((s) => s.graph);
  const graph = graphOverride ?? storeGraph;
```

（`graphOverride` 从 props 解构。后续所有 `graph.nodes`/`graph.edges` 引用不变——它们消费的 `graph` 现在可能是 override。）

- [ ] **Step 4: 写实现 `components/graph/graph-time-scrubber.tsx`**

```tsx
'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { useAppStore } from '@/lib/store';
import { buildFrameDiff, summarizeFrameChange } from '@/lib/snapshot-evolution';
import type { GraphSnapshotFrame } from '@/lib/types';
```

> 组件职责（纯驱动，不读图内容）：
> - `const frames = useAppStore((s) => s.dbAssets?.snapshotFrames ?? []);`
> - `const index = useAppStore((s) => s.snapshotFrameIndex);`
> - `const setIndex = useAppStore((s) => s.setSnapshotFrameIndex);`
> - 局部状态：`playing`（播放中）、`speedRef`（默认每帧 1200ms，`setInterval` + `setIndex((index + 1) % frames.length)`，帧边界循环）。
> - `frame = frames[index]`（无帧时 return null）。
> - **仅 `frames.length > 1` 渲染**：`if (frames.length <= 1) return null;`。
> - 渲染（`role="group" aria-label="时间演化"`）：
>   - 1D 时间轴：`frames.map` 圆点，`width: index / (frames.length-1) * 100%` 定位；hover 显示 `snapshot_id`；当前帧点高亮（pulse）。
>   - 播放/暂停按钮 + ⏮/⏭ 步进（`setIndex(clamp(index ± 1, 0, len-1))`）。
>   - 帧索引「{index+1} / {frames.length}」。
>   - 当前帧时间戳：相对帧序（第一帧为 0，显示 `(ts - frames[0].ts)` 格式化 ms，或用绝对值 `new Date(ts/1e6).toLocaleTimeString()`——取相对 run 起点更直观，与 spec §7.1「相对 run 起点」一致）。
>   - delta 徽章：`summarizeFrameChange(buildFrameDiff(frames[index-1], frames[index]))`（index>0 时；index=0 显示「—」）。
> - **动画而非跳切**：帧切换即 `graphOverride` 数据替换，react-force-graph-3d 以 node id 作 key → 新增淡入/移除淡出天然获得（对象恒常）。scrubber 自身无需额外动画。
> - `useReducedMotion`：reduced motion 时步进直接跳，不做过渡。
> - 样式：`fixed` 底部居中、`z-index` 高于 stage、半透明玻璃底（`bg-black/50 backdrop-blur`），与态势页沉浸视觉一致；`aria-label` 齐全。

- [ ] **Step 5: 态势页集成**

`command-center-page.tsx`：
1. 顶部 import 追加 `import GraphTimeScrubber from '@/components/graph/graph-time-scrubber';`。
2. `const dbAssets = useAppStore((state) => state.dbAssets);`、`const snapshotFrameIndex = useAppStore((state) => state.snapshotFrameIndex);`。
3. stage 传 override：`<MtAgentLensGraphStage active onNodeClick={...} onBackgroundClick={...} graphOverride={frames.length > 1 ? frames[snapshotFrameIndex].graph : undefined} />`，其中 `const frames = dbAssets?.snapshotFrames ?? [];`（在组件内 compute）。
4. stage 容器之后渲染：`<GraphTimeScrubber />`（scrubber 自管显示/隐藏）。

> **裁决 #6 落地**：不改全局 store.graph；stage 只读当前帧。帧索引由 M0 的 applyDbImport 初始化为最新帧，故初始视图 = 最新帧 = 现状，无跳变。

- [ ] **Step 6: 运行测试 + 四门**

Run: `cd frontend && npx vitest run __tests__/snapshot-evolution.test.ts __tests__/command-center-page.test.tsx 2>&1 | tail -15`
Expected: PASS。然后四门全绿后停下验收。

---

## 里程碑完成后全局收尾

### Task 5.1: 全量回归 + 文档同步

**Files:**
- Modify: `README.md`（如页面结构未变则仅确认；本计划新增 4 功能面板/抽屉，如有功能清单节则补一行）
- Modify: `docs/superpowers/archive/specs/2026-08-07-db-four-features-design.md`（将「待用户复核」状态改为「已实现」，并把 §十 已知限制补充本计划发现的 3 项：dt_ns 不可算、PatternVerdict 无 targets、intents 无 agent 标注）

- [ ] **Step 1: 全量四门**

Run: `cd frontend && npm run type-check && npm run lint && npx vitest run && npm run build`
Expected: 全绿。

- [ ] **Step 2: 文档同步**

按上方 Files 更新 README/spec 已知限制；commit message 由用户定稿后提交。

## 自检记录（计划期已完成的核验）

1. **spec 覆盖**：M0 覆盖 §三 全部 3.1/3.2/3.3/3.4/3.5；M1 覆盖 §四 4.1/4.2；M2 覆盖 §五 5.1/5.2；M3 覆盖 §六 6.1/6.2；M4 覆盖 §七 7.1/7.2；§八 顺序与 §九 约束全部体现。无遗漏节。
2. **占位符扫描**：Task 2.1 实现中原有的 `roots.sort((a, b) => a.timestampNsSort(b))` 伪码占位已在计划文本中原地修复为直接比较排序（children 同步排序），无残留。Task 1.2/3.2/4.2 组件实现以「职责要点 + 完整 props/接口/测试 + 精确集成 JSX 片段」给出，核心算法/类型/断言均为真实代码；渲染细节按现有组件模式（SectionPanel/VirtualList/motion）落实现，因执行时需要对照当前组件源码。
3. **类型一致性**：`dt_ns` 在类型（`number | null`）与实现（恒 null）一致；`EngineVerdict.targets` 类型与实现（rule 取 matched_resource / pattern []）一致；`buildForensicChain` 输入签名在测试与实现一致（intentIds/alertEvidenceRefs）；`GraphSnapshotFrame.graph` 为 eager `GraphData`（非 lazy null），与 spec §7.1 提取即解析一致。
4. **已实测断裂点**（均在计划任务内修复）：db-import.test.ts overview 计数 7 处、monitoring-extractors.test.ts 5 处、空库用例补 DROP、derive-monitoring 注释 2 处、NonLinkDiagnostic 改名 1 处。
