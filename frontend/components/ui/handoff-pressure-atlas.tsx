'use client';

import { useMemo } from 'react';
import type {
  HandoffObsMetrics,
  HandoffObservabilityEvent,
} from '@/lib/handoff-observability-contract';

type HandoffPressureAtlasProps = {
  metrics: HandoffObsMetrics;
  events: HandoffObservabilityEvent[];
};

function clampPercentage(value: number) {
  return Math.max(8, Math.min(100, value));
}

export default function HandoffPressureAtlas({ metrics, events }: HandoffPressureAtlasProps) {
  const boundarySignals = useMemo(
    () =>
      events.filter((event) => {
        const subType = String((event as { sub_type?: string }).sub_type ?? '').toUpperCase();
        return subType === 'BOUNDARY_TAG' || subType === 'DATA_LEAK' || subType.includes('DRIFT');
      }),
    [events],
  );

  const orchestrationSignals = useMemo(
    () =>
      events.filter((event) => {
        const subType = String((event as { sub_type?: string }).sub_type ?? '').toUpperCase();
        return subType === 'HANDOFF_SPAWN' || subType === 'FANOUT_STORM' || subType === 'CONTEXT_SATURATION';
      }),
    [events],
  );

  const seamRows = [
    {
      label: 'Fanout Pressure',
      value: clampPercentage(metrics.fan_out_ratio * 26),
      copy: `${metrics.fan_out_ratio.toFixed(2)} : 1 delegation spread`,
      tone: '#8b7dff',
    },
    {
      label: 'Boundary Survival',
      value: clampPercentage(metrics.workspace_tag_survival_rate * 100),
      copy: `${(metrics.workspace_tag_survival_rate * 100).toFixed(1)}% tag fidelity`,
      tone: '#7dd3fc',
    },
    {
      label: 'Artifact Drift',
      value: clampPercentage(16 + metrics.orphaned_artifacts * 22),
      copy: `${metrics.orphaned_artifacts} orphaned outputs`,
      tone: '#fbbf24',
    },
    {
      label: 'Active Load',
      value: clampPercentage(14 + metrics.active_count * 14),
      copy: `${metrics.active_count} active children`,
      tone: '#34d399',
    },
  ];

  const cadencePeaks = useMemo(
    () =>
      events.slice(0, 8).map((event, index) => {
        const severity = String((event as { severity?: string }).severity ?? 'INFO').toUpperCase();
        const tone =
          severity === 'CRITICAL' || severity === 'ERROR'
            ? '#fb7185'
            : severity === 'WARNING'
              ? '#fbbf24'
              : index % 2 === 0
                ? '#8b7dff'
                : '#7dd3fc';
        return {
          key: `${index}-${(event as { timestamp_ms?: number }).timestamp_ms ?? 0}`,
          height: 34 + ((index * 13) % 44),
          tone,
        };
      }),
    [events],
  );

  return (
    <div className="prism-atlas-grid" style={{ marginTop: 18 }}>
      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Delegation Pressure</div>
        <div className="prism-atlas-value">把委托深度、扇出速度与边界衰减压成同一张压力曲面</div>
        <div className="prism-atlas-caption">
          handoff 不该只是树结构浏览器，它应该先告诉你哪一种扩散最接近失控。
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
                      background: `linear-gradient(90deg, ${row.tone}, color-mix(in srgb, ${row.tone} 28%, transparent))`,
                    }}
                  />
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Cadence Strip</div>
        <div className="prism-atlas-value">把 handoff 事件流读成一条失稳节奏，而不是平铺日志</div>
        <div className="prism-atlas-caption">
          峰值越密，说明委托生成、上下文膨胀与边界异常正在更短窗口内叠加。
        </div>
        <div
          style={{
            position: 'relative',
            height: 132,
            marginTop: 18,
            borderRadius: 20,
            border: '1px solid var(--border-soft)',
            background: 'linear-gradient(180deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.01) 100%)',
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
          {cadencePeaks.map((peak, index) => (
            <div
              key={peak.key}
              style={{
                position: 'absolute',
                left: `calc(${10 + index * 11}% - 6px)`,
                bottom: 18,
                width: 12,
                height: peak.height,
                borderRadius: 999,
                background: peak.tone,
                boxShadow: `0 0 22px ${peak.tone}55`,
              }}
            />
          ))}
        </div>
      </section>

      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Field Readout</div>
        <div className="prism-atlas-value">让边界残骸、实时委托和失真信号拥有各自清晰的进入理由</div>
        <div className="prism-atlas-caption">
          当前观测面最值得优先处理的是 {boundarySignals.length} 条边界信号与 {orchestrationSignals.length} 条扩散信号。
        </div>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
            gap: 8,
            marginTop: 18,
          }}
        >
          {[
            { label: 'Boundary', value: boundarySignals.length, tone: '#7dd3fc' },
            { label: 'Orchestration', value: orchestrationSignals.length, tone: '#8b7dff' },
            { label: 'Debris', value: metrics.orphaned_artifacts, tone: '#fbbf24' },
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
              <div
                style={{
                  marginTop: 6,
                  fontSize: 24,
                  lineHeight: 1,
                  fontWeight: 650,
                  color: item.tone,
                  textShadow: `0 0 18px ${item.tone}28`,
                }}
              >
                {item.value}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
