# Handoff Observability — API Contract v3.1

> **来自:** Agent A (前端开发者)
> **交接给:** Agent B (后端开发者)
> **日期:** 2026-06-16
> **组件:** `HandoffDelegationTree` + `HandoffDashboardShell`

## 1. 数据流总览

```
Rust 后端 (HandoffChainAnalyzer / HandoffObservabilityAnalyzer)
  │
  ├─ SSE stream: source="handoff_observability" ──────────→ 前端 HandoffDashboardShell
  │    事件类型: HANDOFF_SPAWN, HANDOFF_COMPLETE, TOOL_DENIED, FANOUT_STORM, ...
  │
  ├─ SSE stream: source="delegation_tree" ───────────────→ 前端 HandoffDelegationTree
  │    增量更新: node_spawned / node_completed / node_failed
  │
  ├─ REST GET  /api/handoff/delegation-tree ────────────→ 完整委托树快照
  ├─ REST GET  /api/handoff/metrics ────────────────────→ 聚合指标
  ├─ REST GET  /api/handoff/events?limit=N ──────────────→ 历史事件查询
  └─ REST POST /api/verification/start ─────────────────→ 启动三阶递验
```

## 2. REST API 端点

### 2.1 `GET /api/handoff/delegation-tree`

返回完整委托树的根节点。客户端自根向下展开。

**响应:**
```typescript
interface DelegationTreeResponse {
  tree: DelegationNode | null;
  tree_id: string;
  built_at_ms: number;        // 树构建完成时间
  metrics: HandoffObsMetrics; // 聚合指标
}

interface DelegationNode {
  node_id: string;
  agent_id: string;
  parent_node_id: string | null;
  children: DelegationNode[];        // ← 后端直接返回嵌套结构
  chain_depth: number;
  spawned_at_ms: number;
  completed_at_ms: number | null;
  bytes_in: number;
  bytes_out: number;
  token_count: number;
  context_overhead_ratio: number;
  idle_breakdown: IdleTimeBreakdown;
  model_assigned: string | null;
  model_optimal: string | null;
  child_ids: string[];
}

interface IdleTimeBreakdown {
  queued_awaiting_dispatch_ms: number;
  context_injection_io_ms: number;
  model_inference_queueing_ms: number;
  tool_call_round_trip_ms: number;
}
```

### 2.2 `GET /api/handoff/metrics`

**响应:**
```typescript
interface HandoffObsMetrics {
  total_spawned: number;
  total_completed: number;
  active_count: number;
  max_chain_depth: number;
  fan_out_ratio: number;
  fan_out_critical: boolean;
  avg_context_overhead: number;
  orphaned_artifacts: number;
  workspace_tag_survival_rate: number;
}
```

### 2.3 `GET /api/handoff/events?limit=100&since_ms=0&sub_type=`

按条件查询历史 handoff 事件。

**查询参数:**
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `limit` | number | 100 | 返回事件数上限 (max 500) |
| `since_ms` | number | 0 | 仅返回该时间戳之后的事件 |
| `sub_type` | string? | — | 按子类型过滤: HANDOFF_SPAWN, FANOUT_STORM 等 |

**响应:**
```typescript
interface HandoffEventsResponse {
  events: HandoffObservabilityEvent[];
  total: number;
  has_more: boolean;
}
```

### 2.4 `GET /api/handoff/events/:id`

返回单条事件的详情。

**响应:** `HandoffObservabilityEvent`

### 2.5 `GET /api/handoff/dependency-edges`

返回所有跨代理依赖边（用于检测孤立工作）。

**响应:**
```typescript
interface DependencyEdgesResponse {
  edges: Array<{
    edge_id: string;
    from_spawn_id: string;
    to_spawn_id: string;
    artifact_path: string;
    is_orphaned: boolean;
    idle_ms: number;
    producer_agent_id: string;
    consumer_agent_id: string | null;
  }>;
}
```

### 2.6 `POST /api/handoff/probe`

启动一次手递手诊断探测（对应 shared_handoff.md Template D/E/F）。

**请求:**
```typescript
interface HandoffProbeRequest {
  scope: "probes" | "stream" | "runtime" | "all";
  pid?: number | null;
  port?: number;
}
```

**响应:**
```typescript
interface HandoffProbeResponse {
  runId: string;           // 后续通过 /api/diag/report/:runId 查询
  started_at_ms: number;
}
```

## 3. SSE 事件流

所有事件通过 `/api/events` SSE 端点推送。前端通过 EventSource 消费。

### 3.1 DelegationTree 增量更新

```
event: message
data: {"stream_type":"delegation_tree","data":{"tree_id":"...","update_type":"node_spawned","timestamp_ms":...,"node":{...}}}
```

**update_type 可选值:**
- `node_spawned` — 新子代理生成
- `node_completed` — 子代理完成（填充 completed_at_ms + 完成记录）
- `node_failed` — 子代理失败

### 3.2 HandoffObservability 事件

```
event: message
data: {"stream_type":"handoff_observability","data":{"sub_type":"HANDOFF_SPAWN","spawn_id":"...","agent_id":"...", ...}}
```

**完整 sub_type 列表:**

| sub_type | 说明 | 所在合约 |
|----------|------|---------|
| `HANDOFF_SPAWN` | 子代理生成 | handoff-observability-contract.ts |
| `HANDOFF_COMPLETE` | 子代理完成 | handoff-observability-contract.ts |
| `BOUNDARY_TAG` | 工作区边界标记 | handoff-observability-contract.ts |
| `FILE_CORRELATION` | 文件访问关联 | handoff-observability-contract.ts |
| `CONTEXT_SATURATION` | 上下文窗口饱和 | handoff-observability-contract.ts |
| `TOOL_DENIED` | 策略门控拒绝 | handoff-observability-contract.ts |
| `FANOUT_STORM` | 扇出风暴告警 | handoff-observability-contract.ts |
| `BOUNDARY_DRIFT` | 边界漂移 | handoff-observability-contract.ts |
| `DATA_LEAK` | 数据泄漏告警 | handoff-observability-contract.ts |
| `STRATEGY_DIVERGENCE` | 策略偏离 | handoff-observability-contract.ts |
| `CONTEXT_CONFLICT` | 上下文冲突 | handoff-observability-contract.ts |
| `CORRECTIVE_LOOP` | 纠正循环 | handoff-observability-contract.ts |
| `ORPHANED_WORK` | 孤立工作 | handoff-observability-contract.ts |
| `DRIFT_ALERT` | 语义漂移告警 | handoff-observability-contract.ts |
| `PIPELINE_INTEGRITY` | 流水线健康告警 | handoff-observability-contract.ts |

### 3.3 FileAccess 事件

```
data: {"stream_type":"file_access","data":{"access_id":"...","target_path":"...","workspace_tag":"inside",...}}
```

## 4. 前端组件 Props

### HandoffDelegationTree

用于渲染委托树的可视化组件。

```typescript
interface HandoffDelegationTreeProps {
  tree: DelegationNode | null;
  metrics: HandoffObsMetrics;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
  replayTimestampMs: number | null;
  dependencyEdges: Array<{
    from_spawn_id: string;
    to_spawn_id: string;
    artifact_path: string;
    is_orphaned: boolean;
  }>;
  colorByDepth?: boolean;
}
```

### HandoffDashboardShell

全局手递手仪表板壳，集成委托树、事件流、扇出监控、边界卫星四个视图。

```typescript
interface HandoffDashboardShellProps {
  delegationTree: DelegationNode | null;
  metrics: HandoffObsMetrics;
  recentEvents: HandoffObservabilityEvent[];
  selectedNodeId: string | null;
  dependencyEdges?: Array<{...}>;
  onSelectNode: (nodeId: string) => void;
  onSelectEvent?: (event: HandoffObservabilityEvent) => void;
  replayTimestampMs?: number | null;
}
```

## 5. 集成点与现有工具对接

| 前端组件 | 对接后端 | 数据源 |
|---------|---------|--------|
| `HandoffDelegationTree` | HandoffChainAnalyzer | `delegation_tree` SSE + `GET /api/handoff/delegation-tree` |
| `HandoffDashboardShell` | HandoffObservabilityAnalyzer | `handoff_observability` SSE + `GET /api/handoff/events` |
| 节点详情 (NodeDetailPanel) | 委托树节点对象 | 已内嵌在 DelegationNode 中 |

## 6. 类型文件路径

所有 TypeScript 类型定义位于:

| 文件 | 内容 |
|------|------|
| `lib/handoff-observability-contract.ts` | DelegationNode, HandoffObsMetrics, 所有事件类型, 组件 Props |
| `lib/handoff-contract.ts` | 委托树 + HandoffAlert + 文件访问扩展 |
| `lib/handoff-chain-contract.ts` | 链深度 + 饱和度 + 扇出 + 纠正循环 + 生命周期延迟 |
| `lib/handoff-visualization-contract.ts` | 仪器类型鉴别符 (InstrumentType) |
| `lib/handoff-dashboard-contract.ts` | PalaceHall / 宫室仪象的完整仪表板类型 |
| `lib/handoff-verification-contract.ts` | 三阶递验类型 (TierAssertion, VerificationProgress) |
| `lib/handoff-verification-dashboard-contract.ts` | 递验总持殿组件 Props |
| `components/ui/handoff-delegation-tree.tsx` | ✅ 已实现 |
| `components/ui/handoff-dashboard-shell.tsx` | ✅ 已实现 |

## 7. 后端需要实现的 REST 端点

- [ ] `GET /api/handoff/delegation-tree` — 完整委托树快照
- [ ] `GET /api/handoff/metrics` — 聚合指标
- [ ] `GET /api/handoff/events` — 历史事件查询 (含分页、过滤)
- [ ] `GET /api/handoff/events/:id` — 单条事件详情
- [ ] `GET /api/handoff/dependency-edges` — 跨代理依赖边
- [ ] SSE 端点: `/api/events` — 推送 `delegation_tree` 和 `handoff_observability` 事件

优先级: SSE 推送 > `GET /metrics` > `GET /delegation-tree` > `GET /events` > `GET /dependency-edges`
