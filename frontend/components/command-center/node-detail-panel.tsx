'use client';

import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import Link from 'next/link';
import { useAppStore } from '@/lib/store';
import { buildAgentHref } from '@/lib/agent-route';
import type { ResponsibilityNode } from '@/lib/types';

type Props = {
  nodeId: string;
  nodeType: string;
  agentId: string;
  desc: string;
  rawNodes: ResponsibilityNode[];
  onClose: () => void;
};

const TYPE_LABEL: Record<string, string> = {
  Agent: 'Agent 主体',
  Resource: '资源',
  PolicyBoundary: '策略边界',
};

const TYPE_COLOR: Record<string, string> = {
  Agent: '#5e6ad2',
  Resource: '#6b7280',
  PolicyBoundary: '#f97316',
};

function resourceSummary(n: ResponsibilityNode): string | null {
  if (!n.resource) return null;
  if ('File' in n.resource) return n.resource.File.canonical_path;
  if ('Network' in n.resource) return `${n.resource.Network.host}:${n.resource.Network.port}`;
  if ('Process' in n.resource) return `PID ${n.resource.Process.process_id.pid}`;
  if ('ToolCall' in n.resource) return n.resource.ToolCall.tool_name;
  return null;
}

export default function NodeDetailPanel({
  nodeId,
  nodeType,
  agentId,
  desc,
  rawNodes,
  onClose,
}: Props) {
  const graph = useAppStore((s) => s.graph);
  const alerts = useAppStore((s) => s.alerts);
  const panelRef = useRef<HTMLDivElement>(null);

  // Entrance animation
  useGSAP(
    () => {
      if (!panelRef.current) return;
      gsap.fromTo(
        panelRef.current,
        { opacity: 0, x: 20, scale: 0.97 },
        { opacity: 1, x: 0, scale: 1, duration: 0.28, ease: 'power3.out' },
      );
    },
    { scope: panelRef },
  );

  // Find connected edges from raw graph
  const connectedEdges = graph.edges.filter((e) =>
    rawNodes.some((n) => n.node_id === e.source || n.node_id === e.target),
  );
  const connectedNodeIds = new Set<string>();
  connectedEdges.forEach((e) => {
    if (!rawNodes.some((n) => n.node_id === e.source)) connectedNodeIds.add(e.source);
    if (!rawNodes.some((n) => n.node_id === e.target)) connectedNodeIds.add(e.target);
  });
  const connectedNodes = graph.nodes.filter((n) => connectedNodeIds.has(n.node_id));

  // Related alerts
  const relatedAlerts = alerts.filter(
    (a) =>
      a.primary_agent.agent_id === agentId ||
      a.involved_agents.some((ia) => ia.agent_id === agentId),
  );

  const isAgentNode = nodeType === 'Agent';
  const color = TYPE_COLOR[nodeType] ?? '#6b7280';

  return (
    <div
      ref={panelRef}
      style={{
        position: 'absolute',
        top: 12,
        right: 12,
        bottom: 12,
        width: 340,
        maxWidth: 'calc(100vw - 80px)',
        background: 'rgba(13,14,15,0.96)',
        backdropFilter: 'blur(20px) saturate(1.2)',
        border: '1px solid rgba(255,255,255,0.07)',
        borderRadius: 14,
        display: 'flex',
        flexDirection: 'column',
        zIndex: 20,
        color: '#fafafa',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '18px 18px 14px',
          borderBottom: '1px solid rgba(255,255,255,0.06)',
          flexShrink: 0,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 10,
          }}
        >
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '2px 8px',
              borderRadius: 4,
              background: `${color}18`,
              border: `1px solid ${color}30`,
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              color,
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: color,
                display: 'inline-block',
              }}
            />
            {TYPE_LABEL[nodeType] ?? nodeType}
          </span>
          <button
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              color: 'rgba(255,255,255,0.25)',
              fontSize: 18,
              lineHeight: 1,
              padding: '0 0 0 8px',
            }}
            aria-label="关闭"
          >
            ×
          </button>
        </div>

        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 3, wordBreak: 'break-all' }}>
          {isAgentNode ? agentId : nodeId.length > 32 ? `${nodeId.slice(0, 30)}…` : nodeId}
        </div>
        <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)', lineHeight: 1.5 }}>{desc}</div>
      </div>

      {/* Body — scrollable */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '14px 18px',
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
        }}
      >
        {/* Connected nodes */}
        {connectedNodes.length > 0 && (
          <section>
            <h3
              style={{
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: '0.1em',
                textTransform: 'uppercase',
                color: 'rgba(255,255,255,0.3)',
                marginBottom: 8,
              }}
            >
              关联节点 ({connectedNodes.length})
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {connectedNodes.slice(0, 8).map((cn) => (
                <div
                  key={cn.node_id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '6px 10px',
                    borderRadius: 6,
                    background: 'rgba(255,255,255,0.02)',
                    fontSize: 11,
                  }}
                >
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: '50%',
                      flexShrink: 0,
                      background: TYPE_COLOR[cn.node_type] ?? '#6b7280',
                    }}
                  />
                  <span
                    style={{
                      color: 'rgba(255,255,255,0.6)',
                      flex: 1,
                      minWidth: 0,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {cn.agent_id && cn.agent_id !== 'unassigned'
                      ? cn.agent_id.slice(0, 24)
                      : cn.node_type}
                  </span>
                  <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.25)', flexShrink: 0 }}>
                    {cn.node_type}
                  </span>
                </div>
              ))}
              {connectedNodes.length > 8 && (
                <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)', textAlign: 'center' }}>
                  +{connectedNodes.length - 8} 个更多
                </div>
              )}
            </div>
          </section>
        )}

        {/* Connected edges */}
        {connectedEdges.length > 0 && (
          <section>
            <h3
              style={{
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: '0.1em',
                textTransform: 'uppercase',
                color: 'rgba(255,255,255,0.3)',
                marginBottom: 8,
              }}
            >
              关联边 ({connectedEdges.length})
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {connectedEdges.slice(0, 6).map((ce) => (
                <div
                  key={ce.edge_id}
                  style={{
                    padding: '6px 10px',
                    borderRadius: 6,
                    background: 'rgba(255,255,255,0.02)',
                    fontSize: 10,
                  }}
                >
                  <div style={{ color: 'rgba(255,255,255,0.5)', marginBottom: 2 }}>
                    {ce.edge_type}
                  </div>
                  <div style={{ color: 'rgba(255,255,255,0.3)', fontFamily: 'monospace' }}>
                    {(ce.confidence * 100).toFixed(0)}% 置信度
                    {ce.evidence_refs.length > 0 ? ` · ${ce.evidence_refs.length} 证据` : ''}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Resource details */}
        {rawNodes.length > 0 && (
          <section>
            <h3
              style={{
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: '0.1em',
                textTransform: 'uppercase',
                color: 'rgba(255,255,255,0.3)',
                marginBottom: 8,
              }}
            >
              资源详情 ({rawNodes.length})
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {rawNodes.slice(0, 10).map((rn) => {
                const summary = resourceSummary(rn);
                return (
                  <div
                    key={rn.node_id}
                    style={{
                      padding: '8px 10px',
                      borderRadius: 6,
                      background: 'rgba(255,255,255,0.02)',
                      fontSize: 10,
                    }}
                  >
                    <div
                      style={{
                        color: 'rgba(255,255,255,0.35)',
                        fontFamily: 'monospace',
                        marginBottom: 2,
                      }}
                    >
                      {rn.node_id.slice(0, 14)}…
                    </div>
                    {summary && (
                      <div
                        style={{
                          color: 'rgba(255,255,255,0.65)',
                          fontFamily: 'monospace',
                          fontSize: 10,
                          wordBreak: 'break-all',
                          lineHeight: 1.5,
                        }}
                      >
                        {summary}
                      </div>
                    )}
                    {rn.evidence_refs.length > 0 && (
                      <div style={{ color: 'rgba(255,255,255,0.3)', marginTop: 3 }}>
                        {rn.evidence_refs.length} 条证据
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {/* Related alerts */}
        {relatedAlerts.length > 0 && (
          <section>
            <h3
              style={{
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: '0.1em',
                textTransform: 'uppercase',
                color: 'rgba(255,255,255,0.3)',
                marginBottom: 8,
              }}
            >
              相关告警 ({relatedAlerts.length})
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {relatedAlerts.slice(0, 3).map((a) => (
                <div
                  key={a.alert_id}
                  style={{
                    padding: '8px 10px',
                    borderRadius: 6,
                    background: 'rgba(255,255,255,0.02)',
                    fontSize: 10,
                  }}
                >
                  {(() => {
                    const isCrit = a.severity === 'Critical';
                    const isWarn = a.severity === 'Warning';
                    const bg = isCrit ? '#f9731620' : isWarn ? '#f59e0b20' : '#5e6ad220';
                    const fg = isCrit ? '#f97316' : isWarn ? '#f59e0b' : '#5e6ad2';
                    return (
                      <span
                        style={{
                          display: 'inline-block',
                          padding: '1px 5px',
                          borderRadius: 3,
                          fontSize: 9,
                          fontWeight: 600,
                          marginBottom: 4,
                          background: bg,
                          color: fg,
                        }}
                      >
                        {a.severity}
                      </span>
                    );
                  })()}
                  <div
                    style={{ color: 'rgba(255,255,255,0.65)', lineHeight: 1.4, marginBottom: 2 }}
                  >
                    {a.title}
                  </div>
                  <div style={{ color: 'rgba(255,255,255,0.3)' }}>
                    {a.decision} · {(a.confidence * 100).toFixed(0)}% 置信度
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>

      {/* Footer actions */}
      <div
        style={{
          padding: '12px 18px',
          borderTop: '1px solid rgba(255,255,255,0.06)',
          flexShrink: 0,
          display: 'flex',
          gap: 8,
        }}
      >
        {isAgentNode && agentId !== 'unassigned' && (
          <Link
            href={buildAgentHref(agentId)}
            style={{
              flex: 1,
              textAlign: 'center',
              padding: '8px 0',
              borderRadius: 8,
              border: '1px solid rgba(255,255,255,0.08)',
              background: 'rgba(255,255,255,0.03)',
              color: 'rgba(255,255,255,0.7)',
              fontSize: 12,
              fontWeight: 500,
              textDecoration: 'none',
              transition: 'background 120ms',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'rgba(255,255,255,0.06)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'rgba(255,255,255,0.03)';
            }}
          >
            打开 Agent 档案
          </Link>
        )}
        <Link
          href={`/investigations?alert=${relatedAlerts[0]?.alert_id ?? ''}`}
          style={{
            flex: 1,
            textAlign: 'center',
            padding: '8px 0',
            borderRadius: 8,
            border: `1px solid ${color}30`,
            background: `${color}10`,
            color,
            fontSize: 12,
            fontWeight: 500,
            textDecoration: 'none',
            transition: 'background 120ms',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = `${color}20`;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = `${color}10`;
          }}
        >
          调查告警
        </Link>
        <button
          onClick={onClose}
          style={{
            padding: '8px 14px',
            borderRadius: 8,
            border: '1px solid rgba(255,255,255,0.06)',
            background: 'transparent',
            color: 'rgba(255,255,255,0.35)',
            fontSize: 12,
            cursor: 'pointer',
          }}
        >
          关闭
        </button>
      </div>
    </div>
  );
}
