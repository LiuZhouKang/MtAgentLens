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

  const d = data as ResponsibilityEdgeData | undefined;
  const edgeType = (d?.edgeType ?? '') as string;
  const confidenceVal = (d?.confidence ?? null) as number | null;

  const edgeStyle = EDGE_STYLE[edgeType] ?? DEFAULT_STYLE;
  const confidencePercent =
    confidenceVal != null ? `${Math.round(confidenceVal * 100)}%` : '';
  const label = confidencePercent
    ? `${edgeStyle.label} · ${confidencePercent}`
    : edgeStyle.label;

  const strokeColor = edgeStyle.color;
  const baseOpacity = 0.35;
  const glowOpacity = 0.7;

  return (
    <>
      {/* dim background edge */}
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

      {/* bright foreground edge with glow */}
      <BaseEdge
        path={edgePath}
        style={{
          ...style,
          stroke: strokeColor,
          strokeWidth: 2.5,
          strokeLinecap: 'round',
          opacity: glowOpacity,
          strokeDasharray: edgeStyle.dashArray,
          filter: `drop-shadow(0 0 3px ${strokeColor}44)`,
        }}
      />

      {/* edge label */}
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
