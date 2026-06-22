'use client';

import { useEffect, useRef, useCallback, useMemo } from 'react';
import Link from 'next/link';
import ObservatoryHeader from '@/components/system/observatory-header';
import { HandoffDashboardShell } from '@/components/ui/handoff-dashboard-shell';
import HandoffPressureAtlas from '@/components/ui/handoff-pressure-atlas';
import PrismButton from '@/components/ui/prism-button';
import PrismPanel from '@/components/ui/prism-panel';
import { useHandoffStore } from '@/lib/handoff-store';
import { connectUnifiedSSE } from '@/lib/sse';
import { ArrowLeft, Activity, GitBranch, ShieldAlert, FileSearch } from 'lucide-react';

const handoffBackendEnabled = process.env.NEXT_PUBLIC_ENABLE_HANDOFF_BACKEND === 'true';

export default function HandoffPage() {
  const store = useHandoffStore();
  const initialized = useRef(false);
  const criticalEventCount = useMemo(
    () =>
      store.recentEvents.filter((event) => {
        const severity = String((event as { severity?: string }).severity ?? 'INFO').toUpperCase();
        return severity === 'CRITICAL' || severity === 'ERROR';
      }).length,
    [store.recentEvents],
  );
  const failureSurfaces = [
    {
      label: 'Delegation Depth',
      value: `${store.metrics.total_spawned} spawned`,
      hint: '把委托层级与扩散速度放到同一视图里，识别是否开始失控。',
    },
    {
      label: 'Boundary Decay',
      value: `${(store.metrics.workspace_tag_survival_rate * 100).toFixed(1)}% survival`,
      hint: '边界标签一旦在链路中衰减，就意味着责任上下文正在丢失。',
    },
    {
      label: 'Artifact Drift',
      value: `${store.metrics.orphaned_artifacts} orphaned`,
      hint: '没有归属的产物，通常就是后续治理最难解释的风险出口。',
    },
  ];
  const spatialChapters = [
    {
      label: 'Delegation Canopy',
      value: `${store.metrics.max_chain_depth} layers`,
      hint: '把链路深度翻译成空间层级，看多跳委托是否开始失去可见性。',
      accent: 'rgba(96, 165, 250, 0.16)',
    },
    {
      label: 'Fanout Pressure',
      value: `${store.metrics.fan_out_ratio.toFixed(2)} : 1`,
      hint: '让 fanout storm 不只是数字，而是可以被迅速定位的扩散压力场。',
      accent: 'rgba(168, 85, 247, 0.16)',
    },
    {
      label: 'Boundary Debris',
      value: `${store.metrics.orphaned_artifacts} orphaned`,
      hint: '脱离主树的 artifact 应该像碎片带一样被单独识别，而不是淹没在事件流里。',
      accent: 'rgba(251, 191, 36, 0.14)',
    },
  ];

  // ── Initialize: try API, fall back to demo data ──
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    store.loadDemoData();
    if (handoffBackendEnabled) {
      store.fetchFromApi();
    }
  }, [store]);

  // ── Connect SSE when backend is available ──
  useEffect(() => {
    if (!handoffBackendEnabled || !store.useBackend) return;
    const connection = connectUnifiedSSE(
      '/api/events',
      () => {
        // Ignore non-handoff alert events on this page.
      },
      (streamType, data) => {
        if (streamType === 'handoff_observability' || streamType === 'delegation_tree') {
          store.handleSseEvent(data);
        }
      },
      (error) => {
        console.warn('Handoff SSE connection error:', error);
      },
      5,
    );

    return () => connection.close();
  }, [store]);

  const handleSelectNode = useCallback((nodeId: string | null) => {
    store.selectNode(nodeId);
  }, [store]);

  return (
    <div
      className="handoff-shell prism-grid-surface"
      style={{
        minHeight: '100vh',
        position: 'relative',
        overflowX: 'hidden',
      }}
    >
      <div
        style={{
          position: 'absolute',
          inset: 0,
          pointerEvents: 'none',
          background:
            'radial-gradient(circle at 18% 12%, rgba(129, 140, 248, 0.15), transparent 24%), radial-gradient(circle at 84% 16%, rgba(56, 189, 248, 0.14), transparent 22%), radial-gradient(circle at 72% 78%, rgba(251, 191, 36, 0.09), transparent 18%)',
          opacity: 0.95,
          zIndex: 0,
        }}
      />

      <div
        className="prism-page-shell"
        style={{
          position: 'relative',
          zIndex: 1,
          maxWidth: 1480,
          margin: '0 auto',
          paddingTop: 20,
          paddingBottom: 28,
          display: 'grid',
          gap: 18,
        }}
      >
        <ObservatoryHeader
          eyebrow="Delegation Observatory"
          title="把多 Agent 委托现场压成一张可以直接研判的扩散总谱"
          description="这一层承接首页的风险定调与调查页的单事件归因，把 delegation canopy、fanout pressure、boundary debris 与 orphaned outputs 放回同一张 handoff 工作台。"
          activeSection="handoff"
          backHref="/"
          backLabel="返回主界面"
          stats={[
            `${criticalEventCount} critical signals`,
            `${store.metrics.total_spawned} spawned`,
            store.loading ? '正在同步 handoff 数据...' : '拓扑状态已同步',
          ]}
          rightSlot={
            <div
              className="observatory-header-pill"
              style={{ color: store.useBackend ? '#34d399' : '#fbbf24' }}
            >
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: store.useBackend ? '#34d399' : '#fbbf24',
                  boxShadow: store.useBackend ? '0 0 0 4px rgba(52, 211, 153, 0.14)' : '0 0 0 4px rgba(251, 191, 36, 0.14)',
                }}
              />
              {store.useBackend ? 'Live backend' : 'Demo fallback'}
            </div>
          }
        />

        <div
          className="handoff-overview-grid"
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1.45fr) minmax(320px, 0.9fr)',
            gap: 18,
          }}
        >
          <PrismPanel size="hero" shimmer className="handoff-dashboard-panel">
            <div style={{ display: 'grid', gap: 18 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <div style={{ display: 'grid', gap: 6 }}>
                  <div style={{ fontSize: 11, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                    Delegation Atlas
                  </div>
                  <div className="prism-display" style={{ fontSize: 22, lineHeight: 1.08, letterSpacing: '-0.03em', color: 'var(--text-primary)' }}>
                    把委托链路、事件风暴与边界保真度收束成一张扩散判断台
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span className="handoff-summary-pill" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '7px 11px', borderRadius: 999, border: '1px solid var(--border-soft)', fontSize: 11 }}>
                    <GitBranch size={12} />
                    {store.metrics.total_spawned} delegated nodes
                  </span>
                  <span className="handoff-summary-pill" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '7px 11px', borderRadius: 999, border: '1px solid var(--border-soft)', fontSize: 11 }}>
                    <Activity size={12} />
                    {store.recentEvents.length} recent signals
                  </span>
                  <span className="handoff-summary-pill" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '7px 11px', borderRadius: 999, border: '1px solid var(--border-soft)', fontSize: 11, color: criticalEventCount > 0 ? '#fb7185' : 'var(--text-secondary)' }}>
                    <ShieldAlert size={12} />
                    {criticalEventCount} critical or error
                  </span>
                </div>
              </div>

              <p style={{ margin: 0, fontSize: 14, lineHeight: 1.76, color: 'var(--text-secondary)', maxWidth: 860 }}>
                handoff 页并不只是告诉你“树有多深”，它应该先告诉你哪里开始扩散失真、哪类边界正在掉落、哪些输出已经脱离归属。顶部摘要负责定调，下面的图表与视图负责让你快速决定下一步。
              </p>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 10 }}>
                {spatialChapters.map((item) => (
                  <div
                    key={item.label}
                    style={{
                      position: 'relative',
                      overflow: 'hidden',
                      padding: '13px 13px 12px',
                      borderRadius: 18,
                      border: '1px solid var(--border-soft)',
                      background: 'linear-gradient(180deg, rgba(8, 14, 28, 0.68) 0%, rgba(8, 14, 28, 0.32) 100%)',
                      boxShadow: '0 18px 34px rgba(0,0,0,0.12)',
                    }}
                  >
                    <div
                      style={{
                        position: 'absolute',
                        inset: 0,
                        background: `radial-gradient(circle at 0% 0%, ${item.accent}, transparent 52%)`,
                        pointerEvents: 'none',
                      }}
                    />
                    <div style={{ position: 'relative', zIndex: 1 }}>
                      <div style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                        {item.label}
                      </div>
                      <div className="prism-display" style={{ marginTop: 8, fontSize: 16, lineHeight: 1.16, color: 'var(--text-primary)' }}>
                        {item.value}
                      </div>
                      <div style={{ marginTop: 7, fontSize: 11, lineHeight: 1.62, color: 'var(--text-secondary)' }}>
                        {item.hint}
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              <div
                className="handoff-failure-strip"
                style={{}}
              >
                {failureSurfaces.map((item, index) => (
                  <div
                    key={item.label}
                    className="prism-story-card"
                    style={{
                      border: `1px solid ${index === 0 ? 'rgba(129, 140, 248, 0.24)' : index === 1 ? 'rgba(96, 165, 250, 0.22)' : 'rgba(251, 191, 36, 0.22)'}`,
                    }}
                  >
                    <div className="prism-story-card__label">
                      {item.label}
                    </div>
                    <div className="prism-story-card__value">
                      {item.value}
                    </div>
                    <div className="prism-story-card__hint">
                      {item.hint}
                    </div>
                  </div>
                ))}
              </div>

              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'minmax(0, 1.2fr) minmax(240px, 0.8fr)',
                  gap: 12,
                  alignItems: 'stretch',
                }}
                className="handoff-story-bridge"
              >
                <div
                  className="prism-story-card"
                  style={{
                    borderColor: 'rgba(108, 184, 255, 0.18)',
                  }}
                >
                  <div className="prism-story-card__label">Narrative Bridge</div>
                  <div className="prism-story-card__value">从首页定调，到调查定责，再到 handoff 展宽</div>
                  <div className="prism-story-card__hint">
                    首页告诉你风险正在发生，调查页锁定一条责任链，而这里把单点事件放回整个多 Agent 委托网络，验证异常是否只是局部症状。
                  </div>
                </div>

                <div className="prism-story-card">
                  <div className="prism-story-card__label">Continue Workflow</div>
                  <div className="prism-pill-row">
                    <Link href="/" style={{ textDecoration: 'none' }}>
                      <PrismButton variant="ghost" size="sm">
                        <ArrowLeft size={13} />
                        首页舞台
                      </PrismButton>
                    </Link>
                    <Link href="/investigations" style={{ textDecoration: 'none' }}>
                      <PrismButton variant="secondary" size="sm">
                        <FileSearch size={13} />
                        调查工作台
                      </PrismButton>
                    </Link>
                  </div>
                  <div className="prism-story-card__hint">
                    当某条链路开始越界，先在这里看扩散，再回到调查页做最终归因。
                  </div>
                </div>
              </div>
            </div>
          </PrismPanel>

          <HandoffPressureAtlas metrics={store.metrics} events={store.recentEvents} />

          <div
            className="handoff-kpi-grid"
            style={{
              display: 'grid',
              gap: 12,
              gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
              alignContent: 'start',
            }}
          >
            <div className="handoff-kpi-card prism-kpi">
              <div className="prism-kpi__label">Active Agents</div>
              <div className="prism-kpi__value">{store.metrics.active_count}</div>
              <div className="prism-kpi__hint">当前仍处于委托链中的子代理数量。</div>
            </div>
            <div className="handoff-kpi-card prism-kpi">
              <div className="prism-kpi__label">Fanout Ratio</div>
              <div className="prism-kpi__value">{store.metrics.fan_out_ratio.toFixed(2)}:1</div>
              <div className="prism-kpi__hint">用于快速识别并发委派是否开始失控。</div>
            </div>
            <div className="handoff-kpi-card prism-kpi">
              <div className="prism-kpi__label">Boundary Survival</div>
              <div className="prism-kpi__value">{(store.metrics.workspace_tag_survival_rate * 100).toFixed(1)}%</div>
              <div className="prism-kpi__hint">标记与边界上下文穿越多 Agent 链路后的保真度。</div>
            </div>
            <div className="handoff-kpi-card prism-kpi">
              <div className="prism-kpi__label">Orphaned Artifacts</div>
              <div className="prism-kpi__value">{store.metrics.orphaned_artifacts}</div>
              <div className="prism-kpi__hint">未能正确归属到委托链的输出或产物数量。</div>
            </div>
          </div>
        </div>

        {store.error && (
          <div
            className="handoff-alert-banner"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '12px 14px',
              borderRadius: 18,
              border: '1px solid var(--border-soft)',
              color: '#fb7185',
            }}
          >
            <ShieldAlert size={15} />
            {store.error}
          </div>
        )}

        <PrismPanel size="lg" grid shimmer className="handoff-dashboard-panel">
          <HandoffDashboardShell
            delegationTree={store.delegationTree}
            metrics={store.metrics}
            recentEvents={store.recentEvents}
            selectedNodeId={store.selectedNodeId}
            dependencyEdges={store.dependencyEdges}
            onSelectNode={handleSelectNode}
          />
        </PrismPanel>
      </div>
    </div>
  );
}
