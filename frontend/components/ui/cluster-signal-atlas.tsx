'use client';

import { useMemo } from 'react';
import type { ClusterModel } from '@/lib/cluster-model';
import { getSeverityPalette } from '@/lib/severity-palette';

type ClusterSignalAtlasProps = {
  clusters: ClusterModel[];
};

function computePressure(model: ClusterModel) {
  const severityBoost =
    model.highestSeverity === 'Critical' ? 28 : model.highestSeverity === 'Warning' ? 16 : 8;
  return Math.min(
    100,
    model.alertIds.length * 16 +
      model.linkedEvidence.length * 11 +
      model.boundaryCrossingCount * 18 +
      severityBoost,
  );
}

export default function ClusterSignalAtlas({ clusters }: ClusterSignalAtlasProps) {
  const ranked = useMemo(
    () =>
      clusters.slice(0, 5).map((model) => ({
        model,
        pressure: computePressure(model),
        palette: getSeverityPalette(model.highestSeverity ?? 'Info'),
      })),
    [clusters],
  );

  const lead = ranked[0];

  return (
    <div className="prism-atlas-grid">
      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Signal Contour</div>
        <div className="prism-atlas-value">
          {lead
            ? `${lead.model.title} 正在牵引当前最高风险压力`
            : '等待 cluster 信号进入可判定区间'}
        </div>
        <div className="prism-atlas-caption">
          这不是传统 dashboard 柱图，而是把风险密度、证据拖曳和边界撕裂压成一条可读的责任曲面。
        </div>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(5, minmax(0, 1fr))',
            alignItems: 'end',
            gap: 10,
            minHeight: 168,
            marginTop: 18,
          }}
        >
          {ranked.map(({ model, pressure, palette }) => (
            <div key={model.clusterId} style={{ display: 'grid', gap: 10 }}>
              <div
                style={{
                  position: 'relative',
                  minHeight: 132,
                  borderRadius: 18,
                  border: `1px solid ${palette.border}`,
                  background:
                    'linear-gradient(180deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.01) 100%)',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    position: 'absolute',
                    inset: 'auto 0 0 0',
                    height: `${pressure}%`,
                    background: `linear-gradient(180deg, ${palette.glow} 0%, ${palette.bg} 54%, rgba(255,255,255,0.02) 100%)`,
                    boxShadow: `0 0 26px ${palette.glow}`,
                  }}
                />
                <div
                  style={{
                    position: 'absolute',
                    inset: 0,
                    background:
                      'linear-gradient(180deg, rgba(255,255,255,0.18) 0px, transparent 1px) top / 100% 22px repeat-y',
                    opacity: 0.12,
                  }}
                />
                <div
                  style={{
                    position: 'absolute',
                    top: 10,
                    left: 10,
                    right: 10,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 8,
                    fontSize: 10,
                    letterSpacing: '0.12em',
                    textTransform: 'uppercase',
                    color: 'var(--text-quaternary)',
                  }}
                >
                  <span>{model.highestSeverity ?? 'Info'}</span>
                  <span>{pressure}</span>
                </div>
              </div>
              <div style={{ display: 'grid', gap: 4 }}>
                <div className="prism-atlas-mini-title">{model.title}</div>
                <div className="prism-atlas-mini-copy">
                  {model.alertIds.length} alerts · {model.linkedEvidence.length} evidence
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Boundary Seams</div>
        <div className="prism-atlas-value">
          把 cluster 的扩散性读成边界缝合压力，而不是单点告警数量
        </div>
        <div className="prism-atlas-caption">
          一条 seam 代表一次真正需要被追责的跨越，不只是技术上发生过一条边。
        </div>
        <div style={{ display: 'grid', gap: 10, marginTop: 18 }}>
          {ranked.map(({ model, palette }) => (
            <div key={model.clusterId} className="prism-atlas-row">
              <div style={{ display: 'grid', gap: 4 }}>
                <div className="prism-atlas-mini-title">{model.title}</div>
                <div className="prism-atlas-mini-copy">
                  {model.relatedAgentIds.length} agents orchestrated
                </div>
              </div>
              <div style={{ minWidth: 164 }}>
                <div className="prism-atlas-track">
                  <div
                    className="prism-atlas-fill"
                    style={{
                      width: `${Math.min(100, 20 + model.boundaryCrossingCount * 22)}%`,
                      background: `linear-gradient(90deg, ${palette.color}, color-mix(in srgb, ${palette.color} 34%, transparent))`,
                    }}
                  />
                </div>
                <div className="prism-atlas-mini-copy" style={{ marginTop: 6, textAlign: 'right' }}>
                  {model.boundaryCrossingCount} boundary crossings
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Evidence Saturation</div>
        <div className="prism-atlas-value">
          证据不是附录，而是决定这条 cluster 是否值得进入的密度阈值
        </div>
        <div className="prism-atlas-caption">
          每一格代表一个有效证据落点。越饱和，说明该 cluster 越接近可以直接展开调查。
        </div>
        <div style={{ display: 'grid', gap: 14, marginTop: 18 }}>
          {ranked.map(({ model, palette }) => (
            <div key={model.clusterId} style={{ display: 'grid', gap: 8 }}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 10,
                }}
              >
                <div className="prism-atlas-mini-title">{model.title}</div>
                <div className="prism-atlas-mini-copy">
                  {model.linkedEvidence.length} materialized traces
                </div>
              </div>
              <div className="prism-atlas-cell-grid">
                {Array.from({ length: 12 }).map((_, index) => {
                  const active = index < Math.min(12, model.linkedEvidence.length);
                  return (
                    <span
                      key={`${model.clusterId}-${index}`}
                      className="prism-atlas-cell"
                      style={{
                        background: active ? palette.color : 'var(--surface-soft)',
                        boxShadow: active ? `0 0 18px ${palette.glow}` : 'none',
                        opacity: active ? 0.92 : 0.42,
                      }}
                    />
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
