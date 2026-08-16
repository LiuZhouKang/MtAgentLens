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

const STATUS_TONE: Record<MonitoringStatus, string> = {
  critical: 'var(--ln-danger)',
  warning: 'var(--ln-warning)',
  normal: 'var(--ln-success)',
};

export default function MonitoringSection() {
  const monitoring = useAppStore((s) => s.monitoring);
  const appState = useAppStore((s) => s.appState);
  const [selected, setSelected] = useState<PerspectiveKey | null>(null);
  const [focusHint, setFocusHint] = useState<string | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);

  // 卡片顶级错峰入场：fade-up + 轻微 scale + blur 渐清（GSAP 只动 transform/filter/opacity，
  // 复合层上运行不触发布局）。切视角重排时重新揭示。
  useGSAP(
    () => {
      const cards = gridRef.current?.querySelectorAll('.ln-monitor-card');
      if (!cards || cards.length === 0) return;
      gsap.from(cards, {
        opacity: 0,
        y: 16,
        scale: 0.97,
        filter: 'blur(4px)',
        duration: 0.55,
        stagger: 0.06,
        ease: 'power3.out',
        delay: 0.05,
        clearProps: 'transform, filter',
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
  const live = appState === 'live';

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
      meta={
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          {live ? (
            <span className="ln-monitor-live" aria-label="实时监控中">
              <i aria-hidden />
              LIVE
            </span>
          ) : null}
          <span>
            {monitoring.sourceLabel}
            {timeStr ? ` · ${timeStr}` : ''}
          </span>
        </span>
      }
      bodyClassName="ln-panel-body"
    >
      {/* 聚合健康横条（全站 ln token 语言）：总览状态 + 严重/关注/正常计数 */}
      <div className="ln-monitor-summary">
        <span className="ln-badge" data-tone={overall === 'critical' ? 'danger' : overall === 'warning' ? 'warning' : 'success'}>
          总览 {STATUS_LABEL[overall]}
        </span>
        <span className="ln-monitor-summary__counts">
          {(Object.keys(counts) as MonitoringStatus[]).map((status) =>
            counts[status] > 0 ? (
              <span
                key={status}
                className="ln-monitor-summary__count"
                data-tone={status}
              >
                <i aria-hidden style={{ background: STATUS_TONE[status] }} />
                {STATUS_LABEL[status]} {counts[status]}
              </span>
            ) : null,
          )}
          <span className="ln-monitor-summary__count" data-tone="muted">
            <i aria-hidden />
            {availableCount} 视角可用
          </span>
        </span>
      </div>

      {focusHint ? (
        <p className="ln-monitor-hint" role="status">
          {focusHint}
        </p>
      ) : null}

      {/* 视角 tab */}
      <nav aria-label="监控视角切换" className="ln-monitor-tabs">
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

      {/* 主体：全部 → 六卡网格（GSAP 错峰入场）；单选 → 详情工作台 */}
      {selected === null ? (
        <div ref={gridRef} className="ln-monitor-grid">
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
          key={selected}
          perspectiveKey={selected}
          monitoring={monitoring}
          onBack={() => setSelected(null)}
          onFocusAgent={focusAgent}
        />
      )}
    </SectionPanel>
  );
}
