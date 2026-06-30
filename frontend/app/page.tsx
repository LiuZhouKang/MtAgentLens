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
import { HomePremiumGraphHud } from '@/components/ui/home-premium-graph-hud';
import type { StageEdgeSelection, StageNodeSelection } from '@/components/ui/home-engine-stage';
import type { Home3DStoryChapter } from '@/components/ui/home-3d-story-director';
import ObservatoryHeader from '@/components/system/observatory-header';
import { resolveCommandSelectionContext } from '@/lib/command-linking';
import { buildInvestigationHref } from '@/lib/investigation-route';
import type { LargeGraphColorBy } from '@/lib/large-graph-adapter';
import { getSeverityPalette } from '@/lib/severity-palette';
import { deriveTracebackEdgeIds } from '@/lib/traceback';
import { buildTraceHref } from '@/lib/trace-route';
import { demoMetrics as demoHandoffMetrics } from '@/lib/handoff-demo-data';
import { Activity, ArrowLeft, FileSearch, Play, Radar, X, Zap } from 'lucide-react';

const CommandPalette = dynamic(() => import('@/components/ui/command-palette'), {
  ssr: false,
});

const Home3DStoryDirector = dynamic(() => import('@/components/ui/home-3d-story-director'), {
  ssr: false,
});

const HomeEngineStage = dynamic(() => import('@/components/ui/home-engine-stage'), {
  ssr: false,
});

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
  const [selectedStageNode, setSelectedStageNode] = useState<StageNodeSelection | null>(null);
  const [selectedStageEdge, setSelectedStageEdge] = useState<StageEdgeSelection | null>(null);
  const [inspectorTab, setInspectorTab] = useState<'details' | 'path' | 'investigation'>('details');
  const auditMode = searchParams.get('audit') === 'headless';
  const hasRealSession = !!store.activeRunId;
  const stageParam = searchParams.get('stage');
  const dataMode = stageParam === 'semantic' ? 'semantic' : stageParam === 'oss-demo' ? 'oss-demo' : 'oss-large-graph';
  const colorParam = searchParams.get('color');
  const colorBy = colorParam === 'agent_id' || colorParam === 'cluster_id' || colorParam === 'node_type'
    ? colorParam
    : undefined;
  const normalizedColorBy: LargeGraphColorBy = colorBy ?? 'node_type';
  const [currentColorMode, setCurrentColorMode] = useState<LargeGraphColorBy>(normalizedColorBy);
  const [showGlobeIntro, setShowGlobeIntro] = useState(!auditMode);
  const autoDemoBootedRef = useRef(false);
  const introSrc = auditMode
    ? '/globe/index.html?embed=intro&audit=headless&v=prism-intro-31'
    : '/globe/index.html?embed=intro&v=prism-intro-31';

  useEffect(() => {
    setCurrentColorMode(normalizedColorBy);
  }, [normalizedColorBy]);

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

  const setHomeQueryParam = useCallback((key: 'color' | 'stage', value?: string) => {
    const next = new URLSearchParams(searchParams.toString());
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    const query = next.toString();
    router.replace(query ? `/?${query}` : '/', { scroll: false });
  }, [router, searchParams]);

  const handleColorModeChange = useCallback((mode: LargeGraphColorBy) => {
    setCurrentColorMode(mode);
    setHomeQueryParam('color', mode);
  }, [setHomeQueryParam]);

  useEffect(() => {
    if (store.graph.nodes.length > 0) {
      postGraph(store.graph.nodes, store.graph.edges);
    }
  }, [postGraph, store.graph.edges, store.graph.nodes]);

  useEffect(() => {
    setSelectedStageNode(null);
    setSelectedStageEdge(null);
    setInspectorTab('details');
  }, [dataMode, store.graph.edges.length, store.graph.nodes.length]);

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
  const compactAlerts = alerts.slice(0, 2);
  const featuredAlert = compactAlerts[0] ?? null;
  const commandSelectionContext = useMemo(
    () =>
      resolveCommandSelectionContext({
        alerts,
        featuredAlert: selectedAlert ?? featuredAlert,
        selectedStageNode,
        selectedStageEdge,
      }),
    [alerts, featuredAlert, selectedAlert, selectedStageEdge, selectedStageNode],
  );
  const openTraceWorkbench = useCallback(() => {
    router.push(commandSelectionContext.traceHref);
  }, [commandSelectionContext.traceHref, router]);

  const openCommandSurface = useCallback(() => {
    router.push(commandSelectionContext.surfaceHref);
  }, [commandSelectionContext.surfaceHref, router]);
  const heroVisible = panelLevel === 0;
  const useMinimalGraphHud = heroVisible;
  const primaryCtaLabel = featuredAlert ? '回放运行链路' : hasRealSession ? '进入总览态势' : '查看示例场景';
  const primaryCtaAction = featuredAlert
    ? () => router.push(buildTraceHref(commandSelectionContext.alertId ?? featuredAlert.alert_id))
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
  const commandCenterStats = [
    {
      label: 'Live Signals',
      value: `${Math.max(alerts.length * 3, 12)}/m`,
      hint: hasRealSession ? '实时接入 collector' : '示例调查节拍',
      icon: Activity,
    },
    {
      label: 'Evidence Load',
      value: `${Math.max(criticalAlertCount * 2, 6)}`,
      hint: '当前案件证据物化线索',
      icon: FileSearch,
    },
  ];
  const railAlerts = compactAlerts.map((alert, index) => ({
    alert,
    tone: getSeverityPalette(alert.severity).color,
    label: index === 0 ? '主案件' : '关联线索',
  }));
  const visibleRailAlerts = railAlerts.slice(0, 1);
  const keySignals = [
    {
      label: '责任越界',
      value: `${sceneStats.boundaryCrossings}`,
      meta: '越权访问与边界突破',
      tone: 'var(--alert)',
      width: `${Math.min(100, 24 + sceneStats.boundaryCrossings * 18)}%`,
    },
    {
      label: '高优先级案件',
      value: `${criticalAlertCount || 1}`,
      meta: '高危判定与不安全执行',
      tone: 'var(--warning)',
      width: `${Math.min(100, 20 + Math.max(criticalAlertCount, 1) * 20)}%`,
    },
  ];
  const visibleKeySignals = keySignals.slice(0, 1);
  const sceneActionPills = [
    {
      label: '责任图',
      action: () => {
        setPanelLevel(0);
        store.setVisualizationMode('hero');
      },
    },
    {
      label: '调查聚焦',
      action: () => {
        if (compactAlerts[0]) {
          setSelectedAlertIdx(0);
          setPanelLevel(1);
        }
        store.setVisualizationMode('focus');
      },
    },
    {
      label: '委托扩散',
      action: () => router.push('/handoff'),
    },
  ];
  const colorModePills: Array<{ key: NonNullable<typeof colorBy> | 'node_type'; label: string; hint: string }> = [
    { key: 'node_type', label: '类型着色', hint: '按 Agent / Resource / Boundary 区分' },
    { key: 'agent_id', label: '主体着色', hint: '突出责任主体分布' },
    { key: 'cluster_id', label: '集群着色', hint: '突出聚合后的连通分量' },
  ];
  const commandDeckCards = [
    {
      kicker: '运行状态',
      title: sessionModeLabel,
      value: hasRealSession ? 'Collector Online' : 'Demo Replay',
      meta: hasRealSession ? '真实观测会话已接入' : '未接入真实会话时自动回落示例场景',
      href: hasRealSession ? '/overview' : '/traces',
    },
    {
      kicker: '主案件',
      title: featuredAlert ? featuredAlert.title : '等待案件接入',
      value: featuredAlert ? `${featuredAlert.severity} · ${featuredAlert.decision}` : 'No priority case',
      meta: featuredAlert ? featuredAlert.summary : '当前仍可以从示例运行链路进入调查模式。',
      href: featuredAlert ? buildInvestigationHref(featuredAlert.alert_id) : '/investigations',
    },
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
  const activeStageChapter = storyChapters.find((chapter) => chapter.id === activeStoryChapterId) ?? storyChapters[0];
  const minimalStageLabel = activeStageChapter?.metricLabel ?? 'overview';
  const stageLegendItems = [
    { label: '智能体', tone: 'var(--brand-primary)' },
    { label: '服务', tone: 'var(--brand-secondary)' },
    { label: '网络', tone: 'var(--info)' },
    { label: '事件', tone: 'var(--warning)' },
    { label: '边界', tone: 'var(--alert)' },
  ];
  const stageDirective = featuredAlert
    ? `${featuredAlert.primary_agent?.agent_id ?? 'Agent'} 正在进入 ${featuredAlert.reasoning ?? '高风险执行链路'}`
    : '等待主案件接入，当前仍以责任图和边界漂移作为监测主轴';
  const activeBeacon =
    activeStoryChapterId === 'authority-surface'
      ? stageBeacons[0]
      : activeStoryChapterId === 'boundary-cross'
        ? stageBeacons[1]
        : stageBeacons[1];
  const heroProofChips = [
    `${sceneStats.nodes} 个核心实体`,
    `${sceneStats.links} 条核心关系`,
    `${sceneStats.boundaryCrossings} 个越界点`,
  ];
  const heroDecisionCards = [
    {
      label: '当前裁决',
      value: featuredAlert ? featuredAlert.decision : '等待主案件',
      meta: featuredAlert ? featuredAlert.severity : sessionModeLabel,
    },
    {
      label: '下一入口',
      value: featuredAlert ? '进入主调查' : hasRealSession ? '进入总览' : '加载示例',
      meta: featuredAlert ? 'Investigation Workbench' : hasRealSession ? 'Runtime Overview' : 'Demo Scene',
    },
    {
      label: '态势摘要',
      value: `${alerts.length} 个事件 / ${graphAgentCount} 个主体`,
      meta: `${criticalAlertCount} 个高优先级 · ${sceneStats.boundaryCrossings} 个越界点`,
    },
  ];
  const focusLockFacts = featuredAlert
    ? [
        featuredAlert.primary_agent?.agent_id ?? '未知主体',
        featuredAlert.severity,
        featuredAlert.decision,
      ]
    : [sessionModeLabel, `${alerts.length} 个事件`, `${sceneStats.boundaryCrossings} 个越界点`];
  const selectedNodeFacts = selectedStageNode
    ? [
        selectedStageNode.nodeType === 'PolicyBoundary' ? '边界节点' : selectedStageNode.nodeType,
        selectedStageNode.clusterId ? `cluster ${selectedStageNode.clusterId.replace('cluster-', '#')}` : null,
        selectedStageNode.boundaryCount ? `${selectedStageNode.boundaryCount} 次 crossing` : null,
      ].filter((item): item is string => Boolean(item))
    : [];
  const selectedEdgeFacts = selectedStageEdge
    ? [
        selectedStageEdge.edgeType,
        `${selectedStageEdge.sourceType} -> ${selectedStageEdge.targetType}`,
        selectedStageEdge.count ? `${selectedStageEdge.count} 次聚合` : null,
      ].filter((item): item is string => Boolean(item))
    : [];
  const selectedPathEdgeIds = useMemo(() => {
    if (selectedStageEdge?.rawEdgeIds?.length) {
      return selectedStageEdge.rawEdgeIds;
    }
    if (selectedStageNode?.rawNodeIds?.length) {
      const rawNodeIds = new Set(selectedStageNode.rawNodeIds);
      return store.graph.edges
        .filter((edge) => rawNodeIds.has(edge.source) || rawNodeIds.has(edge.target))
        .map((edge) => edge.edge_id);
    }
    return [];
  }, [selectedStageEdge?.rawEdgeIds, selectedStageNode?.rawNodeIds, store.graph.edges]);

  useEffect(() => {
    if (!heroVisible || panelLevel !== 0) return;
    if (selectedPathEdgeIds.length) {
      highlightEdgeIds(selectedPathEdgeIds);
      return () => {
        highlightEdgeIds([]);
      };
    }
    const activeChapter = storyChapters.find((chapter) => chapter.id === activeStoryChapterId) ?? storyChapters[0];
    if (!activeChapter) return;
    highlightEdgeIds(activeChapter.edgeIds);
    return () => {
      highlightEdgeIds([]);
    };
  }, [activeStoryChapterId, heroVisible, highlightEdgeIds, panelLevel, selectedPathEdgeIds, storyChapters]);

  return (
    <div className="home-shell prism-grid-surface" style={{ position: 'relative', width: '100vw', height: '100vh', overflow: 'hidden', background: '#02050d' }}>
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
          dataMode={dataMode}
          colorBy={currentColorMode}
          focusEdgeIds={selectedPathEdgeIds}
          onReady={() => {
            setEngineStageFailed(false);
          }}
          onError={() => {
            setEngineStageFailed(true);
          }}
          onNodeSelected={(selection) => {
            setSelectedStageEdge(null);
            setSelectedStageNode(selection);
            setInspectorTab('details');
            if (selection.nodeType === 'PolicyBoundary') {
              setActiveStoryChapterId('boundary-cross');
              return;
            }
            if (selection.nodeType === 'Agent') {
              const idx = alerts.findIndex(
                (a) =>
                  a.primary_agent?.agent_id === selection.nodeId
                  || a.primary_agent?.agent_id === selection.agentId
                  || a.involved_agents?.some((ia) => ia.agent_id === selection.nodeId || ia.agent_id === selection.agentId),
              );
              if (idx >= 0) {
                setSelectedAlertIdx(idx);
              }
            }
          }}
          onEdgeSelected={(selection) => {
            setSelectedStageNode(null);
            setSelectedStageEdge(selection);
            setInspectorTab('path');
            if (selection.edgeType === 'PolicyBoundaryCrossing') {
              setActiveStoryChapterId('boundary-cross');
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

      {heroVisible && !useMinimalGraphHud && (
        <>
          <div className="home-stage-beacons" style={{ position: 'absolute', inset: 0, zIndex: 8, pointerEvents: 'none' }}>
            {[activeBeacon].map((beacon, index) => (
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

      <div className="home-header-shell">
        <ObservatoryHeader
          compact
          activeSection="command"
          rightSlot={(
            <>
              <div className="home-observatory-slot">
                <LiveIndicator />
                <span>Mission Live</span>
              </div>
              <RunSelector />
            </>
          )}
        />
      </div>

      {heroVisible && !useMinimalGraphHud ? (
        <div className="home-stage-toolbar">
          {sceneActionPills.map((item) => (
            <button
              key={item.label}
              type="button"
              className="home-stage-toolbar__pill"
              onClick={item.action}
            >
              {item.label}
            </button>
          ))}
          {colorModePills.map((item) => (
            <button
              key={item.key}
              type="button"
              className="home-stage-toolbar__pill"
              onClick={() => setHomeQueryParam('color', item.key)}
              aria-pressed={(colorBy ?? 'node_type') === item.key}
              style={{
                background: (colorBy ?? 'node_type') === item.key ? 'rgba(105, 227, 255, 0.18)' : undefined,
                borderColor: (colorBy ?? 'node_type') === item.key ? 'rgba(105, 227, 255, 0.3)' : undefined,
                color: (colorBy ?? 'node_type') === item.key ? 'rgba(226, 252, 255, 0.96)' : undefined,
              }}
              title={item.hint}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}

      {useMinimalGraphHud ? (
        <HomePremiumGraphHud
          stageLabel={minimalStageLabel}
          stats={sceneStats}
          legendItems={stageLegendItems}
          currentColorMode={currentColorMode}
          colorModeItems={colorModePills}
          inspectorTab={inspectorTab}
          onInspectorTabChange={setInspectorTab}
          selectedStageNode={selectedStageNode}
          selectedStageEdge={selectedStageEdge}
          selectedNodeFacts={selectedNodeFacts}
          selectedEdgeFacts={selectedEdgeFacts}
          activeStageTitle={activeStageChapter?.title ?? 'overview'}
          onColorModeChange={handleColorModeChange}
          onClearSelection={() => {
            setSelectedStageNode(null);
            setSelectedStageEdge(null);
          }}
          canOpenInvestigation={Boolean(commandSelectionContext.alertId)}
          onOpenInvestigation={() => {
            if (commandSelectionContext.alertId) openInvestigation(commandSelectionContext.alertId);
          }}
          onOpenTraceback={openTraceWorkbench}
          onOpenOverview={openCommandSurface}
          traceActionLabel="打开回放"
          overviewActionLabel={commandSelectionContext.surfaceLabel}
        />
      ) : (
      <div className="home-overlay home-overlay--v2">
        <section className="home-command-left" style={{ display: heroVisible ? 'grid' : 'none' }}>
          <Reveal disabled={auditMode} className="home-command-kicker" delay={0.08}>
            <span className="prism-kicker-rule">Signal Rail</span>
          </Reveal>

          <Reveal disabled={auditMode} delay={0.26}>
            <div className="home-command-metrics">
              {commandCenterStats.map((item) => (
                <div key={item.label} className="home-command-metric-card">
                  <div className="home-command-metric-card__icon">
                    <item.icon size={14} />
                  </div>
                  <div className="home-command-metric-card__body">
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                    <small>{item.hint}</small>
                  </div>
                </div>
              ))}
            </div>
          </Reveal>

          <Reveal disabled={auditMode} delay={0.3}>
            <div className="home-command-dossier prism-dossier-card prism-grid-surface">
              <div className="home-command-dossier__head">
                <span className="prism-kicker-rule">主案件摘要</span>
                <span className="home-command-dossier__mode">{sessionModeLabel}</span>
              </div>
              <div className="home-command-dossier__title prism-display">
                {featuredAlert ? featuredAlert.title : '让首页始终有一个可进入的调查主线'}
              </div>
              <p className="home-command-dossier__copy">
                {featuredAlert
                  ? featuredAlert.summary
                  : '当真实会话尚未接入时，首页会回退到示例运行，保证责任链、边界穿越与证据联动依然可见。'}
              </p>
              <div className="home-command-dossier__chips">
                {(featuredAlert
                  ? [`${featuredAlert.decision}`, featuredAlert.severity]
                  : statusCards.map((item) => `${item.label} ${item.value}`)).map((chip) => (
                  <span key={chip} className="home-command-dossier__chip">
                    {chip}
                  </span>
                ))}
              </div>
              {featuredAlert ? (
                <MagneticButton
                  motionDisabled={auditMode}
                  type="button"
                  onClick={() => openInvestigation(featuredAlert.alert_id)}
                  className="home-command-dossier__button"
                >
                  <span>
                    进入当前最高优先级案件
                  </span>
                  <ArrowLeft size={14} style={{ transform: 'rotate(180deg)' }} />
                </MagneticButton>
              ) : null}
            </div>
          </Reveal>
        </section>

        <div className="home-stage-center" style={{ display: heroVisible ? 'grid' : 'none' }}>
          <Reveal disabled={auditMode} delay={0.08}>
            <div className="home-stage-frame">
              <div className="home-stage-frame__hero">
                <div className="home-stage-frame__editorial">
                  <span className="prism-kicker-rule">责任观测图集</span>
                  <h1 className="home-stage-frame__hero-title prism-display--hero">先锁定主案件，再进入责任现场</h1>
                  <p className="home-stage-frame__hero-copy">
                    用零插桩运行事实、边界穿越与证据链，把当前最危险的一次委派、执行与越权行为压缩进同一张可裁决的三维图谱。
                  </p>
                  <div className="home-stage-frame__hero-actions">
                    <MagneticButton motionDisabled={auditMode} type="button" onClick={primaryCtaAction} className="home-command-actions__primary">
                      <Zap size={16} />
                      {primaryCtaLabel}
                    </MagneticButton>
                    <MagneticButton motionDisabled={auditMode} type="button" onClick={() => setPaletteOpen(true)} className="home-command-actions__secondary">
                      <Radar size={16} />
                      打开指挥面板
                    </MagneticButton>
                  </div>
                  <div className="home-stage-frame__proofs">
                    {heroProofChips.map((chip) => (
                      <span key={chip}>{chip}</span>
                    ))}
                  </div>
                  <div className="home-stage-frame__decision-strip">
                    {heroDecisionCards.map((item) => (
                      <div key={item.label} className="home-stage-frame__decision-card">
                        <span>{item.label}</span>
                        <strong>{item.value}</strong>
                        <small>{item.meta}</small>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="home-stage-frame__spotlight">
                  <span className="prism-kicker-rule">案件锁定</span>
                  <strong>{featuredAlert ? featuredAlert.title : '等待主案件接入'}</strong>
                  <small>{featuredAlert ? featuredAlert.summary : stageDirective}</small>
                  <div className="home-stage-frame__spotlight-chips">
                    {focusLockFacts.map((item) => (
                      <span key={item}>{item}</span>
                    ))}
                  </div>
                </div>
              </div>

              <div className="home-stage-frame__top">
                <div className="home-stage-frame__headline">
                  <span className="prism-kicker-rule">当前图谱章节</span>
                  <strong>{activeStageChapter?.title ?? '责任图总览'}</strong>
                  <small>{activeStageChapter?.summary ?? stageDirective}</small>
                </div>

                <div className="home-stage-frame__stats">
                  <div>
                    <span>核心节点</span>
                    <strong>{sceneStats.nodes}</strong>
                  </div>
                  <div>
                    <span>核心关系</span>
                    <strong>{sceneStats.links}</strong>
                  </div>
                  <div>
                    <span>越界</span>
                    <strong>{sceneStats.boundaryCrossings}</strong>
                  </div>
                </div>
              </div>

              <div className="home-stage-frame__focus-lock">
                <div className="home-stage-frame__focus-lock-head">
                  <span className="prism-kicker-rule">现在为什么值得查</span>
                  <strong>{featuredAlert ? featuredAlert.decision : '主案件待机'}</strong>
                </div>
                <p>{stageDirective}</p>
                <div className="home-stage-frame__focus-lock-actions">
                  <button type="button" onClick={primaryCtaAction} className="home-stage-frame__focus-lock-primary">
                    {primaryCtaLabel}
                  </button>
                  {featuredAlert ? (
                    <button
                      type="button"
                      onClick={() => openInvestigation(featuredAlert.alert_id)}
                      className="home-stage-frame__focus-lock-secondary"
                    >
                      进入主调查
                    </button>
                  ) : null}
                </div>
              </div>

              <div className="home-stage-frame__bottom">
                <div className="home-stage-frame__legend">
                  {stageLegendItems.map((item) => (
                    <span key={item.label}>
                      <i style={{ background: item.tone }} />
                      {item.label}
                    </span>
                  ))}
                </div>
                <div className="home-stage-frame__axis">
                  <span>视角: 3D 力导向</span>
                  <span>交互: 节点可拖拽</span>
                  <span>布局: 实时聚类</span>
                </div>
              </div>
            </div>
          </Reveal>
        </div>

        <aside className="home-command-right" style={{ display: heroVisible ? 'grid' : 'none' }}>
          <Reveal disabled={auditMode} delay={0.1}>
            <div className="home-decision-deck prism-grid-surface prism-shimmer-border">
              <div className="home-decision-deck__head">
                <div>
                  <span className="prism-kicker-rule">Decision Deck</span>
                  <strong>让首页先回答该查什么、为什么值得查</strong>
                </div>
                <span>{visibleRailAlerts.length + visibleKeySignals.length} cards</span>
              </div>

              <div className="home-decision-deck__section">
                <div className="home-decision-deck__label">调查队列</div>
                <div className="home-alert-rail__list">
                  {visibleRailAlerts.map(({ alert, tone, label }, index) => (
                    <button
                      key={alert.alert_id}
                      type="button"
                      className="home-alert-rail__item"
                      onClick={() => {
                        setSelectedAlertIdx(index);
                        setPanelLevel(1);
                      }}
                    >
                      <div className="home-alert-rail__signal" style={{ background: tone }} />
                      <div className="home-alert-rail__content">
                        <span>{label}</span>
                        <strong>{alert.title}</strong>
                        <small>{alert.summary}</small>
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              <div className="home-decision-deck__section">
                <div className="home-decision-deck__label">关键信号</div>
                <div className="home-quick-launch__list">
                  {visibleKeySignals.map((item, index) => (
                    <div key={item.label} className="home-quick-launch__signal">
                      <div className="home-quick-launch__signal-top">
                        <div className="home-quick-launch__index">0{index + 1}</div>
                        <div className="home-quick-launch__copy">
                          <span>{item.label}</span>
                          <small>{item.meta}</small>
                        </div>
                        <strong>{item.value}</strong>
                      </div>
                      <div className="home-quick-launch__signal-bar">
                        <i style={{ width: item.width, background: item.tone }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </Reveal>
        </aside>
      </div>
      )}

      {heroVisible && !useMinimalGraphHud ? (
        <div className="home-bottom-deck">
          {commandDeckCards.map((card, index) => (
            <Reveal key={card.kicker} disabled={auditMode} delay={0.16 + index * 0.04}>
              <Link href={card.href} className="home-bottom-deck__card">
                <span>{card.kicker}</span>
                <strong>{card.title}</strong>
                <em>{card.value}</em>
                <small>{card.meta}</small>
              </Link>
            </Reveal>
          ))}
        </div>
      ) : null}

      {heroVisible && !useMinimalGraphHud && (
        <Home3DStoryDirector
          chapters={storyChapters}
          activeChapterId={activeStoryChapterId}
          sceneStats={sceneStats}
          onSelectChapter={(chapter) => {
            setActiveStoryChapterId(chapter.id);
          }}
        />
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
            background:
              'radial-gradient(circle at 50% 24%, rgba(125, 211, 252, 0.12) 0%, rgba(125, 211, 252, 0) 42%), radial-gradient(circle at 50% 70%, rgba(167, 139, 250, 0.1) 0%, rgba(167, 139, 250, 0) 48%), linear-gradient(180deg, rgba(0,0,0,0.22) 0%, rgba(0,0,0,0.64) 55%, rgba(0,0,0,0.88) 100%)',
            animation: 'homeInitFade 680ms cubic-bezier(0.16, 1, 0.3, 1) both',
          }}
        >
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 12,
              padding: '20px 26px',
              borderRadius: 22,
              background: 'rgba(0, 0, 0, 0.28)',
              border: '1px solid rgba(255,255,255,0.08)',
              backdropFilter: 'blur(12px)',
              boxShadow: '0 28px 72px rgba(0,0,0,0.52)',
            }}
          >
            <div style={{ fontSize: 11, letterSpacing: '0.32em', color: 'var(--text-tertiary)' }}>MTAGENTLENS</div>
            <div
              className="prism-display"
              style={{
                margin: 0,
                fontSize: 34,
                fontWeight: 520,
                letterSpacing: '-0.02em',
                color: 'var(--text-primary)',
                lineHeight: 1.02,
                textAlign: 'center',
              }}
            >
              责任观测正在就绪
            </div>
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
              <span
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: 999,
                  background: 'radial-gradient(circle at 50% 50%, rgba(214, 245, 255, 1) 0%, rgba(133, 215, 255, 0.8) 55%, rgba(133, 215, 255, 0) 100%)',
                  boxShadow: '0 0 20px rgba(133, 215, 255, 0.75)',
                  animation: 'homeInitPulse 1.25s ease-in-out infinite',
                }}
              />
              <span style={{ fontSize: 12, letterSpacing: '0.14em', color: 'var(--text-secondary)' }}>
                Calibrating observability layer
              </span>
            </div>
          </div>
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

        @keyframes homeInitFade {
          from {
            opacity: 0;
            transform: scale(1.012);
          }
          to {
            opacity: 1;
            transform: scale(1);
          }
        }

        @keyframes homeInitPulse {
          0%, 100% {
            transform: scale(1);
            opacity: 0.65;
          }
          50% {
            transform: scale(1.55);
            opacity: 1;
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
