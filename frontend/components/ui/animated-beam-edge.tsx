'use client';

import { BaseEdge, EdgeProps, getBezierPath } from '@xyflow/react';

export function AnimatedBeamEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  markerEnd,
}: EdgeProps) {
  const [edgePath] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  if (!edgePath) return null;

  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={{ ...style, stroke: 'rgba(255, 255, 255, 0.1)', strokeWidth: 1.5 }} />
      
      {/* The glowing beam using stroke-dasharray animation to prevent SVG animateMotion crash */}
      <style>
        {`
          @keyframes dash-draw-${id} {
            from {
              stroke-dashoffset: 220;
            }
            to {
              stroke-dashoffset: 0;
            }
          }
        `}
      </style>
      
      <BaseEdge
        path={edgePath}
        style={{
          ...style,
          stroke: 'url(#beam-gradient)',
          strokeWidth: 3,
          strokeLinecap: 'round',
          strokeDasharray: '40 200',
          animation: `dash-draw-${id} 2s linear infinite`,
        }}
        className="react-flow__edge-path"
      />
    </>
  );
}
