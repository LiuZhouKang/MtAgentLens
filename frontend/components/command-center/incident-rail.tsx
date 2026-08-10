'use client';

import Link from 'next/link';
import type { CSSProperties, ReactNode } from 'react';
import { useState, Fragment, useMemo, useEffect, useRef } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { CountUp, Magnetic } from '@waffo/ui-fluid';
import { FluidHeader, FLUID_PALETTES } from '@/components/ui/fluid-header';
import { LiquidGlass, useLiquidGlassEffects } from '@/components/system/liquid-glass';
import { AnimatedDock, type DockItemData } from '@/components/ui/animated-dock';
import { BentoCard } from '@/components/ui/bento-grid';
import { AnimatedNumber } from '@/components/motion-primitives/animated-number';
import { AnimatedList } from '@/components/ui/magic-animated-list';
import { Codicon } from '@/components/ui/codicon';
import { ResponsibilityMiniGraph } from '@/components/ui/responsibility-mini-graph';
import type { InvestigationModel } from '@/lib/investigation';
import { buildAgentHref } from '@/lib/agent-route';
import { buildInvestigationHref } from '@/lib/investigation-route';
import { buildTraceHref } from '@/lib/trace-route';
import type { ResponsibilityAlert } from '@/lib/types';
import { useAppStore } from '@/lib/store';
import RawEventBrowser from '@/components/command-center/raw-event-browser';

type AlertWithModel = {
  alert: ResponsibilityAlert;
  model: InvestigationModel | null;
};

type SelectionSummary = {
  eyebrow: string;
  title: string;
  description: string;
};

type DockTool = 'incident' | 'evidence' | 'trace';

type DockItem = {
  id: DockTool;
  label: string;
  icon: string;
  count?: number;
};

type LensTone = 'danger' | 'warning' | 'ready' | 'info';

type LensMeta = {
  eyebrow: string;
  tone: LensTone;
};

type IncidentRailProps = {
  selectedAlert: ResponsibilityAlert;
  selectedModel: InvestigationModel;
  selectionSummary: SelectionSummary;
  alertModels: AlertWithModel[];
  selectedAlertId: string;
  anchorNs: number;
  openCount: number;
  criticalCount: number;
  onSelect: (alertId: string) => void;
  formatPercent: (value: number) => string;
  formatRuntimeAge: (timestampNs: number, anchorNs: number) => string;
  severityTone: (severity: ResponsibilityAlert['severity']) => 'danger' | 'warning' | 'info';
  decisionLabel: (decision: ResponsibilityAlert['decision']) => string;
  defaultExpanded?: boolean;
  layoutMode?: 'default' | 'theater';
  customWidth?: number;
  onWidthChange?: (width: number) => void;
};

export const dockItems: DockItem[] = [
  { id: 'incident', label: '事件', icon: 'warning' },
  { id: 'evidence', label: '证据', icon: 'files' },
  { id: 'trace', label: '链路', icon: 'graph-line' },
];

const handoffEdgeTypes = new Set([
  'AgentDelegatesTo',
  'Delegation',
  'AuthorityTransfer',
  'PolicyBoundaryCrossing',
]);

const lensMeta: Record<DockTool, LensMeta> = {
  incident: { eyebrow: '事件研判', tone: 'danger' },
  evidence: { eyebrow: '证据核验', tone: 'ready' },
  trace: { eyebrow: '责任溯源', tone: 'info' },
};

function formatDuration(totalDurationMs: number) {
  if (totalDurationMs < 1000) return `${totalDurationMs}ms`;
  if (totalDurationMs < 60_000) return `${(totalDurationMs / 1000).toFixed(1)}s`;
  return `${Math.round(totalDurationMs / 60_000)}m`;
}

function severityLabel(severity: ResponsibilityAlert['severity']) {
  if (severity === 'Critical') return '严重';
  if (severity === 'Warning') return '警告';
  return '提示';
}

function buildFactRows(selectedAlert: ResponsibilityAlert, selectedModel: InvestigationModel) {
  return [
    { label: '主体', value: selectedAlert.primary_agent.agent_id },
    { label: '判定', value: selectedAlert.decision },
    { label: '权限', value: selectedAlert.authority },
    { label: '边界', value: `${selectedModel.boundaryCrossingCount} 次` },
    { label: '证据', value: `${selectedModel.evidenceRefs.length} 条` },
    { label: '时长', value: formatDuration(selectedModel.timelineDurationMs) },
  ];
}

function buildEvidenceRows(selectedModel: InvestigationModel) {
  const exactCount = selectedModel.timelineSteps.filter(
    (step) => step.evidence_level === 'L1Exact',
  ).length;
  const linkedCount = selectedModel.linkedEvidence.length;

  return [
    { label: '证据引用', value: String(selectedModel.evidenceRefs.length) },
    { label: '关联记录', value: String(linkedCount) },
    { label: 'L1 精确', value: String(exactCount) },
    { label: '规则', value: String(selectedModel.ruleCount) },
  ];
}

function buildTraceRows(selectedModel: InvestigationModel) {
  return [
    { label: '节点', value: String(selectedModel.pathNodes.length) },
    { label: '边', value: String(selectedModel.pathEdges.length) },
    { label: '边界', value: String(selectedModel.boundaryCrossingCount) },
    { label: '时长', value: formatDuration(selectedModel.timelineDurationMs) },
  ];
}

function buildLensStrip(
  activeTool: DockTool,
  selectedAlert: ResponsibilityAlert,
  selectedModel: InvestigationModel,
  formatPercent: (value: number) => string,
) {
  if (activeTool === 'incident') {
    return [
      { label: '主体', value: selectedAlert.primary_agent.agent_id },
      { label: '置信度', value: formatPercent(selectedAlert.confidence) },
      { label: '路由', value: selectedAlert.engine.classification_route || 'engine' },
    ];
  }

  if (activeTool === 'evidence') {
    const exactCount = selectedModel.timelineSteps.filter(
      (step) => step.evidence_level === 'L1Exact',
    ).length;
    return [
      { label: '引用', value: String(selectedModel.evidenceRefs.length) },
      { label: '精确', value: String(exactCount) },
      { label: '规则', value: String(selectedModel.ruleCount) },
    ];
  }

  if (activeTool === 'trace') {
    return [
      { label: '节点', value: String(selectedModel.pathNodes.length) },
      { label: '边', value: String(selectedModel.pathEdges.length) },
      { label: '时长', value: formatDuration(selectedModel.timelineDurationMs) },
    ];
  }

  return [];
}

function buildPathPreview(selectedModel: InvestigationModel) {
  const labels = selectedModel.pathNodes.map((node) => node.label);
  if (!labels.length) return selectedModel.primaryAgentId ?? '暂无活跃链路';
  const preview = labels.slice(0, 3).join(' -> ');
  return labels.length > 3 ? `${preview} -> +${labels.length - 3}` : preview;
}

function AnimatedPathPreview({
  nodes,
  fallback,
}: {
  nodes: { label: string }[];
  fallback: string;
}) {
  if (!nodes.length) {
    return <span className="text-sm font-light text-white/50">{fallback}</span>;
  }

  const displayNodes = nodes.slice(0, 3);
  const overflow = nodes.length > 3 ? nodes.length - 3 : 0;

  return (
    <div className="mt-1 flex flex-wrap items-center gap-2">
      {displayNodes.map((node, idx) => (
        <Fragment key={idx}>
          <motion.div
            initial={{ opacity: 0, scale: 0.9, filter: 'blur(4px)' }}
            animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }}
            transition={{ delay: 0.1 + idx * 0.1, type: 'spring', stiffness: 300, damping: 20 }}
            className="flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-2.5 py-1.5 shadow-[0_0_10px_rgba(255,255,255,0.02)] transition-all hover:border-white/20 hover:bg-white/[0.06]"
          >
            <Codicon name="gist-commit" size={12} className="text-white/40" />
            <span className="text-xs font-medium tracking-wide text-white/80">{node.label}</span>
          </motion.div>
          {idx < displayNodes.length - 1 && (
            <motion.div
              initial={{ opacity: 0, x: -5 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.15 + idx * 0.1 }}
            >
              <Codicon name="arrow-right" size={12} className="text-white/20" />
            </motion.div>
          )}
        </Fragment>
      ))}

      {overflow > 0 && (
        <>
          <motion.div
            initial={{ opacity: 0, x: -5 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.15 + displayNodes.length * 0.1 }}
          >
            <Codicon name="arrow-right" size={12} className="text-white/20" />
          </motion.div>
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.2 + displayNodes.length * 0.1, type: 'spring' }}
            className="flex items-center justify-center rounded-full border border-white/5 bg-white/[0.02] px-2 py-1"
          >
            <span className="text-[10px] font-bold text-white/40">+{overflow}</span>
          </motion.div>
        </>
      )}
    </div>
  );
}

function buildReactFlowData(model: InvestigationModel) {
  const nodes = model.pathNodes.map((node) => ({
    id: node.nodeId,
    label: node.label,
    nodeType: node.nodeType || 'NODE',
  }));

  const edges = model.pathEdges.map((edge) => ({
    id: edge.edge_id,
    source: edge.source,
    target: edge.target,
    edgeType: edge.edge_type,
    confidence: edge.confidence,
    evidenceRefs: edge.evidence_refs,
  }));

  return { nodes, edges };
}

function buildNextAction(selectedAlert: ResponsibilityAlert) {
  if (selectedAlert.action_hint) return selectedAlert.action_hint;
  if (selectedAlert.decision === 'Unsafe')
    return '优先打开调查视图，确认边界证据，然后安排隔离与处置。';
  if (selectedAlert.decision === 'Uncertain') return '先补强链路与证据，再决定是否升级处置。';
  return '保持当前事件受控，并持续观察是否再次出现边界活动。';
}

function evidenceStatus(selectedModel: InvestigationModel, selectedAlert: ResponsibilityAlert) {
  const exactCount = selectedModel.timelineSteps.filter(
    (step) => step.evidence_level === 'L1Exact',
  ).length;
  if (selectedModel.evidenceRefs.length >= 2 && exactCount > 0) {
    return {
      icon: 'pass-filled',
      label: '已就绪',
      detail: `${selectedModel.evidenceRefs.length} 条引用 / ${exactCount} 条 L1 精确事件`,
      tone: 'ready',
    };
  }

  if (selectedAlert.decision === 'Safe') {
    return {
      icon: 'workspace-trusted',
      label: '已受控',
      detail: `${selectedModel.evidenceRefs.length} 条引用 / 当前无活跃边界突破`,
      tone: 'contained',
    };
  }

  return {
    icon: 'warning',
    label: '待复核',
    detail: `${selectedModel.evidenceRefs.length} 条引用 / 需要补强归因`,
    tone: 'review',
  };
}

function firstBoundaryLabel(selectedModel: InvestigationModel) {
  const boundaryStep = selectedModel.timelineSteps.find(
    (step) =>
      step.event_type.toLowerCase().includes('boundary') ||
      step.resource?.toLowerCase().includes('boundary'),
  );

  if (boundaryStep?.resource) return boundaryStep.resource;

  const boundaryEdge = selectedModel.pathEdges.find(
    (edge) =>
      edge.edge_type === 'PolicyBoundaryCrossing' || edge.edge_type === 'ViolationProvenance',
  );

  return boundaryEdge?.target ?? '暂无边界锚点';
}

function riskPulse(selectedAlert: ResponsibilityAlert, selectedModel: InvestigationModel) {
  const severityBoost =
    selectedAlert.severity === 'Critical' ? 34 : selectedAlert.severity === 'Warning' ? 18 : 6;
  const decisionBoost =
    selectedAlert.decision === 'Unsafe' ? 26 : selectedAlert.decision === 'Uncertain' ? 14 : 0;
  return Math.min(
    99,
    Math.max(
      8,
      Math.round(
        selectedAlert.confidence * 36 +
          selectedModel.boundaryCrossingCount * 12 +
          selectedModel.evidenceRefs.length * 4 +
          severityBoost +
          decisionBoost,
      ),
    ),
  );
}

function buildNarrativeRailItems(
  items: { id?: string; label: string; meta?: string }[],
  maxItems = 4,
) {
  const visibleItems = items.slice(0, maxItems).map((item, index) => ({
    id: item.id ?? `${item.label}-${index}`,
    label: item.label,
    meta: item.meta,
  }));

  if (items.length > maxItems) {
    visibleItems.push({
      id: `overflow-${items.length}`,
      label: `+${items.length - maxItems}`,
      meta: '继续',
    });
  }

  return visibleItems;
}

export function buildWorkspaceAction(activeTool: DockTool, selectedAlert: ResponsibilityAlert) {
  if (activeTool === 'trace') {
    return { href: buildTraceHref(selectedAlert.alert_id), label: '打开链路' };
  }

  return { href: buildInvestigationHref(selectedAlert.alert_id), label: '打开调查' };
}

function MtAgentLensMark() {
  return (
    <span className="incident-rail__mark" aria-hidden="true">
      {/* Minimal single-lens mark — matches dock button icon style (currentColor line icon) */}
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" aria-hidden="true">
        <circle
          cx="12"
          cy="12"
          r="8"
          stroke="currentColor"
          fill="none"
          style={{ strokeWidth: 1.7 }}
          opacity="0.9"
        />
        <circle cx="12" cy="12" r="3" fill="currentColor" style={{ fill: 'currentColor' }} />
      </svg>
    </span>
  );
}

function RailSurface({
  theater,
  className,
  style,
  contentClassName,
  contentStyle,
  children,
}: {
  theater: boolean;
  className: string;
  style?: CSSProperties;
  contentClassName?: string;
  contentStyle?: CSSProperties;
  children: ReactNode;
}) {
  if (theater) {
    return (
      <div className={className} style={style}>
        <div className={contentClassName} style={contentStyle}>
          {children}
        </div>
      </div>
    );
  }

  return (
    <LiquidGlass
      macro
      className={className}
      style={style}
      contentClassName={contentClassName}
      contentStyle={contentStyle}
      engine="legacy"
    >
      {children}
    </LiquidGlass>
  );
}

function SignalList({
  items,
  emptyLabel,
  icon = 'circle-filled',
}: {
  items: { id: string; label: string; detail: string; meta?: string }[];
  emptyLabel: string;
  icon?: string;
}) {
  if (!items.length) {
    return <p className="py-2 text-xs font-light text-white/40">{emptyLabel}</p>;
  }

  return (
    <div className="relative flex w-full flex-col">
      {/* 动态脉冲连接线 */}
      <div className="absolute top-4 bottom-4 left-[11px] z-0 w-[2px] rounded-full bg-gradient-to-b from-white/10 via-white/5 to-transparent" />

      <AnimatedList delay={150}>
        {items.map((item, index) => (
          <div
            key={item.id}
            className="group relative -ml-2.5 flex cursor-default gap-4 rounded-xl p-2.5 transition-all duration-400 hover:bg-white/[0.04]"
          >
            {/* 顶级图标锚点 & 发光动效 */}
            <div className="relative z-10 flex flex-col items-center justify-start pt-1">
              <div className="relative flex h-[24px] w-[24px] items-center justify-center rounded-full border border-white/10 bg-[#141415] shadow-[0_0_12px_rgba(255,255,255,0.02)] transition-all duration-500 group-hover:border-white/30 group-hover:bg-[#1C1C1E] group-hover:shadow-[0_0_20px_rgba(255,255,255,0.15)]">
                <Codicon
                  name={icon}
                  size={12}
                  className="text-white/40 transition-all duration-500 group-hover:scale-110 group-hover:text-white/90"
                />
              </div>
              {/* 微小的连接点 */}
              {index !== items.length - 1 && (
                <div className="absolute top-[24px] bottom-[-10px] w-px bg-gradient-to-b from-white/20 to-transparent opacity-0 transition-opacity duration-500 group-hover:opacity-100" />
              )}
            </div>

            {/* 核心内容区 */}
            <div className="flex min-w-0 flex-1 flex-col gap-1 pt-1 pb-2">
              <div className="flex items-center justify-between gap-3">
                <strong className="truncate text-sm font-medium tracking-wide text-white/70 transition-colors duration-300 group-hover:text-white">
                  {item.label}
                </strong>
                {item.meta && (
                  <span className="shrink-0 rounded-md border border-white/5 bg-white/[0.02] px-2 py-0.5 text-[9px] font-medium tracking-widest text-white/40 uppercase transition-all duration-300 group-hover:border-white/20 group-hover:bg-white/[0.06] group-hover:text-white/80">
                    {item.meta}
                  </span>
                )}
              </div>
              <p className="mt-0.5 text-xs leading-relaxed font-light break-words text-white/30 transition-colors duration-300 group-hover:text-white/60">
                {item.detail}
              </p>
            </div>
          </div>
        ))}
      </AnimatedList>
    </div>
  );
}

function LensStrip({ items }: { items: { label: string; value: string }[] }) {
  return (
    <div className="incident-lens-strip" aria-label="透镜上下文">
      {items.map((item) => (
        <span key={item.label}>
          <i>{item.label}</i>
          <strong>{item.value}</strong>
        </span>
      ))}
    </div>
  );
}

function ToolbenchStatus({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <span className="incident-toolbench__status">
      <Codicon name={icon} size={13} />
      {label}
      <strong>{value}</strong>
    </span>
  );
}

function TheaterRailNav({ items }: { items: DockItemData[] }) {
  return (
    <div className="incident-rail__navlist">
      {items.map((item) => (
        <Magnetic key={item.label} strength={12}>
          <button
            type="button"
            aria-label={item.ariaLabel ?? item.label}
            title={item.ariaLabel ?? item.label}
            aria-pressed={item.selected}
            aria-expanded={item.ariaExpanded}
            aria-controls={item.controls}
            className="incident-rail__navitem"
            data-selected={item.selected ? 'true' : 'false'}
            onClick={item.onClick}
          >
            <span className="incident-rail__navicon">{item.Icon}</span>
            {item.badge ? <span className="incident-rail__navbadge">{item.badge}</span> : null}
          </button>
        </Magnetic>
      ))}
    </div>
  );
}

export default function IncidentRail({
  selectedAlert,
  selectedModel,
  selectionSummary,
  openCount,
  criticalCount,
  formatPercent,
  severityTone,
  decisionLabel,
  defaultExpanded = false,
  layoutMode = 'default',
  customWidth,
  onWidthChange,
}: IncidentRailProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const isResizable = onWidthChange != null;
  const layoutExpanded = expanded;
  const [activeTool, setActiveTool] = useState<DockTool>('incident');
  const [eventsOpen, setEventsOpen] = useState(false);
  const dbAssets = useAppStore((s) => s.dbAssets);
  const dbIntents = useAppStore((s) => s.intents);
  const dbActions = useAppStore((s) => s.actions);
  const reduceMotion = useReducedMotion();
  const isTheater = layoutMode === 'theater';
  useLiquidGlassEffects({ cursor: !isTheater, spotlight: false, reveal: false, scroll: false });
  const factRows = buildFactRows(selectedAlert, selectedModel);
  const evidenceRows = buildEvidenceRows(selectedModel);
  const traceRows = buildTraceRows(selectedModel);
  const readiness = evidenceStatus(selectedModel, selectedAlert);
  const readinessIcon = readiness.icon;
  const nextAction = buildNextAction(selectedAlert);
  const traceFlowData = useMemo(() => buildReactFlowData(selectedModel), [selectedModel]);
  const pathPreview = buildPathPreview(selectedModel);
  const severity = severityTone(selectedAlert.severity);
  const boundaryLabel = firstBoundaryLabel(selectedModel);
  const pulse = riskPulse(selectedAlert, selectedModel);
  const handoffEdges = selectedModel.pathEdges.filter((edge) =>
    handoffEdgeTypes.has(edge.edge_type),
  );
  const timelineItems = selectedModel.timelineSteps.slice(0, 5).map((step, index) => ({
    id: `${step.source_event_id ?? step.event_type}-${index}`,
    label: step.event_type,
    detail: step.description,
    meta: step.evidence_level ?? step.operation ?? undefined,
  }));
  const evidenceItems = selectedModel.evidenceRefs.slice(0, 5).map((ref, index) => {
    const evidence = selectedModel.linkedEvidence.find((item) => item.evidence_id === ref);
    return {
      id: ref,
      label: ref,
      detail: evidence
        ? `${evidence.match_level} / ${Math.round(evidence.confidence * 100)}% 置信度`
        : '已被运行时时间线或责任路径引用。',
      meta: index === 0 ? '主证据' : undefined,
    };
  });
  const handoffItems = handoffEdges.slice(0, 5).map((edge) => ({
    id: edge.edge_id,
    label: edge.edge_type,
    detail: `${edge.source} -> ${edge.target}`,
    meta: `${edge.evidence_refs.length} 条引用`,
  }));
  const exactEvidenceCount = selectedModel.timelineSteps.filter(
    (step) => step.evidence_level === 'L1Exact',
  ).length;
  const evidencePulse = Math.min(
    98,
    Math.max(
      16,
      selectedModel.evidenceRefs.length * 22 +
        selectedModel.linkedEvidence.length * 9 +
        exactEvidenceCount * 14,
    ),
  );
  const tracePulse = Math.min(
    98,
    Math.max(
      18,
      selectedModel.pathEdges.length * 12 +
        selectedModel.timelineSteps.length * 4 +
        selectedModel.boundaryCrossingCount * 14,
    ),
  );
  const incidentNarrative = buildNarrativeRailItems(
    selectedModel.pathNodes.map((node, index) => ({
      id: `${node.label}-${index}`,
      label: node.label,
      meta:
        index === 0
          ? '入口'
          : index === selectedModel.pathNodes.length - 1
            ? '目标'
            : `节点 ${index + 1}`,
    })),
  );
  const evidenceNarrative = buildNarrativeRailItems(
    selectedModel.evidenceRefs.map((ref, index) => ({
      id: ref,
      label: ref,
      meta: index === 0 ? '主证据' : '引用',
    })),
  );
  const primaryEvidence = evidenceItems[0]?.label ?? '待补强';
  const traceNarrative = buildNarrativeRailItems(
    selectedModel.pathEdges.map((edge, index) => ({
      id: edge.edge_id,
      label: edge.edge_type,
      meta: index === 0 ? '起始边' : `${Math.round(edge.confidence * 100)}%`,
    })),
  );
  const panelTransition = reduceMotion
    ? { duration: 0 }
    : { duration: 0.22, ease: [0.22, 1, 0.36, 1] as const };
  const activeLabel = dockItems.find((item) => item.id === activeTool)?.label ?? '事件';
  const inspectorTitle = `${activeLabel}工作台`;
  const activeLens = lensMeta[activeTool];
  const lensStripItems = buildLensStrip(activeTool, selectedAlert, selectedModel, formatPercent);
  const headerTitle = inspectorTitle;
  const headerSubtitle = `${activeLens.eyebrow} / ${selectedAlert.primary_agent.agent_id}`;

  const getFluidPalette = (toolId: string) => {
    switch (toolId) {
      case 'incident':
        return FLUID_PALETTES.incident;
      case 'evidence':
        return FLUID_PALETTES.evidence;
      case 'trace':
        return FLUID_PALETTES.narrative;
      default:
        return FLUID_PALETTES.incident;
    }
  };
  const activeFluidColors = getFluidPalette(activeTool);

  const decoratedDockItems = dockItems.map((item) => {
    if (item.id === 'incident') return { ...item, count: criticalCount || openCount };
    if (item.id === 'evidence') return { ...item, count: selectedModel.evidenceRefs.length };
    if (item.id === 'trace') return { ...item, count: selectedModel.pathEdges.length };
    return item;
  });
  const activeDockItem =
    decoratedDockItems.find((item) => item.id === activeTool) ?? decoratedDockItems[0];
  const activeIcon = activeDockItem.icon;
  const workspaceAction = buildWorkspaceAction(activeTool, selectedAlert);
  const effectiveWidth = customWidth;
  const theaterAsideStyle: CSSProperties | undefined = isTheater
    ? {
        position: 'relative',
        top: 'auto',
        left: 'auto',
        flexShrink: 0,
        // 展开宽度自适应视口：极窄窗口自动收缩，给主内容保底 320px，避免挤压重叠
        width: isResizable
          ? expanded
            ? `min(${effectiveWidth ?? 434}px, calc(100vw - 320px))`
            : '58px'
          : expanded
            ? 'min(612px, calc(100vw - 28px))'
            : '58px',
        height: 'auto',
        maxWidth: isResizable ? 'none' : 'min(612px, calc(100vw - 28px))',
      }
    : undefined;
  const theaterLayoutStyle: CSSProperties | undefined = isTheater
    ? {
        display: 'grid',
        // responsive content column — fills whatever width the sash gives
        gridTemplateColumns: layoutExpanded ? '58px minmax(0, 1fr)' : '58px',
        alignItems: 'start',
        gap: '8px',
        width: layoutExpanded ? '100%' : 'fit-content',
        height: 'auto',
      }
    : undefined;
  const theaterDockShellStyle: CSSProperties | undefined = isTheater
    ? {
        width: '58px',
        height: 'auto',
        minHeight: 0,
        alignSelf: 'start',
        justifySelf: 'start',
      }
    : undefined;
  const theaterDockContentStyle: CSSProperties | undefined = isTheater
    ? {
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'flex-start',
        gap: '8px',
        width: '100%',
        height: 'auto',
        minHeight: 0,
        padding: '10px 0 10px',
      }
    : undefined;
  const theaterDockStyle: CSSProperties | undefined = isTheater
    ? {
        width: '40px',
        minWidth: '40px',
        margin: 0,
        flex: '0 0 auto',
        alignSelf: 'center',
        padding: '6px 0',
        gap: '6px',
        border: 0,
        borderRadius: 0,
        background: 'transparent',
        boxShadow: 'none',
      }
    : undefined;
  const theaterDockHeadStyle: CSSProperties | undefined = isTheater
    ? {
        width: '40px',
        height: '40px',
      }
    : undefined;
  const theaterDockItemStyle: CSSProperties | undefined = isTheater
    ? {
        border: '1px solid rgba(189, 201, 221, 0.06)',
        background: '#14181d',
        color: 'rgba(181, 191, 208, 0.88)',
        boxShadow: 'none',
      }
    : undefined;
  const theaterDockItemSelectedStyle: CSSProperties | undefined = isTheater
    ? {
        border: '1px solid rgba(174, 157, 136, 0.28)',
        background: '#1a2026',
        color: 'rgba(245, 247, 251, 0.98)',
        boxShadow: '0 0 0 1px rgba(174, 157, 136, 0.08)',
      }
    : undefined;
  const theaterDockBadgeWrapStyle: CSSProperties | undefined = isTheater
    ? {
        position: 'absolute',
        top: '50%',
        right: '-6px',
        transform: 'translateY(-50%)',
        minWidth: '16px',
        height: '16px',
        padding: 0,
        border: 0,
        background: 'transparent',
        boxShadow: 'none',
        pointerEvents: 'none',
      }
    : undefined;
  const theaterDockBadgeStyle: CSSProperties | undefined = isTheater
    ? {
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        minWidth: '16px',
        height: '16px',
        padding: '0 3px',
        borderRadius: '6px',
        border: '1px solid rgba(7, 10, 17, 0.88)',
        background: '#4a443c',
        color: '#f5f7fb',
        fontSize: '10px',
        fontWeight: 600,
        lineHeight: 1,
        boxShadow: 'none',
      }
    : undefined;
  const theaterPanelShellStyle: CSSProperties | undefined = isTheater
    ? {
        width: '100%',
        height: 'auto',
        minHeight: 0,
        maxHeight: 'calc(100vh - 40px)',
        marginLeft: 0,
        paddingLeft: 0,
        alignSelf: 'start',
        justifySelf: 'start',
      }
    : undefined;
  const theaterPanelStyle: CSSProperties | undefined = isTheater
    ? {
        display: 'grid',
        gridTemplateRows: 'auto',
        width: '100%',
        height: 'auto',
        maxHeight: 'calc(100vh - 40px)',
        minHeight: 0,
        position: 'relative',
        overflow: 'hidden',
      }
    : undefined;
  const theaterPanelContentStyle: CSSProperties | undefined = isTheater
    ? ({
        height: 'auto',
        minHeight: 0,
        maxHeight: 'calc(100vh - 40px)',
        overflow: 'auto',
        display: 'flex',
        flexDirection: 'column',
        gap: '16px',
      } as React.CSSProperties)
    : ({} as React.CSSProperties);
  const theaterPanelHeroStyle: CSSProperties | undefined = isTheater
    ? {
        minHeight: '116px',
      }
    : undefined;
  const theaterPanelCommandStyle: CSSProperties | undefined = isTheater
    ? {
        minHeight: '34px',
      }
    : undefined;
  const theaterSurfaceStyle: CSSProperties | undefined = isTheater
    ? {
        minHeight: 0,
        height: 'auto',
        overflow: 'visible',
      }
    : undefined;
  const dockData: DockItemData[] = decoratedDockItems.map((item) => {
    const selected = activeTool === item.id;
    const buttonLabel =
      layoutExpanded && selected ? `收起${item.label}工作台` : `打开${item.label}工作台`;

    return {
      label: item.label,
      ariaLabel: buttonLabel,
      selected,
      ariaExpanded: layoutExpanded && selected,
      controls: 'incident-command-panel',
      onClick: () => {
        if (expanded && selected) {
          setExpanded(false);
          return;
        }

        setActiveTool(item.id);
        setExpanded(true);
      },
      Icon: <Codicon name={item.icon} size={18} />,
      badge:
        typeof item.count === 'number' ? (
          <span className="incident-rail__dock-badge" style={theaterDockBadgeStyle}>
            {item.count}
          </span>
        ) : undefined,
    };
  });

  // ── VS Code-style sash resize (floating panel) ──
  const asideRef = useRef<HTMLElement>(null);
  const sashRef = useRef<HTMLDivElement>(null);
  const resizingRef = useRef(false);
  const resizeStartXRef = useRef(0);
  const resizeStartWRef = useRef(0);

  useEffect(() => {
    const aside = asideRef.current;
    const sash = sashRef.current;
    if (!aside || !sash || !isResizable) return;

    const clampW = (w: number) => {
      const vw = window.innerWidth;
      const maxPx = Math.min(612, Math.floor(vw * 0.8));
      return Math.min(maxPx, Math.max(340, Math.round(w)));
    };

    const applyWidth = (w: number) => {
      const next = clampW(w);
      aside.style.width = `${next}px`;
      aside.style.maxWidth = 'none';
      return next;
    };

    const onPointerDown = (e: PointerEvent) => {
      e.preventDefault();
      resizingRef.current = true;
      resizeStartXRef.current = e.clientX;
      resizeStartWRef.current = aside.getBoundingClientRect().width;
      sash.setPointerCapture(e.pointerId);
    };

    const onPointerMove = (e: PointerEvent) => {
      if (!resizingRef.current) return;
      // direct DOM write — no React re-render mid-drag
      applyWidth(resizeStartWRef.current + (e.clientX - resizeStartXRef.current));
    };

    const endResize = () => {
      if (!resizingRef.current) return;
      resizingRef.current = false;
      const w = Math.round(aside.getBoundingClientRect().width);
      onWidthChange?.(w);
    };

    const onDblClick = () => {
      const def = 434;
      applyWidth(def);
      onWidthChange?.(def);
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        const cur = aside.getBoundingClientRect().width;
        const delta = e.key === 'ArrowRight' ? 20 : -20;
        const next = applyWidth(cur + delta);
        onWidthChange?.(next);
      }
    };

    sash.addEventListener('pointerdown', onPointerDown);
    sash.addEventListener('pointermove', onPointerMove);
    sash.addEventListener('pointerup', endResize);
    sash.addEventListener('pointercancel', endResize);
    sash.addEventListener('dblclick', onDblClick);
    sash.addEventListener('keydown', onKeyDown);
    return () => {
      sash.removeEventListener('pointerdown', onPointerDown);
      sash.removeEventListener('pointermove', onPointerMove);
      sash.removeEventListener('pointerup', endResize);
      sash.removeEventListener('pointercancel', endResize);
      sash.removeEventListener('dblclick', onDblClick);
      sash.removeEventListener('keydown', onKeyDown);
    };
  }, [isResizable, layoutExpanded, onWidthChange]);

  return (
    <aside
      ref={asideRef}
      className="incident-rail"
      data-expanded={layoutExpanded}
      data-layout={layoutMode}
      data-tool={activeTool}
      data-finish={isTheater ? 'control-deck-v4' : undefined}
      data-theme="apple-pro"
      aria-label="事件调查控制轨"
      style={theaterAsideStyle}
    >
      <h1 id="command-console-title" className="sr-only">
        {selectionSummary.title}
      </h1>
      {isTheater ? (
        <div className="incident-rail__ambient" aria-hidden="true" />
      ) : (
        <motion.div
          className="incident-rail__ambient"
          aria-hidden="true"
          animate={reduceMotion ? undefined : { opacity: [0.08, 0.14, 0.08] }}
          transition={
            reduceMotion ? undefined : { duration: 10, repeat: Infinity, ease: 'easeInOut' }
          }
        />
      )}

      <div className="incident-rail__layout" style={theaterLayoutStyle}>
        <RailSurface
          theater={isTheater}
          className="incident-rail__dock-shell"
          style={theaterDockShellStyle}
          contentClassName="incident-rail__dock-shell-content"
          contentStyle={theaterDockContentStyle}
        >
          <div className="incident-rail__dock-head" style={theaterDockHeadStyle}>
            <MtAgentLensMark />
          </div>
          {isTheater ? (
            <TheaterRailNav items={dockData} />
          ) : (
            <AnimatedDock
              orientation="vertical"
              className="incident-rail__dock"
              items={dockData}
              style={theaterDockStyle}
              itemStyle={theaterDockItemStyle}
              selectedItemStyle={theaterDockItemSelectedStyle}
              badgeWrapperStyle={theaterDockBadgeWrapStyle}
            />
          )}
        </RailSurface>

        <AnimatePresence initial={false}>
          {layoutExpanded ? (
            <motion.div
              id="incident-command-panel"
              className="incident-rail__panel-shell"
              data-tool={activeTool}
              style={theaterPanelShellStyle}
              initial={
                reduceMotion
                  ? { opacity: 1 }
                  : { opacity: 0, x: -10, scale: 0.992, filter: 'blur(4px)' }
              }
              animate={
                reduceMotion
                  ? { opacity: 1 }
                  : {
                      opacity: 1,
                      x: 0,
                      scale: 1,
                      filter: 'blur(0px)',
                      transitionEnd: { transform: 'none' },
                    }
              }
              exit={
                reduceMotion
                  ? { opacity: 0 }
                  : { opacity: 0, x: -8, scale: 0.996, filter: 'blur(3px)' }
              }
              transition={panelTransition}
            >
              <RailSurface
                theater={isTheater}
                className="incident-rail__panel"
                style={{ ...theaterPanelStyle, position: 'relative', zIndex: 1 }}
                contentClassName="incident-rail__panel-content"
                contentStyle={{ ...theaterPanelContentStyle, position: 'relative' }}
              >
                <div
                  className="incident-rail__panel-header"
                  style={{
                    position: 'relative',
                    overflow: 'hidden',
                    backgroundColor: 'transparent',
                    borderBottom: 'none',
                    zIndex: 1,
                  }}
                >
                  <div style={{ position: 'absolute', inset: 0, zIndex: 0, pointerEvents: 'none' }}>
                    <FluidHeader colors={activeFluidColors} />
                  </div>
                  <div
                    className="incident-rail__panel-hero"
                    style={{ ...theaterPanelHeroStyle, position: 'relative', zIndex: 1 }}
                  >
                    <div className="incident-rail__panel-title mesh-text-layer">
                      <span className="incident-rail__panel-kicker">MtAgentLens / 责任透镜</span>
                      <div className="incident-rail__panel-heading">
                        <span className="incident-rail__panel-icon" data-tone={activeLens.tone}>
                          <Codicon name={activeIcon} size={17} />
                        </span>
                        <div>
                          <strong>{headerTitle}</strong>
                          <small>{headerSubtitle}</small>
                        </div>
                      </div>
                    </div>
                    <div className="incident-rail__panel-meta mesh-text-layer">
                      <span data-tone={severity}>{severityLabel(selectedAlert.severity)}</span>
                      <span>{decisionLabel(selectedAlert.decision)}</span>
                      <span>
                        <CountUp value={pulse} />
                      </span>
                    </div>
                  </div>
                  <div
                    className="incident-rail__panel-toolbar"
                    style={{ position: 'relative', zIndex: 2 }}
                  >
                    <Magnetic strength={15}>
                      <Link
                        href={workspaceAction.href}
                        className="incident-rail__panel-command"
                        style={theaterPanelCommandStyle}
                      >
                        {workspaceAction.label}
                        <Codicon name="link-external" size={13} />
                      </Link>
                    </Magnetic>
                    <Magnetic strength={10}>
                      <button
                        type="button"
                        className="incident-rail__collapse-button"
                        aria-label="收起调查控制轨"
                        onClick={() => setExpanded(false)}
                        style={theaterPanelCommandStyle}
                      >
                        <Codicon name="chrome-close" size={15} />
                      </button>
                    </Magnetic>
                  </div>
                </div>

                {isTheater ? null : <LensStrip items={lensStripItems} />}

                <div className="incident-rail__body" style={theaterSurfaceStyle}>
                  <AnimatePresence mode="wait" initial={false}>
                    <motion.div
                      key={activeTool}
                      className="incident-inspector-view w-full"
                      style={theaterSurfaceStyle}
                      initial={
                        reduceMotion ? { opacity: 1 } : { opacity: 0, y: 10, filter: 'blur(5px)' }
                      }
                      animate={
                        reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0, filter: 'blur(0px)' }
                      }
                      exit={
                        reduceMotion ? { opacity: 0 } : { opacity: 0, y: -5, filter: 'blur(2px)' }
                      }
                      transition={{ duration: reduceMotion ? 0 : 0.2, ease: [0.22, 1, 0.36, 1] }}
                    >
                      {activeTool === 'incident' && (
                        <section
                          className="incident-panel-flow w-full"
                          aria-label="当前事件摘要"
                          style={theaterSurfaceStyle}
                        >
                          <div className="flex flex-col gap-4 pb-12">
                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex flex-wrap items-center gap-3">
                                  <ToolbenchStatus
                                    icon="warning"
                                    label="当前事件"
                                    value={severityLabel(selectedAlert.severity)}
                                  />
                                  <ToolbenchStatus
                                    icon="shield"
                                    label="判定"
                                    value={decisionLabel(selectedAlert.decision)}
                                  />
                                </div>
                                <div className="flex flex-col gap-2">
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    {selectionSummary.eyebrow}
                                  </span>
                                  <h2 className="text-2xl leading-snug font-light tracking-tight text-white/90">
                                    {selectionSummary.title}
                                  </h2>
                                  <p className="mt-1 text-sm leading-relaxed font-light text-white/50">
                                    {selectionSummary.description}
                                  </p>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center justify-between">
                                  <div className="flex flex-col gap-1">
                                    <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                      风险脉冲
                                    </span>
                                    <small className="text-xs font-light text-white/40">
                                      {selectedModel.boundaryCrossingCount} 次边界 ·{' '}
                                      {selectedModel.evidenceRefs.length} 条证据
                                    </small>
                                  </div>
                                  <div className="flex items-baseline gap-1">
                                    <strong className="text-5xl font-extralight tracking-tighter text-white/90 tabular-nums">
                                      <CountUp value={pulse} />
                                    </strong>
                                    <span className="text-lg font-light text-white/30">%</span>
                                  </div>
                                </div>
                                <div
                                  className="h-1 w-full overflow-hidden rounded-full bg-white/[0.03]"
                                  aria-hidden="true"
                                >
                                  <motion.i
                                    className="block h-full rounded-full bg-gradient-to-r from-rose-500/80 to-orange-400/80"
                                    initial={{ width: 0 }}
                                    animate={{ width: `${pulse}%` }}
                                    transition={{ delay: 0.3, duration: 0.8, ease: 'easeOut' }}
                                  />
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-4">
                                <div className="flex items-center gap-2">
                                  <Codicon name="zap" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    下一步动作
                                  </span>
                                </div>
                                <p className="text-sm leading-relaxed font-light text-white/80">
                                  {nextAction}
                                </p>
                                <div className="mt-1 flex items-center gap-3">
                                  <Link
                                    href={buildInvestigationHref(selectedAlert.alert_id)}
                                    className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white"
                                  >
                                    打开调查 <Codicon name="arrow-right" size={12} />
                                  </Link>
                                  <Link
                                    href={buildTraceHref(selectedAlert.alert_id)}
                                    className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white"
                                  >
                                    查看链路 <Codicon name="arrow-right" size={12} />
                                  </Link>
                                  <Link
                                    href={buildAgentHref(selectedAlert.primary_agent.agent_id)}
                                    className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white"
                                  >
                                    主体画像 <Codicon name="organization" size={12} />
                                  </Link>
                                  <button
                                    type="button"
                                    onClick={() => setEventsOpen(true)}
                                    className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white"
                                  >
                                    原始事件流 <Codicon name="list-unordered" size={12} />
                                  </button>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="references" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    事实快照
                                  </span>
                                </div>
                                <div className="grid grid-cols-2 gap-x-4 gap-y-5 md:grid-cols-3">
                                  {factRows.map((row) => (
                                    <div key={row.label} className="flex min-w-0 flex-col gap-1">
                                      <span className="truncate text-[10px] font-medium tracking-widest text-white/30 uppercase">
                                        {row.label}
                                      </span>
                                      <span
                                        className="truncate text-base font-light tracking-tight text-white/80 tabular-nums"
                                        title={String(row.value)}
                                      >
                                        {row.value}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="shield" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    边界锚点 & 责任路径
                                  </span>
                                </div>
                                <div className="flex flex-col gap-5">
                                  <div className="flex min-w-0 flex-col gap-2">
                                    <span className="text-[9px] tracking-[0.2em] text-white/20 uppercase">
                                      ANCHOR
                                    </span>
                                    <strong className="text-sm leading-relaxed font-light tracking-wide break-words text-white/80">
                                      {boundaryLabel}
                                    </strong>
                                    <p className="text-xs font-light text-white/40">
                                      第一处可归责边界，用于快速定位。
                                    </p>
                                  </div>
                                  <div className="h-px w-full bg-white/5" />
                                  <div className="flex min-w-0 flex-col gap-2">
                                    <span className="text-[9px] tracking-[0.2em] text-white/20 uppercase">
                                      PATH PREVIEW
                                    </span>
                                    <AnimatedPathPreview
                                      nodes={selectedModel.pathNodes}
                                      fallback={pathPreview}
                                    />
                                    <p className="mt-1 text-xs font-light text-white/40">
                                      主因果链已聚焦到当前事件。
                                    </p>
                                  </div>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="graph-line" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    归责链
                                  </span>
                                </div>
                                <div className="flex flex-col gap-2">
                                  {incidentNarrative.map((item, idx) => (
                                    <motion.div
                                      key={item.id}
                                      className="group/label flex items-center justify-between rounded-lg border border-white/5 bg-white/[0.01] px-4 py-3 transition-all hover:border-white/20 hover:bg-white/[0.04]"
                                      initial={{ opacity: 0, y: 8 }}
                                      animate={{ opacity: 1, y: 0 }}
                                      transition={{
                                        delay: 0.3 + idx * 0.06,
                                        type: 'spring',
                                        stiffness: 200,
                                        damping: 20,
                                      }}
                                    >
                                      <span className="truncate pr-4 text-sm font-light tracking-wide text-white/80 group-hover/label:text-white">
                                        {item.label}
                                      </span>
                                      <span className="shrink-0 text-[9px] font-medium tracking-widest text-white/30 uppercase">
                                        {item.meta}
                                      </span>
                                    </motion.div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>
                          </div>
                        </section>
                      )}

                      {activeTool === 'evidence' && (
                        <section
                          className="incident-panel-flow w-full"
                          aria-label="证据工作台"
                          style={theaterSurfaceStyle}
                        >
                          <div className="flex flex-col gap-4 pb-12">
                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex flex-wrap items-center gap-3">
                                  <ToolbenchStatus
                                    icon={readinessIcon}
                                    label="证据就绪"
                                    value={readiness.label}
                                  />
                                  <ToolbenchStatus
                                    icon="references"
                                    label="引用"
                                    value={`${selectedModel.evidenceRefs.length} 条`}
                                  />
                                </div>
                                <div className="flex flex-col gap-2">
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    {selectionSummary.eyebrow}
                                  </span>
                                  <h2 className="text-2xl leading-snug font-light tracking-tight text-white/90">
                                    {selectionSummary.title}
                                  </h2>
                                  <p className="mt-1 text-sm leading-relaxed font-light text-white/50">
                                    {selectionSummary.description}
                                  </p>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center justify-between">
                                  <div className="flex flex-col gap-1">
                                    <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                      落证完成度
                                    </span>
                                    <small className="text-xs font-light text-white/40">
                                      {exactEvidenceCount} 条 L1 精确 ·{' '}
                                      {selectedModel.linkedEvidence.length} 条关联
                                    </small>
                                  </div>
                                  <div className="flex items-baseline gap-1">
                                    <strong className="text-5xl font-extralight tracking-tighter text-white/90 tabular-nums">
                                      <AnimatedNumber value={evidencePulse} />
                                    </strong>
                                    <span className="text-lg font-light text-white/30">%</span>
                                  </div>
                                </div>
                                <div
                                  className="h-1 w-full overflow-hidden rounded-full bg-white/[0.03]"
                                  aria-hidden="true"
                                >
                                  <motion.i
                                    className="block h-full rounded-full bg-gradient-to-r from-emerald-500/80 to-teal-400/80"
                                    initial={{ width: 0 }}
                                    animate={{ width: `${evidencePulse}%` }}
                                    transition={{ delay: 0.3, duration: 0.8, ease: 'easeOut' }}
                                  />
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-4">
                                <div className="flex items-center gap-2">
                                  <Codicon
                                    name="sparkle-filled"
                                    size={14}
                                    className="text-white/30"
                                  />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    核心落证句
                                  </span>
                                </div>
                                <p className="text-sm leading-relaxed font-light text-white/80">
                                  {primaryEvidence === '待补强'
                                    ? '当前仍缺少主证据。'
                                    : `${primaryEvidence} 正在托住结论。`}
                                </p>
                                <div className="mt-1 flex items-center gap-3">
                                  <Link
                                    href={buildInvestigationHref(selectedAlert.alert_id)}
                                    className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white"
                                  >
                                    打开调查 <Codicon name="arrow-right" size={12} />
                                  </Link>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="references" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    核心指标
                                  </span>
                                </div>
                                <div className="grid grid-cols-2 gap-x-4 gap-y-5 md:grid-cols-4">
                                  {evidenceRows.slice(0, 4).map((row) => (
                                    <div key={row.label} className="flex min-w-0 flex-col gap-1">
                                      <span className="truncate text-[10px] font-medium tracking-widest text-white/30 uppercase">
                                        {row.label}
                                      </span>
                                      <span
                                        className="truncate text-base font-light tracking-tight text-white/80 tabular-nums"
                                        title={String(row.value)}
                                      >
                                        {row.value}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="references" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    落证结构
                                  </span>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                  {evidenceNarrative.map((item) => (
                                    <span
                                      key={item.id}
                                      className="rounded-md border border-white/5 bg-white/[0.02] px-3 py-1.5 text-xs font-light text-white/70"
                                    >
                                      {item.label}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="search" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    证据清单
                                  </span>
                                </div>
                                <div className="flex flex-col divide-y divide-white/[0.03]">
                                  {evidenceItems.map((item, idx) => (
                                    <motion.div
                                      key={item.id}
                                      className="group/item flex flex-col gap-2 py-4 first:pt-0 last:pb-0"
                                      initial={{ opacity: 0, y: 10 }}
                                      animate={{ opacity: 1, y: 0 }}
                                      transition={{
                                        delay: 0.4 + idx * 0.08,
                                        type: 'spring',
                                        stiffness: 200,
                                        damping: 20,
                                      }}
                                    >
                                      <div className="flex items-center justify-between gap-4">
                                        <span className="text-sm font-light tracking-wide break-words text-white/80 group-hover/item:text-white">
                                          {item.label}
                                        </span>
                                        {item.meta ? (
                                          <span className="shrink-0 rounded-full border border-white/5 bg-white/[0.02] px-2 py-0.5 text-[9px] font-medium tracking-widest text-white/50 uppercase">
                                            {item.meta}
                                          </span>
                                        ) : null}
                                      </div>
                                      <span className="text-xs leading-relaxed font-light text-white/40">
                                        {item.detail}
                                      </span>
                                    </motion.div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>
                          </div>
                        </section>
                      )}

                      {activeTool === 'trace' && (
                        <section
                          className="incident-panel-flow w-full"
                          aria-label="链路工作台"
                          style={theaterSurfaceStyle}
                        >
                          <div className="flex flex-col gap-4 pb-12">
                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex flex-wrap items-center gap-3">
                                  <ToolbenchStatus
                                    icon="graph-line"
                                    label="责任路径"
                                    value={`${selectedModel.pathEdges.length} 边`}
                                  />
                                  <ToolbenchStatus
                                    icon="history"
                                    label="时间序列"
                                    value={`${selectedModel.timelineSteps.length} 步`}
                                  />
                                </div>
                                <div className="flex flex-col gap-2">
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    链路工作台
                                  </span>
                                  <h2 className="text-2xl leading-snug font-light tracking-tight text-white/90">
                                    把越界动作压回一条可解释的责任链
                                  </h2>
                                  <p className="mt-1 text-sm leading-relaxed font-light text-white/50">
                                    {selectionSummary.description}
                                  </p>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center justify-between">
                                  <div className="flex flex-col gap-1">
                                    <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                      链路覆盖度
                                    </span>
                                    <small className="text-xs font-light text-white/40">
                                      {selectedModel.pathNodes.length} 节点 ·{' '}
                                      {selectedModel.timelineSteps.length} 步
                                    </small>
                                  </div>
                                  <div className="flex items-baseline gap-1">
                                    <strong className="text-5xl font-extralight tracking-tighter text-white/90 tabular-nums">
                                      <CountUp value={tracePulse} />
                                    </strong>
                                    <span className="text-lg font-light text-white/30">%</span>
                                  </div>
                                </div>
                                <div
                                  className="h-1 w-full overflow-hidden rounded-full bg-white/[0.03]"
                                  aria-hidden="true"
                                >
                                  <motion.i
                                    className="block h-full rounded-full bg-gradient-to-r from-blue-500/80 to-indigo-400/80"
                                    initial={{ width: 0 }}
                                    animate={{ width: `${tracePulse}%` }}
                                    transition={{ delay: 0.3, duration: 0.8, ease: 'easeOut' }}
                                  />
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-4">
                                <div className="flex items-center gap-2">
                                  <Codicon name="zap" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    链路动作
                                  </span>
                                </div>
                                <p className="text-sm leading-relaxed font-light text-white/80">
                                  优先核对第一条边界穿越，再确认委派边和污染传播边是否一致。
                                </p>
                                <div className="mt-1 flex items-center gap-3">
                                  <Link
                                    href={buildTraceHref(selectedAlert.alert_id)}
                                    className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white"
                                  >
                                    打开链路 <Codicon name="arrow-right" size={12} />
                                  </Link>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="graph-line" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    核心指标
                                  </span>
                                </div>
                                <div className="grid grid-cols-2 gap-x-4 gap-y-5 md:grid-cols-4">
                                  {traceRows.slice(0, 4).map((row) => (
                                    <div key={row.label} className="flex min-w-0 flex-col gap-1">
                                      <span className="truncate text-[10px] font-medium tracking-widest text-white/30 uppercase">
                                        {row.label}
                                      </span>
                                      <span
                                        className="truncate text-base font-light tracking-tight text-white/80 tabular-nums"
                                        title={String(row.value)}
                                      >
                                        {row.value}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="git-commit" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    链路骨架
                                  </span>
                                </div>
                                <div className="flex flex-col gap-2">
                                  {traceNarrative.map((item, idx) => (
                                    <motion.div
                                      key={item.id}
                                      className="group/label flex items-center justify-between rounded-lg border border-white/5 bg-white/[0.01] px-4 py-3 transition-all hover:border-white/20 hover:bg-white/[0.04]"
                                      initial={{ opacity: 0, y: 8 }}
                                      animate={{ opacity: 1, y: 0 }}
                                      transition={{
                                        delay: 0.3 + idx * 0.06,
                                        type: 'spring',
                                        stiffness: 200,
                                        damping: 20,
                                      }}
                                    >
                                      <span className="truncate pr-4 text-sm font-light tracking-wide text-white/80 group-hover/label:text-white">
                                        {item.label}
                                      </span>
                                      <span className="shrink-0 text-[9px] font-medium tracking-widest text-white/30 uppercase">
                                        {item.meta}
                                      </span>
                                    </motion.div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon
                                    name="list-selection"
                                    size={14}
                                    className="text-white/30"
                                  />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    链路与时间序列
                                  </span>
                                </div>
                                <div className="flex flex-col gap-2">
                                  <span className="text-[9px] tracking-[0.2em] text-white/20 uppercase">
                                    PATH PREVIEW
                                  </span>
                                  <AnimatedPathPreview
                                    nodes={selectedModel.pathNodes}
                                    fallback={pathPreview}
                                  />
                                </div>
                                <div className="my-2 h-px w-full bg-white/5" />
                                <div className="relative flex w-full flex-col gap-6">
                                  <div className="flex w-full min-w-0 flex-col gap-3">
                                    <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                      交互式责任网络
                                    </span>
                                    <ResponsibilityMiniGraph
                                      nodes={traceFlowData.nodes}
                                      edges={traceFlowData.edges}
                                      alertId={selectedAlert.alert_id}
                                    />
                                  </div>
                                  <div className="mt-2 flex w-full min-w-0 flex-col gap-3">
                                    <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                      事件追踪流
                                    </span>
                                    <SignalList
                                      items={timelineItems}
                                      emptyLabel="当前事件暂无运行时时间线步骤。"
                                      icon="history"
                                    />
                                  </div>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon
                                    name="git-pull-request"
                                    size={14}
                                    className="text-white/30"
                                  />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">
                                    委派与边界转移
                                  </span>
                                </div>
                                <div className="flex flex-col gap-2">
                                  <div className="flex items-center justify-between">
                                    <span className="text-[9px] tracking-[0.2em] text-white/20 uppercase">
                                      委派边
                                    </span>
                                    <span className="text-[9px] font-medium tracking-widest text-white/30 uppercase">
                                      {handoffEdges.length} 条
                                    </span>
                                  </div>
                                  <SignalList
                                    items={handoffItems}
                                    emptyLabel="当前路径没有挂接委派或权限转移边。"
                                    icon="git-pull-request"
                                  />
                                </div>
                              </div>
                            </BentoCard>
                          </div>
                        </section>
                      )}
                    </motion.div>
                  </AnimatePresence>
                </div>
              </RailSurface>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>

      {/* Sash resize handle — only when expanded */}
      {isResizable && layoutExpanded && (
        <div
          ref={sashRef}
          className="incident-rail__sash"
          role="separator"
          aria-orientation="vertical"
          aria-label="拖拽调整侧边栏宽度，双击重置"
          tabIndex={0}
          style={{ touchAction: 'none' }}
        />
      )}

      {/* 原始事件流覆盖层 drawer——保留 3-dock 隐喻，不改 dock 项 */}
      <AnimatePresence>
        {eventsOpen && (
          <RawEventBrowser
            processEvents={dbAssets?.processEvents ?? []}
            tlsSessions={dbAssets?.tlsSessions ?? []}
            intents={dbIntents}
            actions={dbActions}
            onClose={() => setEventsOpen(false)}
          />
        )}
      </AnimatePresence>
    </aside>
  );
}
