'use client';

import { useMemo } from 'react';
import type { InvestigationModel } from '@/lib/investigation';

type ForensicTopologyAtlasProps = {
  model: InvestigationModel;
  tone: string;
  leadEvidenceMaterial: string;
};

function clampPercentage(value: number) {
  return Math.max(10, Math.min(100, value));
}

export default function ForensicTopologyAtlas({
  model,
  tone,
  leadEvidenceMaterial,
}: ForensicTopologyAtlasProps) {
  const timelinePeaks = useMemo(() => {
    if (model.timelineSteps.length <= 1) {
      return [{ key: 'single', left: 50, tone, label: 'Single Scene' }];
    }

    const start = model.timelineSteps[0]?.timestamp_ns ?? 0;
    const end = model.timelineSteps[model.timelineSteps.length - 1]?.timestamp_ns ?? start;
    const span = Math.max(1, end - start);

    return model.timelineSteps.slice(0, 6).map((step, index) => {
      const left = ((step.timestamp_ns - start) / span) * 100;
      const color =
        step.operation === 'Delegation'
          ? '#a78bfa'
          : step.operation === 'FileRead'
            ? '#7dd3fc'
            : index === 0
              ? tone
              : '#fbbf24';
      return {
        key: `${step.timestamp_ns}-${index}`,
        left: clampPercentage(left),
        tone: color,
        label: step.event_type,
      };
    });
  }, [model.timelineSteps, tone]);

  const confidenceBands = useMemo(
    () =>
      model.pathEdges.slice(0, 5).map((edge, index) => ({
        key: edge.edge_id,
        label: `${edge.source} -> ${edge.target}`,
        width: clampPercentage(edge.confidence * 100),
        tone:
          edge.edge_type === 'PolicyBoundaryCrossing'
            ? '#fbbf24'
            : edge.edge_type === 'AgentDelegatesTo'
              ? '#a78bfa'
              : index === 0
                ? tone
                : '#7dd3fc',
      })),
    [model.pathEdges, tone],
  );

  return (
    <div className="prism-atlas-grid" style={{ marginTop: 18 }}>
      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Causality Spine</div>
        <div className="prism-atlas-value">每个节点都应当是责任转移中的一个决定性折点</div>
        <div className="prism-atlas-caption">
          这条 spine 用于把调查页的叙事从“看很多日志”改成“看一条真正成立的因果骨架”。
        </div>
        <div style={{ position: 'relative', marginTop: 20, paddingTop: 24 }}>
          <div
            style={{
              position: 'absolute',
              left: 0,
              right: 0,
              top: 36,
              height: 1,
              background:
                'linear-gradient(90deg, transparent 0%, color-mix(in srgb, var(--brand-primary) 30%, transparent) 18%, color-mix(in srgb, var(--brand-primary) 30%, transparent) 82%, transparent 100%)',
            }}
          />
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: `repeat(${Math.max(1, Math.min(5, model.pathNodes.length))}, minmax(0, 1fr))`,
              gap: 10,
            }}
          >
            {model.pathNodes.slice(0, 5).map((node, index) => (
              <div key={node.nodeId} style={{ display: 'grid', justifyItems: 'center', gap: 10 }}>
                <div
                  style={{
                    width: 16,
                    height: 16,
                    borderRadius: '50%',
                    border: `2px solid ${index === 0 ? tone : 'var(--brand-primary)'}`,
                    background: 'var(--bg-elevated)',
                    boxShadow: `0 0 20px ${index === 0 ? `${tone}40` : 'rgba(108, 194, 255, 0.18)'}`,
                    zIndex: 1,
                  }}
                />
                <div style={{ display: 'grid', gap: 4, textAlign: 'center' }}>
                  <div className="prism-atlas-mini-title">{node.label}</div>
                  <div className="prism-atlas-mini-copy">{node.nodeType}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Timeline Cadence</div>
        <div className="prism-atlas-value">把案发节奏压成一条连续波形，而不是离散时间点的堆叠</div>
        <div className="prism-atlas-caption">
          峰值越密，说明 delegation、执行与证据物化在更短窗口内发生，更值得优先调查。
        </div>
        <div
          style={{
            position: 'relative',
            height: 124,
            marginTop: 18,
            borderRadius: 20,
            border: '1px solid var(--border-soft)',
            background:
              'linear-gradient(180deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.01) 100%)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              position: 'absolute',
              inset: 0,
              background:
                'linear-gradient(180deg, rgba(255,255,255,0.08) 0px, transparent 1px) top / 100% 24px repeat-y',
              opacity: 0.16,
            }}
          />
          {timelinePeaks.map((peak, index) => (
            <div
              key={peak.key}
              title={peak.label}
              style={{
                position: 'absolute',
                left: `calc(${peak.left}% - 7px)`,
                bottom: 18,
                width: 14,
                height: `${42 + (index % 3) * 18}px`,
                borderRadius: 999,
                background: peak.tone,
                boxShadow: `0 0 24px ${peak.tone}55`,
              }}
            />
          ))}
        </div>
      </section>

      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Evidence Voltage</div>
        <div className="prism-atlas-value">
          证据强度决定这场调查是否足够站得住，而不是视觉上看起来有多复杂
        </div>
        <div className="prism-atlas-caption">首个物化对象是 {leadEvidenceMaterial}。</div>
        <div style={{ display: 'grid', gap: 10, marginTop: 18 }}>
          {confidenceBands.map((band) => (
            <div key={band.key} className="prism-atlas-row">
              <div style={{ display: 'grid', gap: 4 }}>
                <div className="prism-atlas-mini-title">{band.label}</div>
                <div className="prism-atlas-mini-copy">confidence ribbon</div>
              </div>
              <div style={{ minWidth: 164 }}>
                <div className="prism-atlas-track">
                  <div
                    className="prism-atlas-fill"
                    style={{
                      width: `${band.width}%`,
                      background: `linear-gradient(90deg, ${band.tone}, color-mix(in srgb, ${band.tone} 34%, transparent))`,
                    }}
                  />
                </div>
                <div className="prism-atlas-mini-copy" style={{ marginTop: 6, textAlign: 'right' }}>
                  {band.width.toFixed(0)}% confidence
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
