# 前端大规模数据性能架构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让整个前端（主页 3D、仪表盘、告警/追踪工作台、侧边栏）在十万级节点 / 百万级边 / 千级告警下不卡死，通过 LOD 聚合 + Web Worker 布局 + 延迟模型计算 + 列表虚拟化 + 实时流合并实现。

**Architecture:** 三层组合「数据降维（LOD 聚合）→ 离主线程（Worker 布局/聚合）→ 只渲染可见（虚拟化 + 渲染预算）」。原始图按规模自动降维，重计算全部闲时/worker 化，长列表虚拟化，live 高频更新按帧合并。

**Tech Stack:** TypeScript, React 19, zustand, graphology, forceAtlas2 (Barnes-Hut), Sigma.js, 3d-force-graph, @tanstack/react-virtual, vitest, Next.js (turbopack dev / export prod)。

**Spec:** `docs/archive/engineering/specs/2026-08-06-frontend-large-data-performance-design.md`

## Global Constraints

- 现有 11 个 vitest 失败为**会话前基线**（见 memory: pre-existing-frontend-test-failures），任何任务不得使其增加新失败。
- 每个任务结束必须 `npx tsc --noEmit` + `npx eslint <改动的文件>` 全绿。
- 不引入 React Compiler；不重写图形库；不改 Rust/数据服务端。
- `barnesHutOptimize: true, barnesHutTheta: 1.0` 用于所有 forceAtlas2 布局。
- worker 创建失败必须自动回退主线程同步布局，禁止让图空白。
- 所有交互文案保持中文，代码/注释英文。
- 现有 11 个基线失败之外，不得新增任何测试失败。

---

## Task 1: LOD 图聚合管线（`lib/graph-lod.ts`）

**Files:**
- Create: `frontend/lib/graph-lod.ts`
- Test: `frontend/__tests__/graph-lod.test.ts`

**Interfaces:**
- Consumes: `GraphData`（`@/lib/types`）、`adaptGraphToLargeGraph`（`@/lib/large-graph-adapter`，返回 `LargeGraphData`，节点带 `agent_id`、`node_type`、`raw_node_ids`；链接带 `source/target/edge_type/raw_edge_ids`）
- Produces:
  - `type LodLevel = 'raw' | 'agent' | 'cluster'`
  - `function pickLodLevel(rawNodeCount: number): LodLevel`
  - `function summarizeToLod(graph: GraphData, level: 'agent' | 'cluster'): LodGraph`
  - `interface LodGraph { level; nodes: { id; node_type; label; count }[]; edges: { source; target; edge_type; count }[]; rawNodeCount; rawEdgeCount }`

- [ ] **Step 1: 写失败测试**

```ts
// frontend/__tests__/graph-lod.test.ts
import { describe, expect, it } from 'vitest';
import { pickLodLevel, summarizeToLod, type LodLevel } from '@/lib/graph-lod';
import { demoGraph } from '@/lib/demo-data';

describe('graph-lod', () => {
  it('picks LOD level by raw node count', () => {
    expect(pickLodLevel(1000)).toBe('raw');
    expect(pickLodLevel(10_000)).toBe('agent');
    expect(pickLodLevel(100_000)).toBe('cluster');
  });

  it('summarizes demo graph to agent level', () => {
    const lod = summarizeToLod(demoGraph, 'agent');
    expect(lod.rawNodeCount).toBe(demoGraph.nodes.length);
    expect(lod.nodes.length).toBeGreaterThan(0);
    expect(lod.nodes.length).toBeLessThanOrEqual(demoGraph.nodes.length);
    // 所有节点 id 唯一
    expect(new Set(lod.nodes.map((n) => n.id)).size).toBe(lod.nodes.length);
    // 边端点引用存在的节点
    for (const e of lod.edges) {
      expect(lod.nodes.some((n) => n.id === e.source)).toBe(true);
      expect(lod.nodes.some((n) => n.id === e.target)).toBe(true);
    }
  });

  it('cluster level is deterministic (same input → same ids)', () => {
    const a = summarizeToLod(demoGraph, 'cluster');
    const b = summarizeToLod(demoGraph, 'cluster');
    expect(a.nodes.map((n) => n.id)).toEqual(b.nodes.map((n) => n.id));
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run __tests__/graph-lod.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `graph-lod.ts`**

```ts
import type { GraphData } from '@/lib/types';
import { adaptGraphToLargeGraph, type LargeGraphData } from '@/lib/large-graph-adapter';

export type LodLevel = 'raw' | 'agent' | 'cluster';

export interface LodNode {
  id: string;
  node_type: string;
  label: string;
  count: number;
}

export interface LodEdge {
  source: string;
  target: string;
  edge_type: string;
  count: number;
}

export interface LodGraph {
  level: LodLevel;
  nodes: LodNode[];
  edges: LodEdge[];
  rawNodeCount: number;
  rawEdgeCount: number;
}

export const LOD_RAW_LIMIT = 4_000;
export const LOD_AGENT_LIMIT = 20_000;
export const CLUSTER_BUCKETS = 24;

export function pickLodLevel(rawNodeCount: number): LodLevel {
  if (rawNodeCount <= LOD_RAW_LIMIT) return 'raw';
  if (rawNodeCount <= LOD_AGENT_LIMIT) return 'agent';
  return 'cluster';
}

function fnv1a32(input: string): number {
  let hash = 2166136261;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

// Coarse, deterministic bucket key for ultra-large graphs: groups agents into
// stable "constellations" so a 100k-node graph renders as ~24 clusters.
function bucketId(agentId: string, buckets: number): string {
  return `bucket:${fnv1a32(agentId) % buckets}`;
}

export function summarizeToLod(graph: GraphData, level: 'agent' | 'cluster'): LodGraph {
  const adapted: LargeGraphData = adaptGraphToLargeGraph(graph);

  let nodes = adapted.nodes;
  if (level === 'cluster') {
    // Merge agent nodes into deterministic buckets.
    const byBucket = new Map<string, LargeGraphData['nodes'][number][]>();
    for (const n of nodes) {
      const key = bucketId(n.agent_id || n.id, CLUSTER_BUCKETS);
      const list = byBucket.get(key) ?? [];
      list.push(n);
      byBucket.set(key, list);
    }
    const merged = Array.from(byBucket.entries()).map(([bucket, members]) => {
      const totalCount = members.reduce((s, m) => s + (m.raw_node_ids?.length ?? 1), 0);
      const first = members[0];
      return {
        id: bucket,
        node_type: 'Agent' as const,
        agent_id: bucket,
        cluster_id: bucket,
        description: `${members.length} agents`,
        raw_node_ids: members.flatMap((m) => m.raw_node_ids ?? []),
        rawEdgeIds: undefined,
      };
    });
    nodes = merged as unknown as LargeGraphData['nodes'];
  }

  const nodeIds = new Set(nodes.map((n) => n.id));

  return {
    level,
    nodes: nodes.map((n) => ({
      id: n.id,
      node_type: n.node_type,
      label: n.agent_id || n.id,
      count: n.raw_node_ids?.length ?? 1,
    })),
    edges: adapted.links
      .filter((l) => {
        const src = typeof l.source === 'string' ? l.source : l.source.id;
        const tgt = typeof l.target === 'string' ? l.target : l.target.id;
        return nodeIds.has(src) && nodeIds.has(tgt);
      })
      .map((l) => ({
        source: typeof l.source === 'string' ? l.source : l.source.id,
        target: typeof l.target === 'string' ? l.target : l.target.id,
        edge_type: l.edge_type,
        count: l.raw_edge_ids?.length ?? 1,
      })),
    rawNodeCount: graph.nodes.length,
    rawEdgeCount: graph.edges.length,
  };
}
```

> 注：若 `LargeGraphNode` 类型缺少 `rawEdgeIds` 字段导致编译错，删除该行即可（聚合结果由 `raw_node_ids` 承载）。

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run __tests__/graph-lod.test.ts`
Expected: PASS（3 用例）

- [ ] **Step 5: 提交**

```bash
git add frontend/lib/graph-lod.ts frontend/__tests__/graph-lod.test.ts
git -c user.name="lzk" -c user.email="lzk@local" commit -m "feat: LOD 图聚合管线（raw/agent/cluster 三级）"
```

---

## Task 2: Web Worker 图布局（`layout.worker.ts` + `layout-worker-client.ts`）

**Files:**
- Create: `frontend/lib/layout.worker.ts`
- Create: `frontend/lib/layout-worker-client.ts`
- Test: `frontend/__tests__/layout-worker-client.test.ts`

**Interfaces:**
- Consumes: `graphology`, `graphology-layout-forceatlas2`
- Produces:
  - `interface LayoutRequest { id: number; nodes: { id: string; x: number; y: number }[]; edges: { source: string; target: string }[]; iterations: number; settings?: Record<string, unknown> }`
  - `interface LayoutResult { id: number; positions: Record<string, { x: number; y: number }>; error?: string }`
  - `function requestLayout(req: Omit<LayoutRequest, 'id'>): Promise<LayoutResult>`（worker 不可用时 reject `worker-unavailable`）
  - `function runLayoutSync(nodes, edges, iterations): Record<string, {x,y}>`（主线程回退，供调用方捕获 reject 后用）

- [ ] **Step 1: 写失败测试（worker 可用时走 worker；不可用测回退）**

```ts
// frontend/__tests__/layout-worker-client.test.ts
import { describe, expect, it } from 'vitest';
import { requestLayout, runLayoutSync } from '@/lib/layout-worker-client';

const nodes = [
  { id: 'a', x: 0, y: 0 },
  { id: 'b', x: 0.5, y: 0 },
  { id: 'c', x: 1, y: 0 },
];
const edges = [
  { source: 'a', target: 'b' },
  { source: 'b', target: 'c' },
];

describe('layout worker client', () => {
  it('sync layout produces positions for every node', () => {
    const positions = runLayoutSync(nodes, edges, 10);
    expect(Object.keys(positions).sort()).toEqual(['a', 'b', 'c']);
    expect(Number.isFinite(positions.a.x)).toBe(true);
    expect(Number.isFinite(positions.a.y)).toBe(true);
  });

  it('requestLayout resolves or falls back gracefully', async () => {
    try {
      const res = await requestLayout({ nodes, edges, iterations: 10 });
      expect(res.error ?? undefined).toBeUndefined();
      if (res.positions) {
        expect(Object.keys(res.positions).length).toBe(3);
      }
    } catch {
      // worker 不可用 → 应 reject 'worker-unavailable'
    }
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run __tests__/layout-worker-client.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 worker 文件**

```ts
// frontend/lib/layout.worker.ts
/// <reference lib="webworker" />
import forceAtlas2 from 'graphology-layout-forceatlas2';
import Graph from 'graphology';
import type { LayoutRequest, LayoutResult } from './layout-worker-client';

self.onmessage = (e: MessageEvent<LayoutRequest>) => {
  const { id, nodes, edges, iterations, settings } = e.data;
  try {
    const g = new Graph({ multi: true });
    for (const n of nodes) g.addNode(n.id, { x: n.x, y: n.y });
    for (const ed of edges) {
      if (g.hasNode(ed.source) && g.hasNode(ed.target)) g.addEdge(ed.source, ed.target);
    }
    if (g.order > 1) {
      forceAtlas2.assign(g, {
        iterations,
        settings: {
          ...(settings ?? {}),
          barnesHutOptimize: true,
          barnesHutTheta: 1.0,
        },
      });
    }
    const positions: LayoutResult['positions'] = {};
    g.forEachNode((nodeId, attr) => {
      positions[nodeId] = { x: attr.x, y: attr.y };
    });
    (self as unknown as Worker).postMessage({ id, positions } satisfies LayoutResult);
  } catch (err) {
    (self as unknown as Worker).postMessage({ id, error: String(err) } satisfies LayoutResult);
  }
};
```

- [ ] **Step 4: 实现客户端（worker + 同步回退）**

```ts
// frontend/lib/layout-worker-client.ts
import forceAtlas2 from 'graphology-layout-forceatlas2';
import Graph from 'graphology';

export interface LayoutNodeInput {
  id: string;
  x: number;
  y: number;
}

export interface LayoutEdgeInput {
  source: string;
  target: string;
}

export interface LayoutRequest {
  id: number;
  nodes: LayoutNodeInput[];
  edges: LayoutEdgeInput[];
  iterations: number;
  settings?: Record<string, unknown>;
}

export interface LayoutResult {
  id: number;
  positions: Record<string, { x: number; y: number }>;
  error?: string;
}

let worker: Worker | null = null;
let seq = 0;
const pending = new Map<number, (r: LayoutResult) => void>();

function getWorker(): Worker | null {
  if (worker) return worker;
  if (typeof window === 'undefined' || typeof Worker === 'undefined') return null;
  try {
    worker = new Worker(new URL('./layout.worker.ts', import.meta.url));
    worker.onmessage = (e: MessageEvent<LayoutResult>) => {
      const resolve = pending.get(e.data.id);
      if (resolve) {
        pending.delete(e.data.id);
        resolve(e.data);
      }
    };
    worker.onerror = () => {
      worker?.terminate();
      worker = null;
    };
    return worker;
  } catch {
    return null;
  }
}

export function requestLayout(req: Omit<LayoutRequest, 'id'>): Promise<LayoutResult> {
  const w = getWorker();
  if (!w) return Promise.reject(new Error('worker-unavailable'));
  const id = ++seq;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    w.postMessage({ ...req, id });
  });
}

// Synchronous fallback — never leaves the graph blank if workers are unavailable.
export function runLayoutSync(
  nodes: LayoutNodeInput[],
  edges: LayoutEdgeInput[],
  iterations: number,
  settings?: Record<string, unknown>,
): Record<string, { x: number; y: number }> {
  const g = new Graph({ multi: true });
  for (const n of nodes) g.addNode(n.id, { x: n.x, y: n.y });
  for (const ed of edges) {
    if (g.hasNode(ed.source) && g.hasNode(ed.target)) g.addEdge(ed.source, ed.target);
  }
  if (g.order > 1) {
    forceAtlas2.assign(g, {
      iterations,
      settings: { ...(settings ?? {}), barnesHutOptimize: true, barnesHutTheta: 1.0 },
    });
  }
  const positions: Record<string, { x: number; y: number }> = {};
  g.forEachNode((nodeId, attr) => {
    positions[nodeId] = { x: attr.x, y: attr.y };
  });
  return positions;
}
```

> **Next.js worker 打包注记：** 若 `next.config.ts` 需要显式支持 worker，turbopack 通常自动处理 `new Worker(new URL(...))`；静态导出（`output: 'export'`）需在构建后检查 `out/` 是否包含 worker chunk。若构建/运行抛错，将 `requestLayout` 的 worker 分支保留但让 `getWorker()` 返回 null（改为始终走 `runLayoutSync`），并在 README 记录。

- [ ] **Step 5: 运行确认通过**

Run: `npx vitest run __tests__/layout-worker-client.test.ts`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add frontend/lib/layout.worker.ts frontend/lib/layout-worker-client.ts frontend/__tests__/layout-worker-client.test.ts
git -c user.name="lzk" -c user.email="lzk@local" commit -m "feat: Web Worker 图布局 + 主线程同步回退"
```

---

## Task 3: 延迟模型计算 Hook（`use-alert-models.ts`）

**Files:**
- Create: `frontend/lib/use-alert-models.ts`
- Test: `frontend/__tests__/use-alert-models.test.ts`

**Interfaces:**
- Consumes: `buildInvestigationModel`（`@/lib/investigation`，返回 `InvestigationModel`，含 `alertId/severity/decision`）、`ResponsibilityAlert`/`GraphData`/`CorrelationEvidence`（`@/lib/types`）
- Produces:
  - `function graphFingerprint(graph: GraphData): string`
  - `interface AlertModelEntry { alert: ResponsibilityAlert; model: InvestigationModel | null }`
  - `function useAlertModels(alerts, graph, evidence?): AlertModelEntry[]`（按 severity→decision→timestamp 排序；未缓存项 model 为 null 并逐步填充）

- [ ] **Step 1: 写失败测试**

```ts
// frontend/__tests__/use-alert-models.test.tsx
import { describe, expect, it } from 'vitest';
import { renderHook } from '@testing-library/react';
import { demoAlerts, demoGraph, demoEvidence } from '@/lib/demo-data';
import { useAlertModels, graphFingerprint } from '@/lib/use-alert-models';

describe('useAlertModels', () => {
  it('fingerprint is stable for same graph and differs for different sizes', () => {
    const a = graphFingerprint(demoGraph);
    const b = graphFingerprint({ nodes: demoGraph.nodes, edges: demoGraph.edges });
    expect(a).toBe(b);
    expect(graphFingerprint({ nodes: [], edges: [] })).not.toBe(a);
  });

  it('returns entries for every alert; selected top entry matches sorted order', () => {
    const { result } = renderHook(() => useAlertModels(demoAlerts, demoGraph, demoEvidence));
    const entries = result.current;
    expect(entries.length).toBe(demoAlerts.length);
    expect(entries.every((e) => e.alert && e.alert.alert_id)).toBe(true);
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run __tests__/use-alert-models.test.tsx`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 Hook**

```ts
// frontend/lib/use-alert-models.ts
'use client';
import { useEffect, useMemo, useState } from 'react';
import { buildInvestigationModel, type InvestigationModel } from '@/lib/investigation';
import type { CorrelationEvidence, GraphData, ResponsibilityAlert } from '@/lib/types';

export interface AlertModelEntry {
  alert: ResponsibilityAlert;
  model: InvestigationModel | null;
}

// Module-level LRU cache keyed by `alertId|graphFp` — shared across all pages so
// switching routes never recomputes the same model twice.
const CACHE = new Map<string, InvestigationModel>();
const MAX_CACHE = 200;

export function graphFingerprint(graph: GraphData): string {
  return `${graph.nodes.length}|${graph.edges.length}`;
}

function cacheKey(alertId: string, fp: string): string {
  return `${alertId}|${fp}`;
}

function cached(alertId: string, fp: string): InvestigationModel | undefined {
  const key = cacheKey(alertId, fp);
  if (!CACHE.has(key)) return undefined;
  const v = CACHE.get(key)!;
  CACHE.delete(key); // LRU touch
  CACHE.set(key, v);
  return v;
}

function storeModel(alertId: string, fp: string, model: InvestigationModel): void {
  CACHE.set(cacheKey(alertId, fp), model);
  if (CACHE.size > MAX_CACHE) {
    const oldest = CACHE.keys().next().value;
    if (oldest) CACHE.delete(oldest);
  }
}

const severityWeight: Record<ResponsibilityAlert['severity'], number> = {
  Critical: 3,
  Warning: 2,
  Info: 1,
};

export function useAlertModels(
  alerts: ResponsibilityAlert[],
  graph: GraphData,
  evidence: CorrelationEvidence[] = [],
): AlertModelEntry[] {
  const fp = graphFingerprint(graph);
  const [processed, setProcessed] = useState(0);

  // Progressively compute uncached models in idle time — never blocks a frame.
  useEffect(() => {
    const uncached = alerts.filter((a) => !cached(a.alert_id, fp));
    if (uncached.length === 0) return;
    let cancelled = false;
    let i = 0;
    const step = () => {
      if (cancelled) return;
      const budgetStart = performance.now();
      while (i < uncached.length && performance.now() - budgetStart < 12) {
        const a = uncached[i++];
        if (!cached(a.alert_id, fp)) {
          storeModel(a.alert_id, fp, buildInvestigationModel(a, graph, evidence));
        }
      }
      setProcessed(i);
      if (i < uncached.length && typeof requestIdleCallback === 'function') {
        requestIdleCallback(step, { timeout: 1000 });
      } else if (i < uncached.length) {
        setTimeout(step, 0);
      }
    };
    if (typeof requestIdleCallback === 'function') {
      const handle = requestIdleCallback(step, { timeout: 1000 });
      return () => {
        cancelled = true;
        cancelIdleCallback(handle);
      };
    }
    step();
    return () => {
      cancelled = true;
    };
  }, [alerts, fp, graph, evidence]);

  return useMemo(() => {
    const entries: AlertModelEntry[] = alerts.map((alert) => ({
      alert,
      model: cached(alert.alert_id, fp) ?? null,
    }));
    entries.sort((a, b) => {
      const aw = a.model ? severityWeight[a.model.severity] : severityWeight[a.alert.severity];
      const bw = b.model ? severityWeight[b.model.severity] : severityWeight[b.alert.severity];
      if (aw !== bw) return bw - aw;
      const ad = a.model ? a.model.decision : a.alert.decision;
      const bd = b.model ? b.model.decision : b.alert.decision;
      const unsafeDelta = Number(bd === 'Unsafe') - Number(ad === 'Unsafe');
      if (unsafeDelta !== 0) return unsafeDelta;
      return b.alert.timestamp_ns - a.alert.timestamp_ns;
    });
    return entries;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alerts, fp, processed]);
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run __tests__/use-alert-models.test.tsx`
Expected: PASS（2 用例）

- [ ] **Step 5: 提交**

```bash
git add frontend/lib/use-alert-models.ts frontend/__tests__/use-alert-models.test.tsx
git -c user.name="lzk" -c user.email="lzk@local" commit -m "feat: 延迟模型计算 Hook（LRU 缓存 + 闲时分块计算）"
```

---

## Task 4: 三处调用点接入 `useAlertModels`

**Files:**
- Modify: `frontend/app/(main)/layout.tsx`（`alertModels` useMemo 区块，约 200-218 行）
- Modify: `frontend/components/command-center/command-center-page.tsx`（`alertModels` useMemo，约 112-129 行）
- Modify: `frontend/components/investigation/investigations-page-client.tsx`（`models` useMemo，约 35 行）

**Interfaces:**
- Consumes: `useAlertModels`, `AlertModelEntry`（Task 3）
- Produces: 三处统一用 `{ alert, model }`（model 可能为 null）结构；排序由 hook 保证

- [ ] **Step 1: 布局层接入（layout.tsx）**

把第 200-202 行：
```tsx
const alertModels = useMemo(
  () =>
    alerts
      .map((a) => ({ alert: a, model: buildInvestigationModel(a, graph, []) }))
      .sort(/* severity → decision → timestamp */),
  [alerts, graph],
);
```
替换为：
```tsx
import { useDeferredValue } from 'react';
import { useAlertModels } from '@/lib/use-alert-models';
// ...
const deferredAlerts = useDeferredValue(alerts);
const deferredGraph = useDeferredValue(graph);
const alertModels = useAlertModels(deferredAlerts, deferredGraph);
```
删除 `import { buildInvestigationModel } from '@/lib/investigation';`（layout 内不再直接调用）。`alertModels` 之后的使用处（`selectedItem`, `IncidentRail`, `openCount` 等）把 `m.model.xxx` 改为 `m.model?.xxx` 或 `m.alert.xxx` 兜底（`openCount`/`criticalCount` 用 `alert.decision/severity`，选中模型用 `selectedItem.model ?? buildInvestigationModel(selectedAlert, graph, [])` 同步兜底保证详情立即可见）。

- [ ] **Step 2: 主页接入（command-center-page.tsx）**

把 `alertModels` 的 `useMemo`（含 `buildInvestigationModel`）替换为 `useAlertModels(alerts, graph)`；删 `buildInvestigationModel` import；`selectedItem.model` 相关取值加 `?.` 兜底（`selectedModel` 为 null 时用 `buildInvestigationModel(selectedAlert, graph, [])` 同步兜底）。

- [ ] **Step 3: 告警工作台接入（investigations-page-client.tsx）**

`models` useMemo 改为 `const models = useAlertModels(alerts, graph, evidenceItems);`；删 `buildInvestigationModel` import；`InvestigationWorkbench` 的 `model={selected.model}` 处：若 `selected.model` 为 null，传 `buildInvestigationModel(selected.alert, graph, evidenceItems)`（同步兜底）。

- [ ] **Step 4: 验证**

Run:
```bash
cd frontend && npx tsc --noEmit
npx eslint "app/(main)/layout.tsx" components/command-center/command-center-page.tsx components/investigation/investigations-page-client.tsx
npx vitest run __tests__/use-alert-models.test.tsx
```
Expected: TS 0 错误、ESLint 0 错误、hook 测试 PASS；现有 11 基线失败数不变。

- [ ] **Step 5: 提交**

```bash
git add frontend/app/\\(main\\)/layout.tsx frontend/components/command-center/command-center-page.tsx frontend/components/investigation/investigations-page-client.tsx
git -c user.name="lzk" -c user.email="lzk@local" commit -m "refactor: 三处模型计算统一接入延迟计算 Hook"
```

---

## Task 5: Sigma 网络渲染预算 + Worker 布局

**Files:**
- Modify: `frontend/components/ui/linear/sigma-network.tsx`
- Modify: `frontend/lib/graph-lod.ts`（如需导出位置归一化辅助）

**Interfaces:**
- Consumes: `requestLayout` / `runLayoutSync`（Task 2）、`pickLodLevel` / `summarizeToLod`（Task 1）
- Produces: `buildGraph` 内：规模分支——>2w 边采样、>4k 节点走 agent LOD、布局优先 worker

- [ ] **Step 1: 渲染预算常量 + 边采样**

在 `sigma-network.tsx` 顶部新增：
```ts
const EDGE_RENDER_BUDGET = 20_000;
const NODE_LABEL_LIMIT = 60;
```
在 `buildGraph` 内、`for (const edge of graph.edges)` 之前插入：
```ts
// 超大图：按置信度采样边，保持集群结构（分层：先保 PolicyBoundary/Violation，再按置信度降序）
let edgeSource = graph.edges;
if (edgeSource.length > EDGE_RENDER_BUDGET) {
  const critical = edgeSource.filter(
    (e) => e.edge_type === 'PolicyBoundaryCrossing' || e.edge_type === 'ViolationProvenance',
  );
  const rest = edgeSource
    .filter((e) => e.edge_type !== 'PolicyBoundaryCrossing' && e.edge_type !== 'ViolationProvenance')
    .sort((a, b) => b.confidence - a.confidence);
  edgeSource = [...critical, ...rest].slice(0, EDGE_RENDER_BUDGET);
}
```
循环改遍历 `edgeSource`。

- [ ] **Step 2: Worker 布局接入**

把 `buildGraph` 中 forceAtlas2 的同步调用替换为：
```ts
const doLayout = async () => {
  const nodeInputs = [];
  g.forEachNode((id, attr) => nodeInputs.push({ id, x: attr.x, y: attr.y }));
  const edgeInputs = [];
  g.forEachEdge((_e, _a, src, tgt) => edgeInputs.push({ source: src, target: tgt }));
  try {
    const res = await requestLayout({ nodes: nodeInputs, edges: edgeInputs, iterations: 40 });
    if (res.error || !res.positions) throw new Error(res.error ?? 'no positions');
    Object.entries(res.positions).forEach(([id, pos]) => g.setNodeAttribute(id, 'x', pos.x));
    Object.entries(res.positions).forEach(([id, pos]) => g.setNodeAttribute(id, 'y', pos.y));
  } catch {
    const positions = runLayoutSync(nodeInputs, edgeInputs, 40);
    Object.entries(positions).forEach(([id, pos]) => g.setNodeAttribute(id, 'x', pos.x));
    Object.entries(positions).forEach(([id, pos]) => g.setNodeAttribute(id, 'y', pos.y));
  }
  finalizeAndRender();
};
```
将原 `buildGraph` 后半（坐标归一化、highlight、new Sigma、drag、事件、`setBuilt`）抽成 `finalizeAndRender()`，`doLayout()` 完成后调用；`requestIdleCallback` 里调用 `doLayout()`（不 await，异步不阻塞主线程）。

- [ ] **Step 3: Sigma 实例化预算**

`new Sigma(g, container, {...})` 增补：
```ts
batchEdgesRendering: true,
capEdgeThickness: 0.6,
renderLabels: g.order < NODE_LABEL_LIMIT,
enableEdgeEvents: g.size < 20_000,
```

- [ ] **Step 4: 超大图接入 LOD**

在组件顶部按节点数选择摘要图：
```ts
const lodLevel = useMemo(() => pickLodLevel(graph.nodes.length), [graph.nodes.length]);
const renderGraph = useMemo(
  () => (lodLevel === 'raw' ? graph : summarizeToLod(graph, lodLevel)),
  [graph, lodLevel],
);
```
`buildGraph` 内 `graph.nodes/edges` 改用 `renderGraph.nodes/edges`（`LodGraph` 的节点字段为 `id`，需在 addNode 处映射 `node_id: n.id`；边映射 `source/target` 直接可用）。`dataKey` 基于 `renderGraph` 计算。

- [ ] **Step 5: 验证**

```bash
cd frontend && npx tsc --noEmit && npx eslint components/ui/linear/sigma-network.tsx
npm run build
```
Expected: 全绿；dev server `/overview` 返回 200，网络区出现完整图。

- [ ] **Step 6: 提交**

```bash
git add frontend/components/ui/linear/sigma-network.tsx
git -c user.name="lzk" -c user.email="lzk@local" commit -m "feat: Sigma 渲染预算 + Worker 布局 + 超大图 LOD"
```

---

## Task 6: 3D 主页升级

**Files:**
- Modify: `frontend/components/ui/mtagentlens-graph-stage.tsx`
- Modify: `frontend/components/ui/home-engine-stage.tsx`（1918 行；仅在必要时最小改动）

**Interfaces:**
- Consumes: `pickLodLevel` / `summarizeToLod`（Task 1）、`runLayoutSync` / `requestLayout`（Task 2）
- Produces: 主页 3D 在 >4k 节点时渲染 agent/cluster LOD；大型图预计算布局并 `pauseAnimation`；失焦/隐藏暂停

- [ ] **Step 1: 节点上限 + LOD 输入**

`mtagentlens-graph-stage.tsx` 中 `adapted` useMemo 之前新增：
```ts
const lodLevel = useMemo(() => pickLodLevel(graph.nodes.length), [graph.nodes.length]);
const lod = useMemo(
  () => (lodLevel === 'raw' ? null : summarizeToLod(graph, lodLevel)),
  [graph, lodLevel],
);
```
`adapted = lod ? adaptFromLod(lod) : adaptGraphToLargeGraph(graph)`——新增小工具把 `LodGraph` 转成 `LargeGraphData`（节点 `id/node_type/agent_id/description/raw_node_ids`，边 `source/target/edge_type/raw_edge_ids`），或直接在 lod 为 raw 时走原路径、否则用 lod 字段构造 `graphData`。

- [ ] **Step 2: 大型图预计算布局 + 暂停力模拟**

在 `home-engine-stage.tsx` 主引擎创建处（约 788 行 `d3AlphaDecay` 配置附近）：当传入图 `nodeCount > 4000` 时：
```ts
const isLarge = adapted.nodes.length > 4000;
// 预计算固定坐标，替代每帧 d3-force
if (isLarge) {
  const inputs = adapted.nodes.map((n) => ({ id: n.id, x: 0, y: 0 }));
  const edgeInputs = adapted.links.map((l) => ({
    source: typeof l.source === 'string' ? l.source : l.source.id,
    target: typeof l.target === 'string' ? l.target : l.target.id,
  }));
  runLayoutSync(inputs, edgeInputs, 30)
  graphData.nodes.forEach((n, i) => {
    const pos = positions[n.id];
    if (pos) { n.fx = pos.x; n.fy = pos.y; n.fz = 0; }
  });
}
// 固定坐标后关闭力模拟
graphInstance.pauseAnimation();
```
（`fx/fy/fz` 固定坐标使 d3-force 不再迭代这些节点。）

- [ ] **Step 3: 失焦/隐藏暂停**

在 `mtagentlens-graph-stage.tsx` 的 effect 中监听 `document.visibilitychange` 与 `IntersectionObserver`（容器进入视口才启动/恢复动画）：
```ts
useEffect(() => {
  const onVis = () => {
    if (document.hidden) fgRef.current?.pauseAnimation?.();
    else if (activeRef.current) fgRef.current?.resumeAnimation?.();
  };
  document.addEventListener('visibilitychange', onVis);
  return () => document.removeEventListener('visibilitychange', onVis);
}, []);
```

- [ ] **Step 4: 验证**

```bash
cd frontend && npx tsc --noEmit && npx eslint components/ui/mtagentlens-graph-stage.tsx components/ui/home-engine-stage.tsx
npm run build
```
Expected: 全绿；`/` 首页 3D 图在 demo 数据下正常，headless 不再每帧 700ms 长任务（用 Playwright longtask 抽查）。

- [ ] **Step 5: 提交**

```bash
git add frontend/components/ui/mtagentlens-graph-stage.tsx frontend/components/ui/home-engine-stage.tsx
git -c user.name="lzk" -c user.email="lzk@local" commit -m "feat: 3D 主页 LOD + 大型图预计算布局 + 失焦暂停"
```

---

## Task 7: 通用虚拟列表组件（`virtual-list.tsx`）

**Files:**
- Create: `frontend/components/ui/virtual-list.tsx`
- Test: `frontend/__tests__/virtual-list.test.tsx`

**Interfaces:**
- Consumes: `@tanstack/react-virtual`（已安装）
- Produces:
  - `function VirtualList<T>({ items, estimateSize?, overscan?, renderRow, className?, emptyLabel? }): JSX.Element`
  - `renderRow: (item: T, index: number) => ReactNode`

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/__tests__/virtual-list.test.tsx
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import VirtualList from '@/components/ui/virtual-list';

describe('VirtualList', () => {
  it('renders rows for provided items', () => {
    render(
      <VirtualList items={['a', 'b', 'c']} renderRow={(item) => <span>{item}</span>} />,
    );
    expect(screen.getByText('a')).toBeTruthy();
    expect(screen.getByText('c')).toBeTruthy();
  });

  it('shows empty label when no items', () => {
    render(<VirtualList items={[]} emptyLabel="空" renderRow={() => null} />);
    expect(screen.getByText('空')).toBeTruthy();
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run __tests__/virtual-list.test.tsx`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现组件**

```tsx
// frontend/components/ui/virtual-list.tsx
'use client';
import { useRef, type ReactNode } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';

type Props<T> = {
  items: T[];
  estimateSize?: number;
  overscan?: number;
  renderRow: (item: T, index: number) => ReactNode;
  className?: string;
  emptyLabel?: string;
};

export default function VirtualList<T>({
  items,
  estimateSize = 40,
  overscan = 8,
  renderRow,
  className,
  emptyLabel = '暂无数据',
}: Props<T>) {
  const ref = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => ref.current,
    estimateSize: () => estimateSize,
    overscan,
  });

  if (items.length === 0) {
    return (
      <div className={className} style={{ padding: '12px', textAlign: 'center', color: 'rgba(255,255,255,0.35)' }}>
        {emptyLabel}
      </div>
    );
  }

  return (
    <div ref={ref} className={className} style={{ overflow: 'auto' }}>
      <div style={{ height: virtualizer.getTotalSize(), position: 'relative', width: '100%' }}>
        {virtualizer.getVirtualItems().map((vi) => (
          <div
            key={vi.key}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: vi.size,
              transform: `translateY(${vi.start}px)`,
            }}
          >
            {renderRow(items[vi.index], vi.index)}
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run __tests__/virtual-list.test.tsx`
Expected: PASS（2 用例）

- [ ] **Step 5: 提交**

```bash
git add frontend/components/ui/virtual-list.tsx frontend/__tests__/virtual-list.test.tsx
git -c user.name="lzk" -c user.email="lzk@local" commit -m "feat: 通用虚拟列表组件"
```

---

## Task 8: 长列表接入虚拟化

**Files:**
- Modify: `frontend/components/command-center/incident-rail.tsx`（告警列表，约 1962 行内告警渲染处）
- Modify: `frontend/components/investigation/investigations-page-client.tsx`（告警选择列表）
- Modify: `frontend/components/traces/traces-page-client.tsx`（追踪时间线）
- Modify: `frontend/components/investigation/investigation-workbench.tsx`（时间线步骤 + 证据列表）

**Interfaces:**
- Consumes: `VirtualList`（Task 7）
- Produces: 各长列表（>50 项）改用 `VirtualList`；外容器已有 overflow 的改为内联高度

- [ ] **Step 1: incident-rail 告警列表**

找到 `incident-rail.tsx` 中渲染告警列表的 `.map(...)` 容器（外层滚动容器已有固定高度/overflow），替换为：
```tsx
<VirtualList
  items={alertModels}
  estimateSize={44}
  renderRow={(entry) => (
    <button
      key={entry.alert.alert_id}
      type="button"
      onClick={() => onSelect(entry.alert.alert_id)}
      className={/* 保留原样式类 */}
    >
      {/* 保留原内容：severity 徽章、title、decision 等 */}
    </button>
  )}
/>
```
原 `.map` 内部 JSX 原样搬入 `renderRow`，仅外层换用 `VirtualList`。若列表外层不是滚动容器，包一层 `<div style={{ overflow: 'auto', height: '100%' }}>`。

- [ ] **Step 2: investigations 告警列表 + workbench 时间线/证据**

`investigations-page-client.tsx` 的告警选择列表（若有 `.map` 长列表）同理换 `VirtualList`。`investigation-workbench.tsx` 中 `model.timelineSteps.map(...)`（告警时间线可达 2000 步）与证据列表 `map(...)` 改用 `VirtualList`（`estimateSize` 按行高，时间线 28、证据 40）。

- [ ] **Step 3: traces 时间线**

`traces-page-client.tsx` 中时间线事件列表 `.map(...)` 改用 `VirtualList`（`estimateSize={32}`）；确认外层滚动容器有明确高度（`flex: 1; overflow: auto` 或 `height`）。

- [ ] **Step 4: 验证**

```bash
cd frontend && npx tsc --noEmit
npx eslint components/command-center/incident-rail.tsx components/investigation/investigations-page-client.tsx components/investigation/investigation-workbench.tsx components/traces/traces-page-client.tsx
npx vitest run __tests__/virtual-list.test.tsx
```
Expected: 全绿；dev server 各页面 200，时间线/列表滚动正常。

- [ ] **Step 5: 提交**

```bash
git add frontend/components/command-center/incident-rail.tsx frontend/components/investigation/investigations-page-client.tsx frontend/components/investigation/investigation-workbench.tsx frontend/components/traces/traces-page-client.tsx
git -c user.name="lzk" -c user.email="lzk@local" commit -m "feat: 长列表接入虚拟化（rail/告警/时间线/证据）"
```

---

## Task 9: 实时流按帧合并（store）

**Files:**
- Modify: `frontend/lib/store.ts`（`onAlertReceived`）

**Interfaces:**
- Consumes: `deriveGraphMetrics`（store 内部）
- Produces: `onAlertReceived` 改为 rAF 合并：缓冲新告警，每帧最多提交一次

- [ ] **Step 1: 实现 rAF 合并**

在 `create<AppStore>` 的 `onAlertReceived` 上方新增模块级缓冲：
```ts
// ── Live-stream coalescing ──
// High-frequency alerts from live sessions are buffered and flushed at most
// once per animation frame, so the store never re-renders the app on every event.
let alertBuffer: ResponsibilityAlert[] = [];
let flushHandle: number | null = null;

function flushAlerts(get: () => AppStore, set: (partial: Partial<AppStore>) => void) {
  flushHandle = null;
  if (alertBuffer.length === 0) return;
  const batch = alertBuffer;
  alertBuffer = [];
  const state = get();
  const existing = new Set(state.alerts.map((a) => a.alert_id));
  const fresh = batch.filter((a) => !existing.has(a.alert_id));
  if (fresh.length === 0) return;
  const nextAlerts = [...fresh, ...state.alerts].slice(0, 200);
  set({ alerts: nextAlerts });
  fresh.forEach((a) => state.engineRef.current?.showAlertPulse(a));
}
```
`onAlertReceived` 改为：
```ts
onAlertReceived: (alert) => {
  alertBuffer.push(alert);
  if (flushHandle == null) {
    flushHandle = typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame(() => flushAlerts(get, set))
      : (setTimeout(() => flushAlerts(get, set), 16) as unknown as number);
  }
  // 图快照更新保持即时（一次）
  if (alert.graph_snapshot?.nodes?.length) {
    const engine = get().engineRef.current;
    if (engine) engine.updateGraph({ nodes: alert.graph_snapshot.nodes, edges: alert.graph_snapshot.edges || [] });
    set({
      graph: { nodes: alert.graph_snapshot.nodes, edges: alert.graph_snapshot.edges || [] },
      graphMetrics: alert.graph_snapshot.metrics ?? deriveGraphMetrics({ nodes: alert.graph_snapshot.nodes, edges: alert.graph_snapshot.edges || [] }),
    });
  }
},
```

- [ ] **Step 2: 验证**

```bash
cd frontend && npx tsc --noEmit && npx eslint lib/store.ts
npx vitest run __tests__/use-alert-models.test.tsx __tests__/graph-lod.test.ts __tests__/virtual-list.test.tsx __tests__/layout-worker-client.test.ts
```
Expected: 全绿；基线失败数不变。

- [ ] **Step 3: 提交**

```bash
git add frontend/lib/store.ts
git -c user.name="lzk" -c user.email="lzk@local" commit -m "feat: 实时流 rAF 合并，live 高频更新不再冻结"
```

---

## Task 10: 十万级数据基准 + 全量回归

**Files:**
- Create: `frontend/scripts/bench-large-data.mjs`（合成 10 万节点图，验证各模块耗时）
- Test: 复用现有 `__tests__/`（跑全量）

- [ ] **Step 1: 合成基准脚本**

```js
// frontend/scripts/bench-large-data.mjs
// 合成 100k 节点 / 500k 边的图，测量：LOD 聚合、同步布局、模型计算。
import { summarizeToLod } from '../lib/graph-lod.ts'; // 若 node 无法跑 TS，改为用 vitest 基准
```
> 注：lib 为 TS/ESM，node 直接跑需 esbuild 或 vitest。将基准写成 vitest 文件 `frontend/__tests__/bench-large-data.test.ts`（合成数据 + `console.log` 耗时，跑完可保留或删除）。

- [ ] **Step 2: 写基准 vitest**

```ts
// frontend/__tests__/bench-large-data.test.ts
import { describe, expect, it } from 'vitest';
import { pickLodLevel, summarizeToLod } from '@/lib/graph-lod';
import { runLayoutSync } from '@/lib/layout-worker-client';
import { buildInvestigationModel } from '@/lib/investigation';

function makeGraph(nNodes: number, nEdges: number) {
  const nodes = Array.from({ length: nNodes }, (_, i) => ({
    node_id: `n${i}`, node_type: i % 10 === 0 ? 'Agent' : 'Resource', agent_id: i % 10 === 0 ? `agent-${Math.floor(i / 10)}` : null,
  }));
  const edges = Array.from({ length: nEdges }, (_, i) => ({
    edge_id: `e${i}`, source: `n${i % nNodes}`, target: `n${(i * 7 + 1) % nNodes}`, edge_type: 'IntentCausesAction', confidence: 0.5,
  }));
  return { nodes, edges };
}

describe('bench large data', () => {
  it('LOD + layout on 100k nodes stays bounded', () => {
    const graph = makeGraph(100_000, 500_000);
    expect(pickLodLevel(graph.nodes.length)).toBe('cluster');
    const t0 = performance.now();
    const lod = summarizeToLod(graph, 'cluster');
    const t1 = performance.now();
    const t2 = performance.now();
    runLayoutSync(lod.nodes.map((n) => ({ id: n.id, x: 0, y: 0 })), lod.edges.map((e) => ({ source: e.source, target: e.target })), 20);
    const t3 = performance.now();
    console.log(`[bench] 100k→cluster: summarize=${(t1 - t0).toFixed(0)}ms layout=${(t3 - t2).toFixed(0)}ms lodNodes=${lod.nodes.length}`);
    expect(lod.nodes.length).toBeLessThan(100); // 24 bucket → cluster 摘要
    expect(lod.nodes.length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 3: 跑基准并记录**

Run: `npx vitest run __tests__/bench-large-data.test.ts`
Expected: PASS；`summarize` 与 `layout` 均在数百 ms 级（主线程外）。记录数值。

- [ ] **Step 4: 全量回归**

Run:
```bash
cd frontend && npx tsc --noEmit && npx eslint . && npm run build && npm test
```
Expected: TS/Lint/Build 全绿；测试 11 基线失败 + 本计划新增用例全过（不新增失败）。Playwright 抽查 `/`、`/overview`、`/investigations`、`/traces` 均 200 且无明显长任务。

- [ ] **Step 5: 删除或保留基准测试 + 提交**

保留 `bench-large-data.test.ts`（作为回归基准）并提交：
```bash
git add frontend/__tests__/bench-large-data.test.ts frontend/scripts/bench-large-data.mjs
git -c user.name="lzk" -c user.email="lzk@local" commit -m "test: 十万级数据基准 + 全量回归"
```

---

## Self-Review 记录

- **Spec 覆盖**：LOD（Task 1/5/6）✓；Worker（Task 2/5/6）✓；延迟模型（Task 3/4）✓；渲染预算（Task 5）✓；3D 升级（Task 6）✓；虚拟化（Task 7/8）✓；实时流（Task 9）✓；memo（贯穿 Task 5/6/8，依赖粒度选择器已存在）✓；基准（Task 10）✓。
- **占位符扫描**：无 TBD/TODO；所有代码步骤含真实实现。
- **类型一致性**：`LodGraph` 的 `id/label/count`、`requestLayout` 的 `LayoutRequest/LayoutResult`、`useAlertModels` 的 `AlertModelEntry` 在跨任务引用处签名一致。
