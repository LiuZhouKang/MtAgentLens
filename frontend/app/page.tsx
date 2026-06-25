'use client';

import dynamic from 'next/dynamic';
import Link from 'next/link';
import { Suspense, useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAppStore } from '@/lib/store';
import { connectUnifiedSSE } from '@/lib/sse';
import { demoGraph, demoAlerts } from '@/lib/demo-data';
import { MagneticButton, Reveal } from '@/components/motion/prism-motion';
import TimelineScrubber from '@/components/ui/timeline-scrubber';
import AlertCard from '@/components/ui/alert-card';
import RunSelector from '@/components/ui/run-selector';
import LiveIndicator from '@/components/ui/live-indicator';
import HomeGlobeIntro from '@/components/ui/home-globe-intro';
import type { Home3DStoryChapter } from '@/components/ui/home-3d-story-director';
import { buildInvestigationHref } from '@/lib/investigation-route';
import { buildClusterHref } from '@/lib/cluster-route';
import { buildAgentHref } from '@/lib/agent-route';
import { getSeverityPalette } from '@/lib/severity-palette';
import { deriveTracebackEdgeIds } from '@/lib/traceback';
import { demoMetrics as demoHandoffMetrics } from '@/lib/handoff-demo-data';
import { Zap, X, Play, ArrowLeft, ShieldCheck, Radar } from 'lucide-react';

const CommandPalette = dynamic(() => import('@/components/ui/command-palette'), {
  ssr: false,
});

const ThemeToggle = dynamic(() => import('@/components/ui/theme-toggle'), {
  ssr: false,
});

const Home3DStoryDirector = dynamic(() => import('@/components/ui/home-3d-story-director'), {
  ssr: false,
});

const HomeEngineStage = dynamic(() => import('@/components/ui/home-engine-stage'), {
  ssr: false,
});

const metricCards = [
  { label: '零插桩观测', value: 'eBPF Native', hint: '不侵入运行时' },
  { label: '责任追溯', value: 'Authority Graph', hint: '签名到执行闭环' },
  { label: '证据密度', value: 'File / Proc / Net', hint: '行为优先于解释' },
];

const stageBeacons = [
  {
    label: 'Authority Claim',
    value: 'frontend-expert -> security-auditor',
    hint: 'claimed role 与进程身份开始失配',
    top: '14%',
    right: '22%',
    tone: 'var(--brand-primary)',
  },
  {
    label: 'Boundary Crossing',
    value: '/etc/shadow',
    hint: '敏感物料被非预期主体触达',
    top: '58%',
    right: '12%',
    tone: 'var(--alert)',
  },
];

function AppShell() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const store = useAppStore();
  const appState = store.appState;
  const alerts = store.alerts;
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [panelLevel, setPanelLevel] = useState(0);
  const [selectedAlertIdx, setSelectedAlertIdx] = useState<number | null>(null);
  const [activeStoryChapterId, setActiveStoryChapterId] = useState('boundary-cross');
  const [effectiveTheme, setEffectiveTheme] = useState<'light' | 'dark'>('dark');
  const [engineStageFailed, setEngineStageFailed] = useState(false);
  const auditMode = searchParams.get('audit') === 'headless';
  const [showGlobeIntro, setShowGlobeIntro] = useState(!auditMode);
  const autoDemoBootedRef = useRef(false);
  const introSrc = auditMode
    ? '/globe/index.html?embed=intro&audit=headless&v=prism-intro-31'
    : '/globe/index.html?embed=intro&v=prism-intro-31';

  useEffect(() => {
    const root = document.documentElement;
    const syncTheme = () => {
      setEffectiveTheme(root.classList.contains('dark') ? 'dark' : 'light');
    };

    syncTheme();
    const observer = new MutationObserver(syncTheme);
    observer.observe(root, {
      attributes: true,
      attributeFilter: ['class', 'data-theme'],
    });

    return () => observer.disconnect();
  }, []);

  const sseRef = useRef<{ close: () => void } | null>(null);
  useEffect(() => {
    if (appState === 'live') {
      sseRef.current = connectUnifiedSSE(
        '/api/events',
        (alert) => store.onAlertReceived(alert),
        () => {
          // Ignore non-alert streams on the homepage.
        },
        (err) => store.setError(err),
      );
    }
    return () => sseRef.current?.close();
  }, [appState, store]);

  const postGraph = useCallback((nodes: any[], edges: any[]) => {
    if (store.engineRef.current) {
      store.engineRef.current.updateGraph({ nodes, edges });
    }
  }, [store]);

  useEffect(() => {
    if (store.graph.nodes.length > 0) {
      postGraph(store.graph.nodes, store.graph.edges);
    }
  }, [postGraph, store.graph.edges, store.graph.nodes]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
      if (e.key === 'Escape') {
        setPanelLevel(0);
        setSelectedAlertIdx(null);
        setPaletteOpen(false);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const loadDemo = useCallback(() => {
    store.setActiveRunId(null);
    store.setGraph(demoGraph.nodes, demoGraph.edges);
    store.setAlerts(demoAlerts);
    store.setError(null);
    store.setAppState('replay');
    postGraph(demoGraph.nodes, demoGraph.edges);
  }, [postGraph, store]);

  useEffect(() => {
    if (autoDemoBootedRef.current) return;
    if (store.activeRunId) return;
    if (store.graph.nodes.length > 0) return;
    if (panelLevel > 0) return;
    if (appState !== 'idle' && appState !== 'init') return;

    const timer = setTimeout(() => {
      if (autoDemoBootedRef.current) return;
      autoDemoBootedRef.current = true;
      loadDemo();
    }, 900);

    return () => clearTimeout(timer);
  }, [appState, loadDemo, panelLevel, store.activeRunId, store.graph.nodes.length]);

  const openInvestigation = useCallback((alertId: string) => {
    router.push(buildInvestigationHref(alertId));
  }, [router]);

  const highlightEdgeIds = useCallback((edgeIds: string[]) => {
    if (edgeIds.length) {
      store.highlightPath(edgeIds);
    } else {
      store.clearHighlights();
    }
  }, [store]);

  const openTraceback = useCallback(() => {
    const alert = alerts[selectedAlertIdx ?? 0];
    if (!alert) return;

    const edgeIds = deriveTracebackEdgeIds(alert, store.graph.edges);
    setPanelLevel(2);
    highlightEdgeIds(edgeIds);
  }, [alerts, highlightEdgeIds, selectedAlertIdx, store.graph.edges]);

  const closePanel = useCallback(() => {
    setPanelLevel(0);
    setSelectedAlertIdx(null);
    highlightEdgeIds([]);
  }, [highlightEdgeIds]);

  const selectedAlert = selectedAlertIdx != null ? alerts[selectedAlertIdx] : null;
  const compactAlerts = alerts.slice(0, 4);
  const featuredAlert = compactAlerts[0] ?? null;
  const heroVisible = panelLevel === 0;
  const hasRealSession = !!store.activeRunId;
  const featuredAgentId = featuredAlert?.primary_agent?.agent_id ?? null;
  const primaryCtaLabel = featuredAlert ? '回放运行链路' : hasRealSession ? '进入总览态势' : '查看示例场景';
  const primaryCtaAction = featuredAlert
    ? () => router.push('/traces')
    : hasRealSession
      ? () => router.push('/overview')
      : loadDemo;
  const graphAgentCount = store.graph.nodes.filter((node) => node.node_type === 'Agent').length;
  const boundaryCrossingCount = store.graph.edges.filter((edge) => edge.edge_type === 'PolicyBoundaryCrossing').length;
  const sceneStats = {
    nodes: store.graph.nodes.length,
    links: store.graph.edges.length,
    boundaryCrossings: boundaryCrossingCount,
  };
  const criticalAlertCount = alerts.filter((item) => item.severity === 'Critical').length;
  const sessionModeLabel = hasRealSession ? '真实观测会话' : '示例调查场景';
  const statusCards = [
    { label: '风险事件', value: `${alerts.length}`, hint: criticalAlertCount > 0 ? `${criticalAlertCount} 个高优先级` : '当前暂无高危事件' },
    { label: '责任主体', value: `${graphAgentCount}`, hint: '纳入同一责任图' },
    { label: '边界穿越', value: `${boundaryCrossingCount}`, hint: '优先进入归因调查' },
  ];

  const storyChapters = useMemo<Home3DStoryChapter[]>(() => {
    const alertTimelineCount = featuredAlert?.timeline?.steps?.length ?? 0;
    return [
      {
        id: 'authority-surface',
        stage: 'Authority Surface',
        title: '先看谁在授权、谁在委托、谁真正把这一步执行出去',
        summary: '镜头优先展示 authority claim、delegation 与 context flow，把责任从角色声明层压缩到真实行动链路层。',
        metricLabel: 'delegation',
        metricValue: String(store.graphMetrics?.delegation_depth ?? 0),
        accent: '#8fe4ff',
        edgeIds: store.graph.edges
          .filter((edge) => edge.edge_type === 'AgentDelegatesTo' || edge.edge_type === 'ContextFlowsFrom')
          .map((edge) => edge.edge_id),
      },
      {
        id: 'boundary-cross',
        stage: 'Boundary Crossing',
        title: '让越界成为首页舞台里无法忽视的断裂时刻',
        summary: '把 taint propagation 与 boundary crossing 变成空间裂缝，而不是一行普通告警，让最高优先级风险先被看见。',
        metricLabel: 'alerts',
        metricValue: String(criticalAlertCount),
        accent: '#fb7185',
        edgeIds: store.graph.edges
          .filter((edge) => edge.edge_type === 'PolicyBoundaryCrossing' || edge.edge_type === 'TaintPropagatesTo')
          .map((edge) => edge.edge_id),
      },
      {
        id: 'evidence-lock',
        stage: 'Evidence Lock',
        title: '把 handoff 扩散与证据密度压成可以直接进入的调查镜头',
        summary: '用现有 handoff 样本和告警时间线，把多个代理的扩散、回收与 orphaned artifact 风险压到同一条叙事主线上。',
        metricLabel: 'handoff',
        metricValue: `${demoHandoffMetrics.total_spawned}/${demoHandoffMetrics.active_count}`,
        accent: '#fbbf24',
        edgeIds: alertTimelineCount > 0
          ? (featuredAlert?.graph_snapshot.edges ?? [])
              .filter((edge) => edge.edge_type === 'IntentCausesAction')
              .map((edge) => edge.edge_id)
          : [],
      },
    ];
  }, [criticalAlertCount, featuredAlert, store.graph.edges, store.graphMetrics?.delegation_depth]);

  useEffect(() => {
    if (!heroVisible || panelLevel !== 0) return;
    const activeChapter = storyChapters.find((chapter) => chapter.id === activeStoryChapterId) ?? storyChapters[0];
    if (!activeChapter) return;
    highlightEdgeIds(activeChapter.edgeIds);
    return () => {
      highlightEdgeIds([]);
    };
  }, [activeStoryChapterId, heroVisible, highlightEdgeIds, panelLevel, storyChapters]);

  return (
    <div className="home-shell prism-grid-surface" style={{ position: 'relative', width: '100vw', height: '100vh', overflow: 'hidden', background: 'var(--home-stage-bg)' }}>
      {showGlobeIntro ? (
        <HomeGlobeIntro
          src={introSrc}
          onComplete={() => setShowGlobeIntro(false)}
        />
      ) : null}

      <div
        className="prism-ambient-orb home-ambient-orb-primary"
        style={{
          top: '12%',
          left: '8%',
          width: 260,
          height: 260,
          background: 'radial-gradient(circle, rgba(84, 190, 255, 0.18) 0%, rgba(84, 190, 255, 0.02) 68%, transparent 78%)',
        }}
      />
      <div
        className="prism-ambient-orb home-ambient-orb-secondary"
        style={{
          right: '18%',
          top: '18%',
          width: 320,
          height: 320,
          animationDelay: '-5.2s',
          background: 'radial-gradient(circle, rgba(137, 92, 255, 0.16) 0%, rgba(137, 92, 255, 0.02) 66%, transparent 78%)',
        }}
      />
      <div className="prism-scanline" />

      {!auditMode && (
        <HomeEngineStage
          active={!engineStageFailed}
          featuredAlert={featuredAlert}
          theme={effectiveTheme}
          panelLevel={panelLevel}
          onReady={() => {
            setEngineStageFailed(false);
          }}
          onError={() => {
            setEngineStageFailed(true);
          }}
          onNodeSelected={(nodeId) => {
            const idx = alerts.findIndex(
              (a) =>
                a.primary_agent?.agent_id === nodeId ||
                a.involved_agents?.some((ia) => ia.agent_id === nodeId),
            );
            if (idx >= 0) {
              setSelectedAlertIdx(idx);
              setPanelLevel(1);
            }
          }}
        />
      )}

      <div
        className="home-gradient-layer"
        style={{
          position: 'absolute',
          inset: 0,
          background: 'var(--home-overlay-gradient)',
          pointerEvents: 'none',
          zIndex: 1,
        }}
      />

      <div
        className="home-top-shine-layer"
        style={{
          position: 'absolute',
          inset: 0,
          background: 'var(--home-top-shine)',
          pointerEvents: 'none',
          zIndex: 2,
        }}
      />

      {heroVisible && (
        <>
          <div className="home-stage-beacons" style={{ position: 'absolute', inset: 0, zIndex: 8, pointerEvents: 'none' }}>
            {stageBeacons.map((beacon, index) => (
              <Reveal
                key={beacon.label}
                disabled={auditMode}
                delay={0.18 + index * 0.08}
                mode="mount"
                style={{
                  position: 'absolute',
                  top: beacon.top,
                  right: beacon.right,
                  width: 184,
                  pointerEvents: 'none',
                }}
              >
                <div
                  className="home-stage-beacon prism-shimmer-border"
                  style={{
                    display: 'grid',
                    gap: 7,
                    padding: '10px 11px 9px',
                    borderRadius: 16,
                    background: 'var(--home-glass-strong)',
                    border: `1px solid color-mix(in srgb, ${beacon.tone} 24%, transparent)`,
                    boxShadow: '0 14px 28px rgba(0, 0, 0, 0.16)',
                    backdropFilter: 'blur(18px)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span
                      className="home-stage-beacon-dot"
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        background: beacon.tone,
                        boxShadow: `0 0 0 6px color-mix(in srgb, ${beacon.tone} 14%, transparent), 0 0 16px color-mix(in srgb, ${beacon.tone} 38%, transparent)`,
                      }}
                    />
                    <div style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                      {beacon.label}
                    </div>
                  </div>
                  <div style={{ fontSize: 12, lineHeight: 1.38, color: 'var(--text-primary)', fontWeight: 600 }}>
                    {beacon.value}
                  </div>
                  <div style={{ fontSize: 10, lineHeight: 1.5, color: 'var(--text-secondary)' }}>
                    {beacon.hint}
                  </div>
                </div>
              </Reveal>
            ))}
          </div>
        </>
      )}

      {appState === 'error' && (
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            zIndex: 50,
            padding: '10px 16px',
            background: 'rgba(248,113,113,0.16)',
            borderBottom: '1px solid rgba(248,113,113,0.28)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <span style={{ fontSize: 13, color: '#fecaca' }}>
            {store.errorMessage || '后端连接失败，请检查 collector 或 API 状态。'}
          </span>
          <button
            onClick={() => {
              store.setError(null);
              store.setAppState('idle');
            }}
            style={{
              padding: '6px 12px',
              borderRadius: 999,
              fontSize: 12,
              background: 'rgba(248,113,113,0.18)',
              border: '1px solid rgba(248,113,113,0.3)',
              color: '#fff',
              cursor: 'pointer',
            }}
          >
            重试
          </button>
        </div>
      )}

      <header
        className="home-header"
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          zIndex: 10,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '18px 26px',
          pointerEvents: 'none',
        }}
      >
        <Reveal disabled={auditMode} className="home-header-left" delay={0.02} style={{ display: 'flex', alignItems: 'center', gap: 16, pointerEvents: 'auto' }}>
          <div
            className="home-header-brand prism-shimmer-border"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 11,
              padding: '8px 0',
              borderRadius: 0,
              background: 'transparent',
              border: 'none',
              boxShadow: 'none',
              backdropFilter: 'none',
            }}
          >
            <div
              style={{
                width: 28,
                height: 1,
                borderRadius: 999,
                background: 'linear-gradient(90deg, rgba(143,228,255,0.95) 0%, rgba(143,228,255,0.14) 100%)',
              }}
            />
            <div>
              <div style={{ fontSize: 10, letterSpacing: '0.26em', color: 'var(--text-tertiary)' }}>MTAGENTPRISM</div>
              <div className="prism-display" style={{ fontSize: 15, color: 'var(--text-primary)', lineHeight: 1.1 }}>
                责任观测入口
              </div>
            </div>
          </div>
          <div
            className="home-context-pills"
            style={{
              display: 'none',
              alignItems: 'center',
              gap: 8,
              padding: '8px 10px',
              borderRadius: 999,
              background: 'var(--home-glass-soft)',
              border: '1px solid var(--home-glass-border)',
            }}
          >
            {['权责', '委派', '执行', '证据'].map((item, index) => (
              <div key={item} style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                {index > 0 ? <span style={{ width: 3, height: 3, borderRadius: '50%', background: 'var(--text-quaternary)' }} /> : null}
                <span
                  style={{
                    fontSize: 11,
                    letterSpacing: '0.08em',
                    color: 'var(--text-secondary)',
                  }}
                >
                  {item}
                </span>
              </div>
            ))}
          </div>
        </Reveal>

        <Reveal disabled={auditMode} className="home-header-right" delay={0.08} style={{ display: 'flex', alignItems: 'center', gap: 12, pointerEvents: 'auto' }}>
          <ThemeToggle />
          <RunSelector />
          <div
            className="home-command-chip prism-shimmer-border"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '8px 0 8px 14px',
              borderRadius: 999,
              background: 'transparent',
              border: 'none',
              backdropFilter: 'none',
            }}
          >
            <LiveIndicator />
            <span style={{ fontSize: 11, color: 'var(--text-quaternary)', letterSpacing: '0.08em' }}>
              CMD/CTRL + K
            </span>
          </div>
        </Reveal>
      </header>

      <div
        className="home-overlay"
        style={{
          position: 'absolute',
          inset: 0,
          zIndex: 8,
          display: 'flex',
          alignItems: 'stretch',
          justifyContent: 'space-between',
          padding: '108px 34px 28px',
          pointerEvents: 'none',
        }}
      >
        <section
          className="home-hero-panel"
          style={{
            display: heroVisible ? 'flex' : 'none',
            flexDirection: 'column',
            justifyContent: 'space-between',
            pointerEvents: 'auto',
            width: 'min(404px, calc(100vw - 68px))',
            maxWidth: 404,
          }}
        >
          <div>
            <Reveal
              disabled={auditMode}
              className="home-hero-badge"
              delay={0.08}
              style={{
                display: 'inline-flex',
                marginBottom: 14,
              }}
            >
              <span className="prism-kicker-rule">MtAgentLens Responsibility Observatory</span>
            </Reveal>

            <Reveal disabled={auditMode} delay={0.13}>
              <h1
                className="home-hero-title prism-display--hero"
                style={{
                  margin: 0,
                  fontSize: 'clamp(40px, 4rem, 64px)',
                  lineHeight: 1.02,
                  letterSpacing: 0,
                  color: 'var(--text-primary)',
                  maxWidth: 388,
                  textShadow: 'var(--home-hero-shadow)',
                }}
              >
                让多 Agent
                <br />
                责任现场
                <br />
                直接可判
              </h1>
            </Reveal>

            <Reveal disabled={auditMode} delay={0.18}>
              <p
                className="home-hero-copy"
                style={{
                  margin: '18px 0 0',
                  maxWidth: 356,
                  fontSize: 14,
                  lineHeight: 1.72,
                  color: 'var(--text-secondary)',
                }}
              >
                MtAgentLens 不再把多 Agent 风险拆成日志、链路与截图，而是把 authority、delegation、execution 与 evidence
                收束成一个可以直接进入判断的责任现场。
              </p>
            </Reveal>

            <Reveal disabled={auditMode} className="home-cta-row" delay={0.22} style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 20 }}>
              <MagneticButton
                motionDisabled={auditMode}
                type="button"
                onClick={primaryCtaAction}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '12px 16px',
                  borderRadius: 999,
                  border: '1px solid var(--home-primary-button-border)',
                  background: 'var(--home-primary-button-bg)',
                  color: 'var(--text-primary)',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: 'pointer',
                  boxShadow: '0 18px 42px rgba(17, 58, 112, 0.34)',
                }}
              >
                <Zap size={16} />
                {primaryCtaLabel}
              </MagneticButton>
              <MagneticButton
                motionDisabled={auditMode}
                type="button"
                onClick={() => setPaletteOpen(true)}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '12px 16px',
                  borderRadius: 999,
                  border: '1px solid var(--home-secondary-button-border)',
                  background: 'var(--home-secondary-button-bg)',
                  color: 'var(--text-primary)',
                  fontSize: 12,
                  fontWeight: 500,
                  cursor: 'pointer',
                  backdropFilter: 'blur(16px)',
                }}
              >
                <Radar size={16} />
                调出指挥面板
              </MagneticButton>
            </Reveal>

            <Reveal disabled={auditMode} delay={0.24}>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 10 }}>
                <Link href="/overview" style={{ textDecoration: 'none' }}>
                  <span className="prism-meta-inline__item">进入 Stellar Overview</span>
                </Link>
                <Link href="/traces" style={{ textDecoration: 'none' }}>
                  <span className="prism-meta-inline__item">进入 Trace Workbench</span>
                </Link>
                {featuredAgentId ? (
                  <Link href={buildClusterHref(featuredAgentId)} style={{ textDecoration: 'none' }}>
                    <span className="prism-meta-inline__item">进入 Cluster Observatory</span>
                  </Link>
                ) : null}
                {featuredAgentId ? (
                  <Link href={buildAgentHref(featuredAgentId)} style={{ textDecoration: 'none' }}>
                    <span className="prism-meta-inline__item">进入 Agent Cockpit</span>
                  </Link>
                ) : null}
              </div>
            </Reveal>

            <Reveal
              disabled={auditMode}
              className="home-hero-footnote"
              delay={0.26}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                flexWrap: 'wrap',
                  marginTop: 18,
              }}
            >
              <div className="prism-meta-inline">
                {metricCards.map((card) => (
                  <div key={card.label} className="prism-meta-inline__item">
                    <ShieldCheck size={12} color="rgba(143, 228, 255, 0.82)" />
                    <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{card.value}</span>
                    <span>{card.hint}</span>
                  </div>
                ))}
              </div>
            </Reveal>

            <Reveal disabled={auditMode} delay={0.3}>
              <div
                className="home-proof-rail prism-dossier-card prism-grid-surface"
                style={{
                  marginTop: 22,
                  maxWidth: 390,
                  boxShadow: '0 28px 68px rgba(2, 8, 23, 0.22)',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 12,
                  }}
                >
                  <div className="prism-kicker-rule" style={{ gap: 0 }}>
                    Featured Case Dossier
                  </div>
                  <div
                    style={{
                      fontSize: 10,
                      color: 'var(--text-tertiary)',
                      textTransform: 'uppercase',
                      letterSpacing: '0.12em',
                    }}
                  >
                    {sessionModeLabel}
                  </div>
                </div>

                <div className="prism-display" style={{ marginTop: 10, fontSize: 22, lineHeight: 1.06, letterSpacing: '-0.055em', color: 'var(--text-primary)' }}>
                  {featuredAlert ? featuredAlert.title : '让风险事件直接成为首页主叙事'}
                </div>
                <div style={{ marginTop: 8, fontSize: 12.5, lineHeight: 1.64, color: 'var(--text-secondary)' }}>
                  {featuredAlert
                    ? featuredAlert.summary
                    : '在真实会话尚未接入时，首页会回落到示例场景，让 authority、delegation、evidence 的关系依然以完整叙事方式被看见。'}
                </div>

                <div className="prism-meta-inline" style={{ marginTop: 10 }}>
                  {featuredAlert
                    ? [
                        `${featuredAlert.decision} decision`,
                        `${featuredAlert.confidence.toFixed(2)} confidence`,
                        featuredAlert.severity,
                      ].map((chip) => (
                        <span
                          key={chip}
                          className="prism-meta-inline__item"
                        >
                          {chip}
                        </span>
                      ))
                    : statusCards.slice(0, 3).map((item) => (
                        <span
                          key={item.label}
                          className="prism-meta-inline__item"
                        >
                          {item.label} {item.value}
                        </span>
                      ))}
                </div>

                <div className="home-proof-grid" style={{ marginTop: 12, display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 }}>
                  {statusCards.slice(0, 3).map((card) => (
                    <div
                      key={card.label}
                      style={{
                        padding: '10px 10px 9px',
                        borderRadius: 16,
                        background: 'color-mix(in srgb, var(--surface-soft) 80%, transparent)',
                        border: '1px solid color-mix(in srgb, var(--border-soft) 88%, transparent)',
                      }}
                    >
                      <div style={{ fontSize: 9.5, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                        {card.label}
                      </div>
                      <div style={{ marginTop: 5, fontSize: 14, lineHeight: 1.15, color: 'var(--text-primary)', fontWeight: 650 }}>
                        {card.value}
                      </div>
                      <div style={{ marginTop: 3, fontSize: 10, lineHeight: 1.45, color: 'var(--text-secondary)' }}>
                        {card.hint}
                      </div>
                    </div>
                  ))}
                </div>

                {featuredAlert ? (
                  <MagneticButton
                    motionDisabled={auditMode}
                    type="button"
                    onClick={() => openInvestigation(featuredAlert.alert_id)}
                    style={{
                      marginTop: 12,
                      width: '100%',
                      justifyContent: 'space-between',
                      padding: '12px 14px',
                      borderRadius: 16,
                      border: '1px solid var(--home-primary-button-border)',
                      background: 'linear-gradient(180deg, rgba(39, 112, 194, 0.18) 0%, rgba(18, 47, 88, 0.22) 100%)',
                      color: 'var(--text-primary)',
                      cursor: 'pointer',
                    }}
                  >
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                      <span
                        style={{
                          width: 8,
                          height: 8,
                          borderRadius: '50%',
                          background: getSeverityPalette(featuredAlert.severity).color,
                        }}
                      />
                      直接进入当前最高优先级案件
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Signal -&gt; Timeline -&gt; Verdict</span>
                  </MagneticButton>
                ) : null}
              </div>
            </Reveal>
          </div>
        </section>
      </div>

      {heroVisible && (
        <Home3DStoryDirector
          chapters={storyChapters}
          activeChapterId={activeStoryChapterId}
          sceneStats={sceneStats}
          onSelectChapter={(chapter) => {
            setActiveStoryChapterId(chapter.id);
          }}
        />
      )}

      {heroVisible && (
        <Reveal
          disabled={auditMode}
          className="home-globe-legend prism-shimmer-border prism-grid-surface"
          delay={0.3}
          style={{
            position: 'absolute',
            right: 24,
            bottom: 24,
            zIndex: 9,
            width: 'min(312px, calc(100vw - 48px))',
            padding: '12px 12px 11px',
            borderRadius: 20,
            background: 'var(--home-glass-strong)',
            border: '1px solid var(--home-glass-border)',
            boxShadow: '0 18px 38px rgba(0, 0, 0, 0.2)',
            backdropFilter: 'blur(18px)',
            pointerEvents: 'auto',
          }}
        >
          <div style={{ display: 'grid', gap: 6 }}>
            <div style={{ fontSize: 10, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
              Investigation Paths
            </div>
            <div style={{ fontSize: 16, lineHeight: 1.14, letterSpacing: '-0.04em', color: 'var(--text-primary)', fontWeight: 600 }}>
              从首页定调后，直接进入你下一步要判定的观测层与工作台。
            </div>
          </div>

          <div style={{ display: 'grid', gap: 7, marginTop: 11 }}>
            {[
              {
                label: 'Overview',
                hint: '先锁定最危险的责任星群与 delegation path。',
                href: '/overview',
              },
              featuredAgentId
                ? {
                    label: 'Cluster',
                    hint: '查看当前主事件所在 cluster 的 fan-out 与边界漂移。',
                    href: buildClusterHref(featuredAgentId),
                  }
                : null,
              featuredAgentId
                ? {
                    label: 'Agent',
                    hint: '把 claimed role、资源接触和证据摘要收束到单体视图。',
                    href: buildAgentHref(featuredAgentId),
                  }
                : null,
              featuredAlert
                ? {
                    label: 'Investigation',
                    hint: '把 causality chain、timeline、evidence 与 verdict 收束成法证工作台。',
                    href: buildInvestigationHref(featuredAlert.alert_id),
                  }
                : {
                    label: 'Handoff',
                    hint: '当没有主事件时，直接查看委托拓扑和扩散压力。',
                    href: '/handoff',
                  },
            ]
              .filter((item): item is { label: string; hint: string; href: string } => !!item)
              .map((item, index) => (
                <Link
                  key={item.label}
                  href={item.href}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '30px minmax(0, 1fr)',
                    gap: 8,
                    alignItems: 'start',
                    padding: '9px 9px 8px',
                    borderRadius: 14,
                    background: 'var(--surface-soft)',
                    border: '1px solid var(--border-soft)',
                    textDecoration: 'none',
                    color: 'inherit',
                  }}
                >
                  <div
                  style={{
                    width: 30,
                    height: 30,
                    borderRadius: 999,
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 9,
                    fontWeight: 700,
                    color: 'var(--text-primary)',
                    background:
                      index === 0
                        ? 'rgba(108, 194, 255, 0.16)'
                        : index === 1
                          ? 'rgba(139, 125, 255, 0.16)'
                          : index === 2
                            ? 'rgba(251, 191, 36, 0.16)'
                            : 'rgba(52, 211, 153, 0.16)',
                    border: `1px solid ${
                      index === 0
                        ? 'rgba(108, 194, 255, 0.24)'
                        : index === 1
                          ? 'rgba(139, 125, 255, 0.24)'
                          : index === 2
                            ? 'rgba(251, 191, 36, 0.24)'
                            : 'rgba(52, 211, 153, 0.24)'
                    }`,
                  }}
                >
                  0{index + 1}
                </div>
                <div>
                  <div style={{ fontSize: 10.5, letterSpacing: '0.08em', color: 'var(--text-primary)', textTransform: 'uppercase' }}>
                    {item.label}
                  </div>
                  <div style={{ marginTop: 3, fontSize: 10.5, lineHeight: 1.5, color: 'var(--text-secondary)' }}>
                    {item.hint}
                  </div>
                </div>
                </Link>
              ))}
          </div>
        </Reveal>
      )}

      {panelLevel === 1 && selectedAlert && (
        <div
          style={{
            position: 'absolute',
            right: 24,
            top: 104,
            bottom: 24,
            width: 'min(760px, calc(100vw - 48px))',
            zIndex: 20,
            display: 'flex',
            flexDirection: 'column',
            padding: 20,
            borderRadius: 28,
            background: 'linear-gradient(180deg, var(--bg-elevated) 0%, var(--bg-panel) 100%)',
            border: '1px solid var(--border-soft)',
            backdropFilter: 'blur(22px)',
            animation: 'panelSlideIn 0.45s cubic-bezier(0.16, 1, 0.3, 1)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
            <button
              onClick={closePanel}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                background: 'none',
                border: 'none',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
                fontSize: 13,
              }}
            >
              <X size={16} />
              返回首页主舞台
            </button>
            <button
              onClick={() => openInvestigation(selectedAlert.alert_id)}
              style={{
                padding: '10px 14px',
                borderRadius: 999,
                fontSize: 12,
                fontWeight: 600,
                background: 'var(--surface-soft)',
                border: '1px solid var(--border-soft)',
                color: 'var(--text-primary)',
                cursor: 'pointer',
              }}
            >
              在调查页打开
            </button>
            <button
              onClick={openTraceback}
              style={{
                padding: '10px 14px',
                borderRadius: 999,
                fontSize: 12,
                fontWeight: 600,
                background: 'rgba(96, 165, 250, 0.12)',
                border: '1px solid rgba(96, 165, 250, 0.25)',
                color: '#d9efff',
                cursor: 'pointer',
              }}
            >
              追溯根因
            </button>
          </div>
          <div className="prism-premium-scroll" style={{ flex: 1, minHeight: 0, overflowY: 'auto', paddingRight: 4 }}>
            <AlertCard alert={selectedAlert} />
          </div>
        </div>
      )}

      {panelLevel === 2 && selectedAlert && (
        <div
          style={{
            position: 'absolute',
            right: 24,
            top: 104,
            bottom: 24,
            width: 'min(760px, calc(100vw - 48px))',
            zIndex: 21,
            display: 'flex',
            flexDirection: 'column',
            padding: 24,
            borderRadius: 28,
            background: 'linear-gradient(180deg, var(--bg-elevated) 0%, var(--bg-panel) 100%)',
            border: '1px solid var(--border-soft)',
            backdropFilter: 'blur(24px)',
            animation: 'panelSlideIn 0.45s cubic-bezier(0.16, 1, 0.3, 1)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 18 }}>
            <button
              onClick={() => setPanelLevel(1)}
              style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer' }}
            >
              <ArrowLeft size={16} />
            </button>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '0.08em' }}>责任根因回放</span>
            <button
              onClick={closePanel}
              style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', marginLeft: 'auto' }}
            >
              <X size={16} />
            </button>
          </div>

          <div
            style={{
              marginBottom: 18,
              padding: '16px 18px',
              borderRadius: 18,
              background: 'rgba(99, 102, 241, 0.08)',
              border: '1px solid rgba(99, 102, 241, 0.14)',
            }}
          >
            <div style={{ fontSize: 11, letterSpacing: '0.12em', color: '#a5b4fc', textTransform: 'uppercase' }}>Root Cause</div>
            <p style={{ margin: '8px 0 0', fontSize: 14, lineHeight: 1.75, color: 'var(--text-secondary)' }}>
              {selectedAlert.action_hint || selectedAlert.reasoning || '沿着责任图谱回放完整因果链。'}
            </p>
          </div>

          <div className="prism-premium-scroll" style={{ flex: 1, minHeight: 0, overflowY: 'auto', paddingRight: 4 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 0, borderLeft: '2px solid rgba(99,102,241,0.22)', paddingLeft: 18 }}>
              {(selectedAlert.timeline?.steps || []).map((step: any, i: number) => (
                <div
                  key={i}
                  style={{
                    position: 'relative',
                    paddingBottom: i < (selectedAlert.timeline?.steps?.length || 0) - 1 ? 20 : 0,
                  }}
                >
                  <div
                    style={{
                      position: 'absolute',
                      left: -24,
                      top: 6,
                      width: 10,
                      height: 10,
                      borderRadius: '50%',
                      background:
                        step.operation === 'FileRead'
                          ? '#7dd3fc'
                          : step.operation === 'Delegation'
                            ? '#c4b5fd'
                            : '#fb7185',
                      border: '2px solid rgba(7,11,20,0.95)',
                    }}
                  />
                  <div style={{ fontSize: 11, color: 'var(--text-quaternary)', fontFamily: 'monospace' }}>
                    T+{(step.timestamp_ns / 1e9).toFixed(1)}s
                  </div>
                  <div style={{ marginTop: 6, fontSize: 14, lineHeight: 1.75, color: 'var(--text-primary)' }}>
                    {step.description}
                  </div>
                  {step.resource && (
                    <code style={{ display: 'inline-block', marginTop: 6, fontSize: 11, color: 'var(--text-tertiary)' }}>
                      {step.resource}
                    </code>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div style={{ marginTop: 18, display: 'flex', alignItems: 'center', gap: 12 }}>
            <button
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '10px 14px',
                borderRadius: 999,
                fontSize: 12,
                fontWeight: 600,
                background: 'rgba(96, 165, 250, 0.12)',
                border: '1px solid rgba(96, 165, 250, 0.22)',
                color: '#d9efff',
                cursor: 'pointer',
              }}
            >
              <Play size={14} />
              回放
            </button>
            <div style={{ position: 'relative', flex: 1, height: 2, borderRadius: 999, background: 'rgba(255,255,255,0.08)' }}>
              <div
                style={{
                  position: 'absolute',
                  left: '34%',
                  top: -4,
                  width: 10,
                  height: 10,
                  borderRadius: '50%',
                  background: '#93c5fd',
                  boxShadow: '0 0 14px rgba(147, 197, 253, 0.85)',
                }}
              />
            </div>
          </div>
        </div>
      )}

      {appState === 'init' && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            pointerEvents: 'none',
            zIndex: 6,
          }}
        >
          <h1 style={{ margin: 0, fontSize: 46, fontWeight: 600, letterSpacing: '0.14em', color: 'var(--text-primary)' }}>
            MTAGENTPRISM
          </h1>
          <p style={{ marginTop: 12, fontSize: 14, letterSpacing: '0.05em', color: 'var(--text-secondary)' }}>
            零插桩多 Agent 责任归因界面正在启动
          </p>
        </div>
      )}

      {paletteOpen && <CommandPalette onClose={() => setPaletteOpen(false)} />}
      {appState === 'replay' && panelLevel > 0 && <TimelineScrubber />}

      <style>{`
        @keyframes panelSlideIn {
          from {
            opacity: 0;
            transform: translateY(20px) scale(0.98);
          }
          to {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }

        @media (max-width: 1100px) {
          .home-story-director {
            top: auto !important;
            right: 24px !important;
            bottom: 112px !important;
          }

          .home-gradient-layer {
            background: var(--home-overlay-gradient-tablet) !important;
          }

          .home-stage-beacons {
            display: none !important;
          }

          .home-stage-manifesto {
            width: min(560px, calc(100vw - 180px)) !important;
          }
        }

        @media (max-width: 768px) {
          .home-shell {
            height: auto !important;
            min-height: 100vh !important;
            overflow-x: hidden !important;
            overflow-y: auto !important;
          }

          .home-header {
            flex-direction: column !important;
            align-items: stretch !important;
            gap: 12px !important;
            padding: 16px !important;
          }

          .home-header-left,
          .home-header-right {
            width: 100% !important;
            justify-content: space-between !important;
            gap: 10px !important;
          }

          .home-context-pills {
            display: none !important;
          }

          .home-command-chip span:last-child {
            display: none !important;
          }

          .home-overlay {
            display: block !important;
            padding: 108px 16px 20px !important;
          }

          .home-story-director {
            position: static !important;
            width: 100% !important;
            margin-top: 12px !important;
          }

          .home-hero-panel {
            width: 100% !important;
            max-width: none !important;
            justify-content: flex-start !important;
            gap: 18px !important;
          }

          .home-hero-title {
            max-width: none !important;
            font-size: clamp(28px, 13vw, 54px) !important;
            line-height: 0.93 !important;
          }

          .home-hero-copy {
            max-width: none !important;
            font-size: 14px !important;
            line-height: 1.72 !important;
          }

          .home-cta-row {
            flex-direction: column !important;
          }

          .home-globe-legend {
            position: static !important;
            width: 100% !important;
            margin-top: 12px !important;
          }

          .home-stage-ring,
          .home-stage-manifesto {
            display: none !important;
          }

          .home-shell > div[aria-live="polite"] {
            display: none !important;
          }

          .home-cta-row button {
            width: 100% !important;
            justify-content: center !important;
          }

          .home-proof-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
          }

          .home-gradient-layer {
            background: var(--home-overlay-gradient-mobile) !important;
          }
        }

        @media (max-width: 480px) {
          .home-header-brand {
            padding: 9px 11px !important;
          }

          .home-header-right {
            width: auto !important;
            justify-content: flex-end !important;
          }

          .home-header-right > :first-child {
            display: none !important;
          }

          .home-header-brand > div:last-child > div:last-child {
            font-size: 12px !important;
          }

          .home-proof-grid {
            grid-template-columns: 1fr !important;
          }

        }
      `}</style>
    </div>
  );
}

export default function Home() {
  return (
    <Suspense fallback={null}>
      <AppShell />
    </Suspense>
  );
}
