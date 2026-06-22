'use client';

import type { AgentModel } from '@/lib/agent-model';
import { getSeverityPalette } from '@/lib/severity-palette';

type AgentResponsibilityAtlasProps = {
  model: AgentModel;
};

function clampPercentage(value: number) {
  return Math.max(10, Math.min(100, value));
}

function computeAuthority(model: AgentModel) {
  const severityBoost =
    model.highestSeverity === 'Critical' ? 26 : model.highestSeverity === 'Warning' ? 14 : 8;
  return clampPercentage(
    model.claimedRoles.length * 20 +
      model.alertIds.length * 16 +
      model.touchedResources.length * 10 +
      model.linkedEvidence.length * 11 +
      severityBoost,
  );
}

export default function AgentResponsibilityAtlas({ model }: AgentResponsibilityAtlasProps) {
  const palette = getSeverityPalette(model.highestSeverity ?? 'Info');
  const authorityScore = computeAuthority(model);
  const surfaceRows = [
    {
      label: 'Role Density',
      value: clampPercentage(14 + model.claimedRoles.length * 24),
      copy: `${model.claimedRoles.length || 1} claimed roles`,
      tone: '#8b7dff',
    },
    {
      label: 'Resource Exposure',
      value: clampPercentage(12 + model.touchedResources.length * 16),
      copy: `${model.touchedResources.length} touched resources`,
      tone: '#7dd3fc',
    },
    {
      label: 'Evidence Hold',
      value: clampPercentage(10 + model.linkedEvidence.length * 18),
      copy: `${model.linkedEvidence.length} linked evidence`,
      tone: palette.color,
    },
  ];

  return (
    <div className="prism-atlas-grid">
      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Authority Signature</div>
        <div className="prism-atlas-value">把身份声明、实际暴露面与风险强度压成一个可读签名</div>
        <div className="prism-atlas-caption">
          单个 agent 的问题不是“它做了多少事”，而是“它凭什么能做这些事，以及证据是否足够”。
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
              height: `${authorityScore}%`,
              background: `linear-gradient(180deg, ${palette.glow} 0%, ${palette.bg} 60%, rgba(255,255,255,0.01) 100%)`,
              boxShadow: `0 0 32px ${palette.glow}`,
            }}
          />
          <div style={{ position: 'relative', display: 'grid', gap: 14, minHeight: 154 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
              <div className="prism-atlas-mini-copy">responsibility signature</div>
              <div
                style={{
                  fontSize: 42,
                  lineHeight: 0.9,
                  letterSpacing: '-0.05em',
                  color: palette.color,
                  textShadow: `0 0 22px ${palette.glow}`,
                }}
              >
                {authorityScore}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {(model.claimedRoles.length ? model.claimedRoles : ['Pending role claim']).slice(0, 3).map((role) => (
                <span
                  key={role}
                  style={{
                    padding: '8px 10px',
                    borderRadius: 999,
                    border: '1px solid var(--border-soft)',
                    background: 'color-mix(in srgb, var(--surface-soft) 84%, transparent)',
                    fontSize: 10,
                    letterSpacing: '0.08em',
                    textTransform: 'uppercase',
                    color: 'var(--text-secondary)',
                  }}
                >
                  {role}
                </span>
              ))}
            </div>
            <div className="prism-atlas-mini-copy">
              当前这个 agent 最值得优先确认的是身份声明是否与真正执行路径一致。
            </div>
          </div>
        </div>
      </section>

      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Exposure Surface</div>
        <div className="prism-atlas-value">把资源接触、横向关联与委托余波压成暴露面剖面</div>
        <div className="prism-atlas-caption">
          当资源接触范围开始扩大，这个 agent 就不再只是一个节点，而是一个需要被单独判断的责任面。
        </div>
        <div style={{ display: 'grid', gap: 12, marginTop: 18 }}>
          {surfaceRows.map((row) => (
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
                      background: `linear-gradient(90deg, ${row.tone}, color-mix(in srgb, ${row.tone} 32%, transparent))`,
                    }}
                  />
                </div>
              </div>
            </div>
          ))}
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
            { label: 'Peers', value: model.relatedAgentIds.length, tone: '#8b7dff' },
            { label: 'Resources', value: model.touchedResources.length, tone: '#7dd3fc' },
            { label: 'Alerts', value: model.alertIds.length, tone: palette.color },
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
                  textShadow: `0 0 18px ${item.tone}24`,
                }}
              >
                {item.value}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="prism-atlas-panel">
        <div className="prism-atlas-label">Evidence Hold</div>
        <div className="prism-atlas-value">把这个 agent 能否被定责，读成证据抓握力而不是补充说明</div>
        <div className="prism-atlas-caption">
          每一格代表一个已物化的 trace；亮起越多，说明这条 agent 画像越接近可以直接进入 investigation。
        </div>
        <div className="prism-atlas-cell-grid" style={{ marginTop: 18 }}>
          {Array.from({ length: 12 }).map((_, index) => {
            const active = index < Math.min(12, model.linkedEvidence.length);
            return (
              <span
                key={`${model.agentId}-${index}`}
                className="prism-atlas-cell"
                style={{
                  background: active ? palette.color : 'var(--surface-soft)',
                  boxShadow: active ? `0 0 16px ${palette.glow}` : 'none',
                  opacity: active ? 0.92 : 0.38,
                }}
              />
            );
          })}
        </div>
        <div style={{ display: 'grid', gap: 10, marginTop: 16 }}>
          {model.touchedResources.slice(0, 3).map((resource) => (
            <div key={resource} className="prism-atlas-row">
              <div className="prism-atlas-mini-title">{resource}</div>
              <div className="prism-atlas-mini-copy">materialized touchpoint</div>
            </div>
          ))}
          {!model.touchedResources.length ? (
            <div className="prism-atlas-mini-copy">等待真实资源接触轨迹接入。</div>
          ) : null}
        </div>
      </section>
    </div>
  );
}
