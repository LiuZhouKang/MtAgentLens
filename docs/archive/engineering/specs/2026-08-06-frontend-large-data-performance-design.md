# 前端大规模数据性能架构设计

**日期**: 2026-08-06
**状态**: 已批准（用户确认方案 A）
**目标规模**: 十万级节点 / 百万级边 / 千级告警

## 背景与问题

当前前端在数据规模较大时（真实 run 已达 1661 节点 / 3780 边 / 11 条告警）出现明显卡死：

1. 三处（layout / command-center / investigations）在渲染时对**全部告警**同步执行 `buildInvestigationModel`（O(边数) 每告警），每次数据变化都阻塞主线程。
2. 主页 3D 力导向图（3d-force-graph）数千节点每帧力模拟 + three.js 精灵渲染，headless 下实测每帧 ~700ms。
3. Sigma 网络对海量边无渲染预算；forceAtlas2 布局在主线程。
4. 长列表（告警列表、时间线）无虚拟化。
5. 实时流（live）高频更新无合并，触发全量重渲染。
6. 无选择性 memo，重组件广泛重渲染。

## 架构原则

「**数据降维 + 离主线程 + 只渲染可见**」三层组合（业界大图可视化标准做法）：

- **数据降维**：按规模自动聚合（LOD），渲染摘要而非原始百万边。
- **离主线程**：图布局、图聚合、模型计算全部 Web Worker / 闲时化。
- **只渲染可见**：列表虚拟化 + 渲染预算 + 缩放 LOD。

## 支柱 1 — LOD 图聚合管线（`lib/graph-lod.ts`）

按节点数自动选择聚合层级，支持下钻：

| LOD | 触发规模 | 内容 |
|-----|---------|------|
| `raw` | ≤ 4,000 节点 | 全保真 |
| `agent` | ≤ 20,000 节点 | 按 `agent_id` 聚合（复用/强化 `large-graph-adapter.ts`）|
| `cluster` | > 20,000 节点 | 按集群聚合 |

- `pickLodLevel(nodeCount, zoom?)`：规模 + 缩放共同决定层级；缩放钻入时升级细节。
- 聚合为 **cluster 级直连路径**（`summarizeClusterDirect`）：O(n+e) 哈希桶直接聚合，跳过重型 adapter；10 万节点/50 万边 ~271ms（实测）。**不采用 Web Worker 聚合**——structuredClone 传输 74MB 图需 ~718ms 主线程，反而更慢。
- 确定性位置：用 `fnv1a32` 哈希播种，LOD 切换时节点位置稳定，避免视觉跳变。
- 输出类型：`LodGraph = { nodes, edges, level, rawCount }`。
- 消费钩子：`useGraphLod(graph, zoom)` → 按（graph 指纹, 层级）memo，重聚合在 worker 中完成。

## 支柱 2 — Web Worker 图布局（`lib/layout.worker.ts` + `lib/layout-worker-client.ts`）

- 自建 worker（避免 Next.js 静态导出对官方 supervisor Worker URL 的打包风险）。
- 协议：主线程 post `{ id, nodes, edges, iterations, settings }` → worker 构建 graphology 图并运行 forceAtlas2（含 Barnes-Hut）→ post `{ id, positions }`。
- 客户端：单例 worker、Promise 化请求（`requestLayout()`）、失败自动回退主线程同步 `forceAtlas2.assign`。
- Sigma 网络与 3D 主页共用；大型图用 worker 布局，主线程始终可交互。

## 支柱 3 — 延迟模型计算（`lib/use-alert-models.ts`）

- 模块级 LRU 缓存 `Map<alertId|graphFp, InvestigationModel>`（容量上限 ~200）。
- 钩子 `useAlertModels(alerts, graph, evidence)`：
  - 同步返回廉价列表（`{ alert, model? }`，未缓存项 model 为 null）。
  - 已缓存项直接复用；未缓存项在 `requestIdleCallback` 分块渐进计算，结果写回缓存。
  - 返回排序后的模型数组；随渐进计算自动填充。
- 替换三处调用点：
  - `app/(main)/layout.tsx`（IncidentRail 用）→ 钩子 + `useDeferredValue(alerts, graph)`，首帧永不阻塞。
  - `components/command-center/command-center-page.tsx`。
  - `components/investigation/investigations-page-client.tsx`。
- 布局层选中的告警模型同步计算（保证选中项立即可见），其余惰性。

## 支柱 4 — 渲染预算与缩放 LOD

### Sigma 网络（`components/ui/linear/sigma-network.tsx`）
- 边渲染预算：`edges > 20,000` 时按 `confidence` 降序采样至预算内；启用 `batchEdgesRendering`。
- `capEdgeThickness: 0.6`；大型图 `renderLabels = false`、`enableEdgeEvents = false`。
- 布局走 worker（支柱 2）；构建期间 NetworkLoading 继续展示。
- 超大图接收 `useGraphLod` 的 agent/cluster 摘要。

### 3D 主页（`mtagentlens-graph-stage.tsx` + `home-engine-stage.tsx`）
- 节点上限 + sprite 实例化（共享几何/材质）替代逐节点 `nodeThreeObject`。
- 图大时：预计算布局（worker）→ 固定坐标，关闭每帧 d3-force（`pauseAnimation`）。
- tab 隐藏 / 面板失焦 / `active=false` 时暂停。
- 接入 `useGraphLod` 摘要。

## 支柱 5 — 全列表虚拟化（`@tanstack/react-virtual`）

已安装，无需新依赖。通用组件 `components/ui/virtual-list.tsx`：

- `VirtualList<T>({ items, estimateHeight, renderRow, overscan })`。
- 应用点：
  - `incident-rail.tsx` 告警列表。
  - `investigations-page-client` 告警选择列表 + workbench 时间线。
  - `traces-page-client` 追踪时间线。
  - workbench 证据列表、时间线步骤。

## 支柱 6 — 实时流批量合并（`lib/store.ts`）

- `onAlertReceived` 改为 rAF 合并：缓冲新告警，每帧最多提交一次 store；保留 200 条上限。
- 重组件一律粒度订阅（zustand selector）+ `useDeferredValue` 接收高频数据。
- 探索 `startTransition` 包裹高频 set 以避免阻塞交互。

## 支柱 7 — 选择性 memo（贯穿）

- 重叶子组件 `React.memo`（rail 项、图表面板、节点行），配合稳定 props（`useCallback`）。
- 不引入 React Compiler（与自建 worker/动态导入组合有风险，手动 memo 足够）。

## 分层推进与验证

1. **基础层**：`graph-lod.ts` + `layout.worker.ts` + `use-alert-models.ts`（消灭主线程阻塞）。
2. **渲染层**：Sigma/3D 渲染预算 + 缩放 LOD。
3. **虚拟化**：`virtual-list.tsx` + 各列表接入。
4. **实时流**：store rAF 合并。
5. **收尾**：100k 节点合成数据基准 + 回归。

每阶段：
- `npx tsc --noEmit` + `npx eslint` + `npm run build` 全绿。
- 新增单元测试（LOD 正确性/确定性、模型缓存、worker 协议 mock、虚拟化）。
- 现有 11 个会话前测试失败基线不得新增回归。

## 关键风险

- **Worker 与 Next.js 静态导出**：自建 worker 用 `new Worker(new URL(...))`，需在 `next.config.ts` 确认打包（turbopack/dev 与 export/prod 行为）；失败时自动回退主线程同步布局，保证不破坏功能。
- **LOD 切换视觉稳定性**：确定性哈希播种 + 过渡动画缓解。
- **超大规模边采样**：保证采样不破坏集群结构（按置信度 + 边类型分层采样）。

## 非目标

- 不重写图形库；不引入 React Compiler；不改 Rust/数据服务端（`?lean=1` 已落地）。
