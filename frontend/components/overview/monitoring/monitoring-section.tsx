'use client';

import { useRef, useState } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { useAppStore } from '@/lib/store';
import type {
  PerspectiveKey,
  MonitoringStatus,
  PerspectiveHealth,
} from '@/lib/monitoring/monitoring-types';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { StatusBadge } from '@/components/ui/linear/status-badge';
import { PERSPECTIVE_META, PERSPECTIVE_ORDER } from './perspective-config';
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

const HEALTH_CHIP: Record<MonitoringStatus, string> = {
  critical: '#f87171',
  warning: '#fbbf24',
  normal: '#4ade80',
};

export default function MonitoringSection() {
  const monitoring = useAppStore((s) => s.monitoring);
  const [selected, setSelected] = useState<PerspectiveKey | null>(null);
  const [focusHint, setFocusHint] = useState<string | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);

  // 卡片错峰入场（顶级滚动/入场动效）；切视角重排时重新揭示
  useGSAP(
    () => {
      const cards = gridRef.current?.querySelectorAll('.monitoring-card');
      if (!cards || cards.length === 0) return;
      gsap.from(cards, {
        opacity: 0,
        y: 18,
        duration: 0.5,
        stagger: 0.06,
        ease: 'power3.out',
        delay: 0.08,
        clearProps: 'transform',
      });
    },
    { scope: gridRef, dependencies: [monitoring, selected] },
  );

  if (!monitoring) return null;

  const overall = aggregateHealth(monitoring.health);
  const counts = { critical: 0, warning: 0, normal: 0 };
  for (const key of PERSPECTIVE_ORDER) {
    const h = monitoring.health[key];
    if (h && h.available) counts[h.status] += 1;
  }
  const availableCount = counts.critical + counts.warning + counts.normal;
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
      {/* 聚合健康横条（替代冗余的 6 按钮总览条）：总览状态 + 严重/关注/正常计数 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flexWrap: 'wrap',
          marginBottom: 12,
        }}
      >
        <StatusBadge
          tone={overall === 'critical' ? 'danger' : overall === 'warning' ? 'warning' : 'success'}
        >
          总览 {STATUS_LABEL[overall]}
        </StatusBadge>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginLeft: 'auto' }}>
          {(Object.keys(counts) as MonitoringStatus[]).map((status) =>
            counts[status] > 0 ? (
              <span
                key={status}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 5,
                  fontSize: 11,
                  fontFamily: 'var(--ln-mono, monospace)',
                  color: HEALTH_CHIP[status],
                }}
              >
                <span aria-hidden style={{ width: 6, height: 6, borderRadius: '50%', background: HEALTH_CHIP[status] }} />
                {STATUS_LABEL[status]} {counts[status]}
              </span>
            ) : null,
          )}
          <span style={{ fontSize: 11, color: 'var(--ln-text-muted)' }}>
            {availableCount} 视角可用
          </span>
        </div>
        {focusHint ? (
          <span className="ln-metric__label" style={{ fontSize: 11, width: '100%' }}>
            {focusHint}
          </span>
        ) : null}
      </div>

      {/* 视角 tab */}
      <nav aria-label="监控视角切换" style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
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

      {/* 主体：全部 → 卡片网格（GSAP 错峰入场）；单选 → 详情工作台 */}
      {selected === null ? (
        <div
          ref={gridRef}
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
            gap: 10,
          }}
        >
          {PERSPECTIVE_ORDER.map((key) => (
            <div key={key} className="monitoring-card" style={{ minWidth: 0 }}>
              <MonitoringCard
                perspectiveKey={key}
                health={monitoring.health[key]}
                monitoring={monitoring}
                onOpen={() => setSelected(key)}
              />
            </div>
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
    </SectionPanel>
  );
}
