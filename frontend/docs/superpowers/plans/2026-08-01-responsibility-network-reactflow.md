# 交互式责任网络 — ReactFlow 重写实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 用 ReactFlow v12 + dagre 自动布局完全重写侧边栏"交互式责任网络"，支持7种节点类型和7种边类型的顶级可视化。

**Architecture:** ResponsibilityNetwork 主组件接收节点/边数据，通过 dagre 计算分层布局，渲染自定义节点（按类型着色+图标）和自定义边（按类型着色+标签+动画）。incident-rail.tsx 仅修改3行（import + 数据函数增强 + JSX替换）。

**Tech Stack:** @xyflow/react v12, dagre, motion/react, Tailwind CSS 4, TypeScript 5

## Global Constraints

- 深色主题，匹配现有 dark mode 视觉体系
- 仅修改 incident-rail.tsx 中 TraceNetwork 相关部分（~5行），其他1840+行不动
- 不引入 unsafe 代码
- 响应式适配 540px 侧边栏面板宽度
- 保持现有组件的文件命名约定和代码风格

---

### Task 1: 安装 dagre 布局库

**Files:**
- Modify: `frontend/package.json`

**Interfaces:**
- Consumes: 无
- Produces: `dagre` + `@types/dagre` 可用依赖

- [ ] **Step 1: 安装 dagre 和类型定义**

```bash
cd frontend && npm install dagre @types/dagre
```

- [ ] **Step 2: 验证安装**

```bash
cd frontend && node -e "const dagre = require('dagre'); console.log('dagre OK:', typeof dagre.graphlib)" 
```

---

### Task 2: 创建 ResponsibilityNode 自定义节点组件

**Files:**
- Create: `frontend/components/ui/responsibility-node.tsx`

**Interfaces:**
- Consumes: `@xyflow/react` (Handle, Position, NodeProps, Node)
- Produces: `ResponsibilityNodeData` 类型, `ResponsibilityNode` 组件 (nodeTypes: { responsibility: ResponsibilityNode })

7种节点类型的颜色/图标映射表：

| nodeType | color | icon |
|----------|-------|------|
| Agent | #60a5fa (blue) | organization |
| Resource | #34d399 (emerald) | file |
| PolicyBoundary | #f43f5e (rose) | shield |
| Process | #fbbf24 (amber) | server-process |
| ToolCall | #a78bfa (purple) | tools |
| Credential | #fb923c (orange) | key |
| Context | #22d3ee (cyan) | symbol-namespace |
| default | #6b7280 (gray) | circle-filled |

- [ ] **Step 1: 创建 responsibility-node.tsx**

```tsx
'use client';

import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import { motion } from 'motion/react';
import { Codicon } from '@/components/ui/codicon';

export type ResponsibilityNodeData = {
  label: string;
  nodeType: string;
};

export type ResponsibilityNodeType = Node<ResponsibilityNodeData, 'responsibility'>;

const NODE_STYLE: Record<string, { color: string; icon: string; label: string }> = {
  Agent: { color: '#60a5fa', icon: 'organization', label: 'Agent' },
  Resource: { color: '#34d399', icon: 'file', label: 'Resource' },
  PolicyBoundary: { color: '#f43f5e', icon: 'shield', label: 'Boundary' },
  Process: { color: '#fbbf24', icon: 'server-process', label: 'Process' },
  ToolCall: { color: '#a78bfa', icon: 'tools', label: 'Tool' },
  Credential: { color: '#fb923c', icon: 'key', label: 'Cred' },
  Context: { color: '#22d3ee', icon: 'symbol-namespace', label: 'Context' },
};

const DEFAULT_STYLE = { color: '#6b7280', icon: 'circle-filled', label: 'Node' };

export function ResponsibilityNode({ data, selected }: NodeProps<ResponsibilityNodeType>) {
  const style = NODE_STYLE[data.nodeType] ?? DEFAULT_STYLE;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.8, filter: 'blur(4px)' }}
      animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      className="group relative flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl border bg-[#141415]/90 backdrop-blur-md transition-all duration-300 min-w-[140px]"
      style={{
        borderColor: selected ? style.color : 'rgba(255,255,255,0.08)',
        boxShadow: selected
          ? `0 0 24px ${style.color}22, inset 0 0 12px ${style.color}08`
          : '0 0 0 0 transparent',
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!w-2 !h-2 !border !border-white/10 !bg-white/20"
      />

      <div
        className="flex items-center justify-center w-8 h-8 rounded-lg border transition-all duration-300 shrink-0"
        style={{
          borderColor: `${style.color}33`,
          backgroundColor: `${style.color}10`,
        }}
      >
        <Codicon name={style.icon} size={14} style={{ color: style.color }} />
      </div>

      <div className="flex flex-col gap-0.5 min-w-0">
        <strong className="text-xs font-medium tracking-wide text-white/80 truncate max-w-[120px]">
          {data.label}
        </strong>
        <span
          className="text-[9px] font-medium uppercase tracking-widest truncate"
          style={{ color: `${style.color}99` }}
        >
          {style.label}
        </span>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        className="!w-2 !h-2 !border !border-white/10 !bg-white/20"
      />
    </motion.div>
  );
}
```

- [ ] **Step 2: 类型检查通过**

```bash
cd frontend && npx tsc --noEmit components/ui/responsibility-node.tsx 2>&1 | head -20
```

---

### Task 3: 创建 ResponsibilityEdge 自定义边组件

**Files:**
- Create: `frontend/components/ui/responsibility-edge.tsx`

**Interfaces:**
- Consumes: `@xyflow/react` (BaseEdge, EdgeLabelRenderer, getBezierPath, EdgeProps)
- Produces: `ResponsibilityEdge` 组件 (edgeTypes: { responsibility: ResponsibilityEdge })

7种边类型的颜色映射：

| edgeType | color | dashArray | label |
|----------|-------|-----------|-------|
| IntentCausesAction | #60a5fa | none | 意图→动作 |
| PolicyBoundaryCrossing | #f43f5e | 6 4 | 边界穿越 |
| AgentDelegatesTo | #a78bfa | none | 委派至 |
| AuthorityTransfer | #fbbf24 | none | 权限转移 |
| Delegation | #8b5cf6 | 8 4 | 委派 |
| TaintPropagation | #fb923c | 3 4 | 污点传播 |
| ViolationProvenance | #ef4444 | 6 4 | 违规溯源 |
| default | #6b7280 | none | 关联 |

- [ ] **Step 1: 创建 responsibility-edge.tsx**

```tsx
'use client';

import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from '@xyflow/react';

export type ResponsibilityEdgeData = {
  edgeType: string;
  confidence: number;
};

const EDGE_STYLE: Record<string, { color: string; dashArray?: string; label: string }> = {
  IntentCausesAction: { color: '#60a5fa', label: '意图→动作' },
  PolicyBoundaryCrossing: { color: '#f43f5e', dashArray: '6 4', label: '边界穿越' },
  AgentDelegatesTo: { color: '#a78bfa', label: '委派至' },
  AuthorityTransfer: { color: '#fbbf24', label: '权限转移' },
  Delegation: { color: '#8b5cf6', dashArray: '8 4', label: '委派' },
  TaintPropagation: { color: '#fb923c', dashArray: '3 4', label: '污点传播' },
  ViolationProvenance: { color: '#ef4444', dashArray: '6 4', label: '违规溯源' },
};

const DEFAULT_STYLE = { color: '#6b7280', label: '关联' };

export function ResponsibilityEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  markerEnd,
  style = {},
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  if (!edgePath) return null;

  const edgeStyle = EDGE_STYLE[data?.edgeType ?? ''] ?? DEFAULT_STYLE;
  const confidencePercent = data?.confidence != null ? `${Math.round(data.confidence * 100)}%` : '';
  const label = confidencePercent ? `${edgeStyle.label} · ${confidencePercent}` : edgeStyle.label;

  const strokeColor = edgeStyle.color;
  const baseOpacity = 0.35;
  const glowOpacity = 0.7;

  return (
    <>
      {/* Base edge (dim background) */}
      <BaseEdge
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          ...style,
          stroke: strokeColor,
          strokeWidth: 1.5,
          opacity: baseOpacity,
          strokeDasharray: edgeStyle.dashArray,
        }}
      />

      {/* Glow edge (brighter on top) */}
      <BaseEdge
        path={edgePath}
        style={{
          ...style,
          stroke: strokeColor,
          strokeWidth: 3,
          strokeLinecap: 'round',
          opacity: glowOpacity,
          strokeDasharray: edgeStyle.dashArray,
          filter: `drop-shadow(0 0 4px ${strokeColor}44)`,
        }}
      />

      {/* Edge label */}
      <EdgeLabelRenderer>
        <div
          className="absolute pointer-events-none nodrag nopan"
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
          }}
        >
          <span
            className="inline-block rounded-md px-2 py-0.5 text-[9px] font-medium tracking-wide whitespace-nowrap"
            style={{
              color: strokeColor,
              backgroundColor: `${strokeColor}10`,
              border: `1px solid ${strokeColor}22`,
            }}
          >
            {label}
          </span>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
```

- [ ] **Step 2: 类型检查通过**

---

### Task 4: 创建 ResponsibilityNetwork 主组件

**Files:**
- Create: `frontend/components/ui/responsibility-network.tsx`

**Interfaces:**
- Consumes: `@xyflow/react` (ReactFlow, Background, BackgroundVariant, Controls, MiniMap, useNodesState, useEdgesState, Position, type Node, type Edge), `dagre`, `motion/react` (AnimatePresence)
- Produces: `ResponsibilityNetwork` 组件

dagre 布局参数:
- rankdir: 'LR'
- nodesep: 60
- ranksep: 180
- marginx: 30
- marginy: 30

- [ ] **Step 1: 创建 responsibility-network.tsx**

```tsx
'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  Position,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import { motion } from 'motion/react';

import { ResponsibilityNode, type ResponsibilityNodeData } from '@/components/ui/responsibility-node';
import { ResponsibilityEdge, type ResponsibilityEdgeData } from '@/components/ui/responsibility-edge';

// ── node types & edge types (stable references) ──
const nodeTypes = { responsibility: ResponsibilityNode };
const edgeTypes = { responsibility: ResponsibilityEdge };

// ── dagre layout ──
const NODE_WIDTH = 160;
const NODE_HEIGHT = 60;

function buildDagreLayout(
  nodes: { id: string; label: string; nodeType: string }[],
  edges: { id: string; source: string; target: string }[],
): { nodes: Node<ResponsibilityNodeData>[]; edges: Edge<ResponsibilityEdgeData>[] } {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 180, marginx: 30, marginy: 30 });

  nodes.forEach((n) => {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  });

  edges.forEach((e) => {
    g.setEdge(e.source, e.target);
  });

  dagre.layout(g);

  const rfNodes: Node<ResponsibilityNodeData>[] = nodes.map((n) => {
    const dagreNode = g.node(n.id);
    return {
      id: n.id,
      type: 'responsibility',
      position: {
        x: dagreNode.x - NODE_WIDTH / 2,
        y: dagreNode.y - NODE_HEIGHT / 2,
      },
      data: { label: n.label, nodeType: n.nodeType },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    };
  });

  const rfEdges: Edge<ResponsibilityEdgeData>[] = edges.map((e, i) => ({
    id: e.id || `e-${e.source}-${e.target}-${i}`,
    source: e.source,
    target: e.target,
    type: 'responsibility',
    data: {
      edgeType: (e as { edgeType?: string }).edgeType ?? '',
      confidence: (e as { confidence?: number }).confidence ?? 0,
    },
    animated: false,
  }));

  return { nodes: rfNodes, edges: rfEdges };
}

// ── sync helper: only update when data truly changes ──
function useStableSync<T>(
  next: T[],
  setter: (items: T[]) => void,
  getKey: (item: T) => string,
) {
  const prevKey = useMemo(() => next.map(getKey).join('|'), [next, getKey]);
  useEffect(() => { setter(next); }, [prevKey]); // eslint-disable-line react-hooks/exhaustive-deps
}

// ── props ──
export type ResponsibilityNetworkNode = {
  id: string;
  label: string;
  nodeType: string;
};

export type ResponsibilityNetworkEdge = {
  id: string;
  source: string;
  target: string;
  edgeType: string;
  confidence: number;
  evidenceRefs?: string[];
};

type ResponsibilityNetworkProps = {
  nodes: ResponsibilityNetworkNode[];
  edges: ResponsibilityNetworkEdge[];
  height?: number;
};

// ── component ──
export function ResponsibilityNetwork({
  nodes: rawNodes,
  edges: rawEdges,
  height = 320,
}: ResponsibilityNetworkProps) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  // Compute dagre layout (stable: keyed on id/label/nodeType signature)
  const { nodes: layoutNodes, edges: layoutEdges } = useMemo(
    () => buildDagreLayout(rawNodes, rawEdges),
    [
      rawNodes.map((n) => `${n.id}:${n.label}:${n.nodeType}`).join('|'),
      rawEdges.map((e) => `${e.id}:${e.source}:${e.target}:${e.edgeType}:${e.confidence}`).join('|'),
    ].join('::'),
  );

  // Highlight connected edges when a node is selected
  const displayedEdges = useMemo(() => {
    if (!selectedNodeId) return layoutEdges;
    return layoutEdges.map((edge) => ({
      ...edge,
      style: {
        ...edge.style,
        opacity:
          edge.source === selectedNodeId || edge.target === selectedNodeId ? 1 : 0.12,
        stroke:
          edge.source === selectedNodeId || edge.target === selectedNodeId
            ? undefined
            : '#6b728044',
      },
      animated: edge.source === selectedNodeId || edge.target === selectedNodeId,
    }));
  }, [layoutEdges, selectedNodeId]);

  const displayedNodes = useMemo(() => {
    if (!selectedNodeId) return layoutNodes;
    return layoutNodes.map((node) => ({
      ...node,
      style: {
        ...node.style,
        opacity: node.id === selectedNodeId ? 1 : 0.45,
      },
      data: {
        ...node.data,
        selected: node.id === selectedNodeId,
      },
    }));
  }, [layoutNodes, selectedNodeId]);

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<Node<ResponsibilityNodeData>>([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState<Edge<ResponsibilityEdgeData>>([]);

  useStableSync(displayedNodes, setRfNodes, (n) => n.id);
  useStableSync(displayedEdges, setRfEdges, (e) => e.id);

  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node<ResponsibilityNodeData>) => {
      setSelectedNodeId((prev) => (prev === node.id ? null : node.id));
    },
    [],
  );

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null);
  }, []);

  // ── empty state ──
  if (!rawNodes.length) {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="flex items-center justify-center border border-white/5 bg-white/[0.02] rounded-2xl w-full"
        style={{ height: `${height}px` }}
      >
        <div className="flex flex-col items-center gap-2">
          <span className="text-2xl opacity-30">⚡</span>
          <p className="text-xs font-light text-white/40">暂无责任链路数据</p>
          <p className="text-[10px] font-light text-white/20">等待运行时事件触发归因分析</p>
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 6, filter: 'blur(4px)' }}
      animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
      transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
      className="relative w-full rounded-2xl border border-white/5 bg-[#141415]/50 overflow-hidden"
      style={{ height: `${height}px` }}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.15}
        maxZoom={2.5}
        panOnDrag
        zoomOnScroll
        zoomOnPinch
        autoPanOnNodeDrag
        autoPanSpeed={20}
        proOptions={{ hideAttribution: true }}
        colorMode="dark"
        className="react-flow-custom-dark"
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        defaultEdgeOptions={{ type: 'responsibility' }}
      >
        <MiniMap
          pannable
          zoomable
          position="bottom-left"
          nodeColor={(n) => {
            const d = (n as Node<ResponsibilityNodeData>).data;
            const colors: Record<string, string> = {
              Agent: '#60a5fa',
              Resource: '#34d399',
              PolicyBoundary: '#f43f5e',
              Process: '#fbbf24',
              ToolCall: '#a78bfa',
              Credential: '#fb923c',
              Context: '#22d3ee',
            };
            return colors[d?.nodeType ?? ''] ?? '#6b7280';
          }}
          maskColor="rgba(0, 0, 0, 0.3)"
          className="!bg-[#141415]/80 !border-white/5 !rounded-xl !overflow-hidden"
        />
        <Controls
          showZoom
          showFitView
          showInteractive={false}
          position="bottom-right"
          className="!bg-[#141415]/80 !border-white/5 !rounded-xl !overflow-hidden"
        />
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          color="rgba(255,255,255,0.04)"
        />
      </ReactFlow>
    </motion.div>
  );
}
```

- [ ] **Step 2: 类型检查通过**

---

### Task 5: 修改 incident-rail.tsx — 替换 TraceNetwork

**Files:**
- Modify: `frontend/components/command-center/incident-rail.tsx`

**Interfaces:**
- Consumes: `InvestigationModel` (已有)
- Produces: `traceFlowData` 增强为包含 edge_id, confidence, edgeType（已有变量名不变）

**具体改动（共3处）：**

1. **Import 替换** (line 16):
   - 旧: `import { TraceNetwork } from '@/components/command-center/trace-network';`
   - 新: `import { ResponsibilityNetwork } from '@/components/ui/responsibility-network';`

2. **buildReactFlowData 增强** (lines 274-289):
   - 增加 edge_id、confidence、edgeType 传递
   - 移除不再需要的 icon/meta 简化逻辑

3. **JSX 替换** (lines 1592-1593):
   - 旧: `<TraceNetwork nodes={traceFlowData.nodes} edges={traceFlowData.edges} height={320} />`
   - 新: `<ResponsibilityNetwork nodes={traceFlowData.nodes} edges={traceFlowData.edges} height={360} />`

- [ ] **Step 1: 替换 import**

```typescript
// 删除 line 16:
// import { TraceNetwork } from '@/components/command-center/trace-network';

// 在 Codicon import 附近添加（已在 line 15 有 Codicon import）:
import { ResponsibilityNetwork } from '@/components/ui/responsibility-network';
```

- [ ] **Step 2: 增强 buildReactFlowData 函数**

```typescript
function buildReactFlowData(model: InvestigationModel) {
  const nodes = model.pathNodes.map(node => ({
    id: node.nodeId,
    label: node.label,
    nodeType: node.nodeType || 'NODE',
  }));

  const edges = model.pathEdges.map(edge => ({
    id: edge.edge_id,
    source: edge.source,
    target: edge.target,
    edgeType: edge.edge_type,
    confidence: edge.confidence,
    evidenceRefs: edge.evidence_refs,
  }));

  return { nodes, edges };
}
```

- [ ] **Step 3: 替换 JSX 调用并增加高度**

```tsx
// 原 line 1593:
// <TraceNetwork nodes={traceFlowData.nodes} edges={traceFlowData.edges} height={320} />
// 新:
<ResponsibilityNetwork nodes={traceFlowData.nodes} edges={traceFlowData.edges} height={360} />
```

- [ ] **Step 4: TypeScript 类型检查**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -30
```

- [ ] **Step 5: 构建验证**

```bash
cd frontend && npm run build 2>&1 | tail -20
```

---

### Task 6: 端到端验证

- [ ] **Step 1: 启动开发服务器验证**

```bash
cd frontend && npm run dev
```

验证要点:
- 侧边栏 → 链路 tab → "交互式责任网络" 卡片
- 节点按类型着色显示（不同颜色+图标）
- 边显示类型标签 + 置信度百分比
- dagre 自动布局，从左到右分层排列
- 点击节点高亮关联路径
- 点击空白取消选中
- MiniMap 正确渲染
- 空数据时显示优雅的空状态
- 拖拽、缩放正常工作
- 深色主题匹配
