// @vitest-environment jsdom
import React from 'react';
import { act, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TraceNetwork } from '@/components/command-center/trace-network';

const mockState = vi.hoisted(() => ({
  latestProps: null as Record<string, unknown> | null,
}));

vi.mock('@xyflow/react', () => {
  function useNodesState(initialNodes: Array<Record<string, unknown>>) {
    const [nodes, setNodes] = React.useState(initialNodes);

    const onNodesChange = (changes: Array<{ id: string; position?: { x: number; y: number } }>) => {
      setNodes((currentNodes) =>
        currentNodes.map((node) => {
          const change = changes.find((item) => item.id === node.id);
          if (!change) return node;

          return {
            ...node,
            position: change.position ?? node.position,
          };
        }),
      );
    };

    return [nodes, setNodes, onNodesChange] as const;
  }

  function useEdgesState(initialEdges: Array<Record<string, unknown>>) {
    const [edges, setEdges] = React.useState(initialEdges);
    const onEdgesChange = () => undefined;
    return [edges, setEdges, onEdgesChange] as const;
  }

  return {
    __esModule: true,
    Background: () => <div data-testid="background" />,
    BackgroundVariant: { Dots: 'dots' },
    Controls: () => <div data-testid="controls" />,
    MiniMap: () => <div data-testid="minimap" />,
    Position: { Left: 'left', Right: 'right' },
    ReactFlow: (props: Record<string, unknown>) => {
      mockState.latestProps = props;
      return <div data-testid="react-flow">{props.children as React.ReactNode}</div>;
    },
    useEdgesState,
    useNodesState,
  };
});

describe('TraceNetwork', () => {
  it('keeps dragged node positions when the same graph rerenders', () => {
    const initialNodes = [
      { id: 'agent-frontend', label: 'agent-frontend', meta: 'Agent' },
      { id: 'agent-security', label: 'agent-security', meta: 'Agent' },
      { id: 'boundary-system', label: 'boundary-system', meta: 'PolicyBoundary' },
    ];
    const initialEdges = [
      { source: 'agent-frontend', target: 'agent-security', label: 'delegates' },
      { source: 'agent-security', target: 'boundary-system', label: 'crosses' },
    ];

    const { rerender } = render(<TraceNetwork nodes={initialNodes} edges={initialEdges} height={320} />);

    const firstProps = mockState.latestProps as Record<string, unknown>;
    const onNodesChange = firstProps.onNodesChange as (changes: Array<{ id: string; position?: { x: number; y: number } }>) => void;
    expect(firstProps.panOnDrag).toBe(true);
    expect(firstProps.zoomOnScroll).toBe(true);
    expect(firstProps.zoomOnPinch).toBe(true);
    expect(firstProps.autoPanOnNodeDrag).toBe(true);
    expect(firstProps.nodesDraggable).toBe(true);
    expect(mockState.latestProps?.children).toBeDefined();

    expect(document.querySelector('[data-testid="background"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="controls"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="minimap"]')).not.toBeNull();

    act(() => {
      onNodesChange([
        {
          id: 'agent-security',
          position: { x: 640, y: 180 },
        },
      ]);
    });

    const movedProps = mockState.latestProps as Record<string, unknown>;
    const movedNodes = movedProps.nodes as Array<Record<string, unknown>>;
    expect(movedNodes.find((node) => node.id === 'agent-security')?.position).toEqual({ x: 640, y: 180 });

    rerender(<TraceNetwork nodes={initialNodes} edges={initialEdges} height={320} />);

    const rerenderedProps = mockState.latestProps as Record<string, unknown>;
    const rerenderedNodes = rerenderedProps.nodes as Array<Record<string, unknown>>;
    expect(rerenderedNodes.find((node) => node.id === 'agent-security')?.position).toEqual({ x: 640, y: 180 });
  });
});