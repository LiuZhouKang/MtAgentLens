'use client';

import { useState } from 'react';
import { useAppStore } from '@/lib/store';
import type {
  PerspectiveKey,
  MonitoringStatus,
  PerspectiveHealth,
} from '@/lib/monitoring/monitoring-types';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { StatusBadge } from '@/components/ui/linear/status-badge';
import { PERSPECTIVE_META, PERSPECTIVE_ORDER, PHASE2_PILLS } from './perspective-config';
import { MonitoringCard } from './monitoring-card';
import { PerspectiveDetailShell } from './perspective-detail-shell';

const STATUS_PRIORITY: Record<MonitoringStatus, number> = { normal: 0, warning: 1, critical: 2 };

/** 6 个可用（available=true）视角中最差状态——健康聚合不含缺表视角。 */
export function aggregateHealth(
  health: Record<PerspectiveKey, PerspectiveHealth>,
): MonitoringStatus {
  let worst: MonitoringStatus = 'normal';
  for (const key of PERSPECTIVE_ORDER) {
    const h = health[key];
    if (h && h.available && STATUS_PRIORITY[h.status] > STATUS_PRIORITY[worst]) worst = h.status;
  }
  return worst;
}

const STATUS_LABEL: Record<MonitoringStatus, string> = {
  critical: '严重',
  warning: '关注',
  normal: '正常',
};

export default function MonitoringSection() {
  const monitoring = useAppStore((s) => s.monitoring);
  const [selected, setSelected] = useState<PerspectiveKey | null>(null);
  const [focusHint, setFocusHint] = useState<string | null>(null);

  if (!monitoring) return null;

  const overall = aggregateHealth(monitoring.health);
  const timeStr =
    monitoring.derivedAtMs > 0
      ? new Date(monitoring.derivedAtMs).toLocaleTimeString('zh-CN', {
          hour: '2-digit',
          minute: '2-digit',
        })
      : '';

  /** 交叉导航：目标 agent 有告警才写 #agentId=，否则降级提示（少死胡同）。 */
  const focusAgent = (agentId: string) => {
    const owned = useAppStore.getState().alerts.some((a) => a.primary_agent.agent_id === agentId);
    if (!owned) {
      setFocusHint(`${agentId.slice(0, 8)}… 无告警，已跳过聚焦`);
      return;
    }
    setFocusHint(null);
    window.location.hash = `agentId=${agentId}`;
  };

  return (
    <SectionPanel
      eyebrow="Monitoring"
      title="运行监控"
      meta={`${monitoring.sourceLabel}${timeStr ? ` · ${timeStr}` : ''}`}
      bodyClassName="ln-panel-body"
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <StatusBadge
          tone={overall === 'critical' ? 'danger' : overall === 'warning' ? 'warning' : 'success'}
        >
          总览 {STATUS_LABEL[overall]}
        </StatusBadge>
        {focusHint ? (
          <span className="ln-metric__label" style={{ fontSize: 11 }}>
            {focusHint}
          </span>
        ) : null}
      </div>

      <div
        style={{
          maxHeight: 520,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        {/* 健康总览条：6 个可用派生视角（缺表灰态不并入聚合） */}
        <div
          role="group"
          aria-label="监控健康总览"
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
            gap: 8,
          }}
        >
          {PERSPECTIVE_ORDER.map((key) => {
            const h = monitoring.health[key];
            return (
              <button
                key={key}
                type="button"
                onClick={() => setSelected(key)}
                aria-pressed={selected === key}
                className="ln-panel"
                style={{
                  textAlign: 'left',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                  padding: 12,
                  cursor: 'pointer',
                }}
              >
                <StatusBadge
                  tone={
                    h.available
                      ? h.status === 'critical'
                        ? 'danger'
                        : h.status === 'warning'
                          ? 'warning'
                          : 'success'
                      : 'muted'
                  }
                >
                  {h.available ? STATUS_LABEL[h.status] : '无此表'}
                </StatusBadge>
                <span style={{ fontWeight: 600, fontSize: 12 }}>{PERSPECTIVE_META[key].label}</span>
                <span className="ln-metric__label" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {h.score != null ? h.score : '—'}
                </span>
              </button>
            );
          })}
        </div>

        {/* 实时视角行（阶段二 SSE 占位，不与派生视角混入聚合） */}
        <div
          role="group"
          aria-label="实时视角（阶段二）"
          style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}
        >
          <span className="ln-eyebrow">实时视角 · 阶段二 SSE</span>
          {PHASE2_PILLS.map((p) => (
            <span
              key={p}
              aria-disabled="true"
              title="阶段二经 SSE 接入"
              style={{
                opacity: 0.45,
                fontSize: 12,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <span aria-hidden="true" style={{ fontSize: 8, color: 'var(--ln-warning)' }}>
                ●
              </span>
              {p}
            </span>
          ))}
        </div>

        {/* 视角 tab */}
        <nav aria-label="监控视角切换" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          <button
            type="button"
            onClick={() => setSelected(null)}
            aria-current={selected === null ? 'page' : undefined}
            className="ln-btn"
          >
            全部
          </button>
          {PERSPECTIVE_ORDER.map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setSelected(key)}
              aria-current={selected === key ? 'page' : undefined}
              className="ln-btn"
            >
              {PERSPECTIVE_META[key].label}
            </button>
          ))}
        </nav>

        {/* 主体：全部 → 卡片网格；单选 → 详情工作台 */}
        {selected === null ? (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
              gap: 10,
            }}
          >
            {PERSPECTIVE_ORDER.map((key) => (
              <MonitoringCard
                key={key}
                perspectiveKey={key}
                health={monitoring.health[key]}
                monitoring={monitoring}
                onOpen={() => setSelected(key)}
              />
            ))}
          </div>
        ) : (
          <PerspectiveDetailShell
            perspectiveKey={selected}
            monitoring={monitoring}
            onBack={() => setSelected(null)}
            onFocusAgent={focusAgent}
          />
        )}
      </div>
    </SectionPanel>
  );
}
