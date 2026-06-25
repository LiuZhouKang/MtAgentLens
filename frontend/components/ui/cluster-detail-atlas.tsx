'use client';

import type { ClusterModel } from '@/lib/cluster-model';
import { getSeverityPalette } from '@/lib/severity-palette';

type ClusterDetailAtlasProps = {
  model: ClusterModel;
};

function clampPercentage(value: number) {
  return Math.max(10, Math.min(100, value));
}

function computePressure(model: ClusterModel) {
  return clampPercentage(model.pressureScore);
}

export default function ClusterDetailAtlas({ model }: ClusterDetailAtlasProps) {
  const palette = getSeverityPalette(model.highestSeverity ?? 'Info');
  const pressure = computePressure(model);
  const seamRows = [
    {
      label: 'Boundary Stress',
      value: clampPercentage(14 + model.boundaryCrossingCount * 24),
      copy: `${model.boundaryCrossingCount} live crossings`,
      tone: palette.color,
    },
    {
      label: 'Delegation Breadth',
      value: clampPercentage(18 + model.delegationEdgeCount * 26),
      copy: `${model.delegationEdgeCount} delegation seams`,
      tone: '#8b7dff',
    },
    {
      label: 'Evidence Saturation',
      value: clampPercentage(12 + model.linkedEvidence.length * 18),
      copy: `${model.linkedEvidence.length} linked evidence`,
      tone: '#7dd3fc',
    },
  ];

  return (
    <div className="prism-atlas-grid">
      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Cluster Pressure</div>
        <div className="prism-atlas-value">把 fan-out、越界与证据聚束压成单一压力截面</div>
        <div className="prism-atlas-caption">
          当前选中的 cluster 不再被拆成松散统计，而是作为一个整体责任体被读取。
        </div>
        <div
          style={{
            position: 'relative',
            marginTop: 20,
            padding: 18,
            borderRadius: 22,
            border: `1px solid ${palette.border}`,
            background: `linear-gradient(180deg, ${palette.bg} 0%, rgba(255,255,255,0.02) 100%)`,
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              position: 'absolute',
              inset: 'auto 0 0 0',
              height: `${pressure}%`,
              background: `linear-gradient(180deg, ${palette.glow} 0%, ${palette.bg} 58%, rgba(255,255,255,0.01) 100%)`,
              boxShadow: `0 0 34px ${palette.glow}`,
            }}
          />
          <div
            style={{
              position: 'relative',
              display: 'grid',
              gap: 18,
              minHeight: 152,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
              <div className="prism-atlas-mini-copy">responsibility compression</div>
              <div
                style={{
                  fontSize: 42,
                  lineHeight: 0.9,
                  letterSpacing: '-0.05em',
                  color: palette.color,
                  textShadow: `0 0 24px ${palette.glow}`,
                }}
              >
                {pressure}
              </div>
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                gap: 8,
              }}
            >
              {[
                { label: 'Alerts', value: model.alertIds.length },
                { label: 'Evidence', value: model.linkedEvidence.length },
                { label: 'Signals', value: model.surfacedResources.length },
              ].map((item) => (
                <div
                  key={item.label}
                  style={{
                    padding: '12px 10px',
                    borderRadius: 16,
                    border: '1px solid var(--border-soft)',
                    background: 'color-mix(in srgb, var(--surface-soft) 82%, transparent)',
                  }}
                >
                  <div className="prism-atlas-mini-copy">{item.label}</div>
                  <div className="prism-atlas-mini-title" style={{ marginTop: 8, fontSize: 22 }}>
                    {item.value}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Delegation Mesh</div>
        <div className="prism-atlas-value">让一个 cluster 的扩散形态先被看见，再决定是否立刻进入调查</div>
        <div className="prism-atlas-caption">
          这里读的是 cluster 的组织张力，而不是一行行 agent 名单。
        </div>
        <div style={{ display: 'grid', gap: 12, marginTop: 18 }}>
          {seamRows.map((row) => (
            <div key={row.label} className="prism-atlas-row">
              <div style={{ display: 'grid', gap: 4 }}>
                <div className="prism-atlas-mini-title">{row.label}</div>
                <div className="prism-atlas-mini-copy">{row.copy}</div>
              </div>
              <div style={{ minWidth: 176 }}>
                <div className="prism-atlas-track">
                  <div
                    className="prism-atlas-fill"
                    style={{
                      width: `${row.value}%`,
                      background: `linear-gradient(90deg, ${row.tone}, color-mix(in srgb, ${row.tone} 34%, transparent))`,
                    }}
                  />
                </div>
              </div>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 18, display: 'grid', gap: 10 }}>
          {model.agentLanes.slice(0, 4).map((lane, index) => (
            <div
              key={lane.agentId}
              className="prism-atlas-row"
              style={{
                padding: '10px 12px',
                borderRadius: 16,
                border: '1px solid var(--border-soft)',
                background: 'color-mix(in srgb, var(--surface-soft) 84%, transparent)',
              }}
            >
              <div style={{ display: 'grid', gap: 4 }}>
                <div className="prism-atlas-mini-title">{lane.label}</div>
                <div className="prism-atlas-mini-copy">delegation lane {index + 1}</div>
              </div>
              <div className="prism-atlas-mini-copy">
                {lane.boundaryCrossingCount > 0
                  ? `${lane.boundaryCrossingCount} boundary seams`
                  : `${Math.max(1, lane.highlightedEdgeCount)} active seams`}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Evidence Threshold</div>
        <div className="prism-atlas-value">这组 cluster 是否值得立刻打开，取决于证据是否足够站住结论</div>
        <div className="prism-atlas-caption">
          每一格代表一个有效证据落点；亮度越高，说明该 cluster 越接近直接进入 verdict。
        </div>
        <div className="prism-atlas-cell-grid" style={{ marginTop: 18 }}>
          {Array.from({ length: 12 }).map((_, index) => {
            const active = index < Math.min(12, model.linkedEvidence.length);
            return (
              <span
                key={`${model.clusterId}-${index}`}
                className="prism-atlas-cell"
                style={{
                  background: active ? palette.color : 'var(--surface-soft)',
                  boxShadow: active ? `0 0 18px ${palette.glow}` : 'none',
                  opacity: active ? 0.92 : 0.38,
                }}
              />
            );
          })}
        </div>
        <div style={{ display: 'grid', gap: 10, marginTop: 16 }}>
          <div className="prism-atlas-row">
            <div className="prism-atlas-mini-title">Lead signal</div>
            <div className="prism-atlas-mini-copy">{model.highestSeverity ?? 'Info'} severity</div>
          </div>
          {model.surfacedResources[0] ? (
            <div className="prism-atlas-row">
              <div className="prism-atlas-mini-title">Materialized surface</div>
              <div className="prism-atlas-mini-copy">{model.surfacedResources[0].label}</div>
            </div>
          ) : null}
          <div className="prism-atlas-row">
            <div className="prism-atlas-mini-title">Reading cue</div>
            <div className="prism-atlas-mini-copy">
              先看边界撕裂，再看委托宽度，最后确认是否具备进入 investigation 的证据饱和度。
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
