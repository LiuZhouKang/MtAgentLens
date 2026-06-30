'use client';

import { Activity, ArrowUpRight, FileSearch, Gauge, GitBranch, Radar, ShieldAlert, Sparkles, Waypoints, X } from 'lucide-react';
import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import { AnimatedBackground } from '@/components/motion-primitives/animated-background';
import { AnimatedNumber } from '@/components/motion-primitives/animated-number';
import { GlowEffect } from '@/components/motion-primitives/glow-effect';
import { Spotlight } from '@/components/motion-primitives/spotlight';
import { TransitionPanel } from '@/components/motion-primitives/transition-panel';
import type { StageEdgeSelection, StageNodeSelection } from '@/components/ui/home-engine-stage';
import type { LargeGraphColorBy } from '@/lib/large-graph-adapter';
import { cn } from '@/lib/utils';

type InspectorTab = 'details' | 'path' | 'investigation';

type PremiumGraphHudProps = {
  stageLabel: string;
  stats: {
    nodes: number;
    links: number;
    boundaryCrossings: number;
  };
  legendItems: Array<{ label: string; tone: string }>;
  currentColorMode: LargeGraphColorBy;
  colorModeItems: Array<{ key: LargeGraphColorBy; label: string; hint: string }>;
  inspectorTab: InspectorTab;
  onInspectorTabChange: (value: InspectorTab) => void;
  selectedStageNode: StageNodeSelection | null;
  selectedStageEdge: StageEdgeSelection | null;
  selectedNodeFacts: string[];
  selectedEdgeFacts: string[];
  activeStageTitle: string;
  onColorModeChange: (mode: LargeGraphColorBy) => void;
  onClearSelection: () => void;
  canOpenInvestigation: boolean;
  onOpenInvestigation: () => void;
  onOpenTraceback: () => void;
  onOpenOverview: () => void;
  traceActionLabel?: string;
  overviewActionLabel?: string;
};

function PanelShell({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        'relative overflow-hidden rounded-[30px] border border-cyan-200/10 bg-[linear-gradient(180deg,rgba(7,13,26,0.94)_0%,rgba(4,8,15,0.82)_52%,rgba(3,6,12,0.96)_100%)] p-4 shadow-[0_24px_70px_rgba(0,0,0,0.5),inset_0_1px_0_rgba(255,255,255,0.06)] backdrop-blur-2xl',
        className,
      )}
    >
      <GlowEffect
        colors={['rgba(34,211,238,0.18)', 'rgba(96,165,250,0.14)', 'rgba(168,85,247,0.14)']}
        mode="breathe"
        blur="stronger"
        duration={7}
        className="opacity-60"
      />
      <Spotlight size={280} className="from-cyan-200/10 via-cyan-100/4 to-transparent" />
      <motion.div
        className="pointer-events-none absolute inset-x-6 top-0 h-px bg-gradient-to-r from-transparent via-cyan-200/90 to-transparent"
        animate={{ opacity: [0.35, 0.9, 0.35], scaleX: [0.82, 1, 0.82] }}
        transition={{ duration: 4.6, repeat: Infinity, ease: 'easeInOut' }}
      />
      <motion.div
        className="pointer-events-none absolute -right-20 top-[-72px] h-44 w-44 rounded-full bg-cyan-400/12 blur-3xl"
        animate={{ opacity: [0.22, 0.44, 0.22], scale: [0.92, 1.12, 0.92] }}
        transition={{ duration: 6.8, repeat: Infinity, ease: 'easeInOut' }}
      />
      <motion.div
        className="pointer-events-none absolute inset-x-0 top-0 h-28 bg-[linear-gradient(180deg,rgba(75,194,255,0.12),transparent)]"
        animate={{ opacity: [0.14, 0.3, 0.14] }}
        transition={{ duration: 4.2, repeat: Infinity, ease: 'easeInOut' }}
      />
      <motion.div
        className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-[linear-gradient(180deg,transparent,rgba(255,255,255,0.045),transparent)]"
        animate={{ y: ['-55%', '75%'] }}
        transition={{ duration: 9, repeat: Infinity, ease: 'linear' }}
      />
      <div className="relative z-10">{children}</div>
    </div>
  );
}

function SectionEyebrow({
  icon: Icon,
  label,
  meta,
}: {
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
  meta?: string;
}) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <div className="inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.24em] text-cyan-100/50">
        <Icon size={12} className="text-cyan-200/70" />
        {label}
      </div>
      {meta ? <div className="text-[11px] uppercase tracking-[0.18em] text-white/34">{meta}</div> : null}
    </div>
  );
}

function SignalRow({
  label,
  value,
  hint,
  width,
  tone,
}: {
  label: string;
  value: number;
  hint: string;
  width: string;
  tone: string;
}) {
  return (
    <div className="rounded-[22px] border border-white/8 bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02))] px-3 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-[0.2em] text-white/36">{label}</div>
          <div className="mt-1 text-[11px] text-white/52">{hint}</div>
        </div>
        <AnimatedNumber
          value={value}
          className="text-xl font-semibold tracking-tight text-white"
          springOptions={{ bounce: 0, duration: 0.6 }}
        />
      </div>
      <div className="mt-3 h-1.5 rounded-full bg-white/6">
        <motion.div
          className="h-full rounded-full"
          style={{
            width,
            background: `linear-gradient(90deg, ${tone}, rgba(255,255,255,0.96))`,
            boxShadow: `0 0 18px color-mix(in srgb, ${tone} 28%, transparent)`,
          }}
          animate={{ opacity: [0.65, 1, 0.65] }}
          transition={{ duration: 3.2, repeat: Infinity, ease: 'easeInOut' }}
        />
      </div>
    </div>
  );
}

function SelectionChip({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-full border border-white/8 bg-white/[0.045] px-3 py-1.5 text-xs text-white/76 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      {children}
    </span>
  );
}

const inspectorTabs = [
  { id: 'details', label: 'Details', icon: Radar },
  { id: 'path', label: 'Path', icon: GitBranch },
  { id: 'investigation', label: 'Actions', icon: FileSearch },
] as const;

export function HomePremiumGraphHud({
  stageLabel,
  stats,
  legendItems,
  currentColorMode,
  colorModeItems,
  inspectorTab,
  onInspectorTabChange,
  selectedStageNode,
  selectedStageEdge,
  selectedNodeFacts,
  selectedEdgeFacts,
  activeStageTitle,
  onColorModeChange,
  onClearSelection,
  canOpenInvestigation,
  onOpenInvestigation,
  onOpenTraceback,
  onOpenOverview,
  traceActionLabel = 'Trace Workbench',
  overviewActionLabel = 'Overview',
}: PremiumGraphHudProps) {
  const selectedTitle = selectedStageEdge
    ? `${selectedStageEdge.sourceLabel} -> ${selectedStageEdge.targetLabel}`
    : selectedStageNode?.label ?? 'Overview';
  const selectedSubtitle = selectedStageEdge
    ? `${selectedStageEdge.edgeType} · ${selectedStageEdge.sourceType} -> ${selectedStageEdge.targetType}`
    : selectedStageNode?.description ?? 'Select a node or path to inspect the current graph state.';
  const inspectorIndex = inspectorTabs.findIndex((item) => item.id === inspectorTab);
  const factItems = (selectedStageEdge ? selectedEdgeFacts : selectedNodeFacts).slice(0, 6);
  const focusUnits = selectedStageEdge?.rawEdgeIds?.length
    ?? selectedStageNode?.rawNodeIds?.length
    ?? 0;
  const boundaryDensity = Math.max(12, Math.min(96, Math.round((stats.boundaryCrossings / Math.max(stats.links, 1)) * 240)));
  const selectionType = selectedStageEdge ? 'edge' : selectedStageNode ? 'node' : 'scene';
  const stageSignalRows = [
    {
      label: 'nodes',
      value: stats.nodes,
      hint: 'active graph objects',
      width: `${Math.max(24, Math.min(100, 26 + stats.nodes * 7))}%`,
      tone: '#67e8f9',
    },
    {
      label: 'links',
      value: stats.links,
      hint: 'causal and execution lines',
      width: `${Math.max(24, Math.min(100, 20 + stats.links * 4))}%`,
      tone: '#8b9dff',
    },
    {
      label: 'boundary',
      value: stats.boundaryCrossings,
      hint: 'policy and resource crossings',
      width: `${Math.max(20, Math.min(100, 22 + stats.boundaryCrossings * 15))}%`,
      tone: '#7dd3fc',
    },
  ];

  return (
    <div className="pointer-events-none absolute inset-0 z-[9]">
      <div className="absolute left-6 top-[104px] grid w-[min(372px,calc(100vw-48px))] gap-4">
        <PanelShell className="pointer-events-auto">
          <div className="mb-4 flex items-start justify-between gap-3">
            <div>
              <div className="inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.24em] text-cyan-100/52">
                <Sparkles size={12} className="text-cyan-200/70" />
                Command Matrix
              </div>
              <div className="mt-2 text-sm font-medium text-white/90">{stageLabel}</div>
            </div>
            <div className="inline-flex items-center gap-2 rounded-full border border-emerald-400/24 bg-emerald-400/10 px-3 py-1 text-[11px] font-medium text-emerald-100 shadow-[0_0_24px_rgba(16,185,129,0.16)]">
              <Activity size={12} />
              Live
            </div>
          </div>

          <div className="relative overflow-hidden rounded-[24px] border border-cyan-200/10 bg-[radial-gradient(circle_at_20%_50%,rgba(34,211,238,0.18),transparent_30%),linear-gradient(135deg,rgba(255,255,255,0.05),rgba(255,255,255,0.02))] p-4">
            <div className="absolute inset-y-4 left-4 right-[44%]">
              <div className="relative h-full min-h-[146px]">
                <motion.div
                  className="absolute left-1/2 top-1/2 h-28 w-28 -translate-x-1/2 -translate-y-1/2 rounded-full border border-cyan-200/25"
                  animate={{ scale: [0.92, 1.1, 0.92], opacity: [0.72, 0.28, 0.72] }}
                  transition={{ duration: 3.8, repeat: Infinity, ease: 'easeInOut' }}
                />
                <motion.div
                  className="absolute left-1/2 top-1/2 h-20 w-20 -translate-x-1/2 -translate-y-1/2 rounded-full border border-cyan-100/30"
                  animate={{ rotate: [0, 360] }}
                  transition={{ duration: 14, repeat: Infinity, ease: 'linear' }}
                />
                <div className="absolute left-1/2 top-1/2 h-14 w-14 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(circle,rgba(125,211,252,0.92),rgba(56,189,248,0.22)_55%,transparent_70%)] blur-[1px]" />
                <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
                  <div className="text-[10px] uppercase tracking-[0.22em] text-cyan-50/48">Core Risk</div>
                  <AnimatedNumber
                    value={stats.boundaryCrossings}
                    className="mt-2 text-4xl font-semibold tracking-tight text-white"
                    springOptions={{ bounce: 0, duration: 0.7 }}
                  />
                  <div className="mt-2 text-[11px] text-white/52">policy crossings</div>
                </div>
              </div>
            </div>

            <div className="ml-[42%] grid gap-2">
              {stageSignalRows.map((item) => (
                <SignalRow key={item.label} {...item} />
              ))}
            </div>
          </div>

          <div className="mt-4 grid grid-cols-3 gap-2.5">
            {[
              { label: 'density', value: boundaryDensity, suffix: '%' },
              { label: 'focus', value: focusUnits, suffix: '' },
              { label: 'stage', value: 1, suffix: '' },
            ].map((item) => (
              <div key={item.label} className="rounded-[20px] border border-white/8 bg-white/[0.035] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
                <div className="text-[11px] uppercase tracking-[0.2em] text-white/34">{item.label}</div>
                <div className="mt-2 flex items-end gap-1 text-white">
                  <AnimatedNumber value={item.value} className="text-2xl font-semibold tracking-tight" />
                  <span className="pb-1 text-[11px] uppercase tracking-[0.18em] text-white/36">{item.suffix || activeStageTitle.split(' ')[0]}</span>
                </div>
              </div>
            ))}
          </div>
        </PanelShell>

        <PanelShell className="pointer-events-auto">
          <SectionEyebrow icon={Waypoints} label="Legend" meta={currentColorMode} />
          <div className="grid gap-2.5">
            {legendItems.map((item, index) => (
              <div
                key={item.label}
                className="relative overflow-hidden rounded-[22px] border border-white/8 bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02))] px-3 py-3"
              >
                <motion.div
                  className="absolute inset-y-2 left-2 w-1 rounded-full"
                  style={{ background: item.tone }}
                  animate={{ opacity: [0.4, 1, 0.4] }}
                  transition={{ duration: 2.6 + index * 0.4, repeat: Infinity, ease: 'easeInOut' }}
                />
                <div className="ml-3 flex items-center justify-between gap-3">
                  <div>
                    <div className="inline-flex items-center gap-2 text-sm text-white/84">
                      <i className="h-2.5 w-2.5 rounded-full" style={{ background: item.tone, boxShadow: `0 0 14px ${item.tone}` }} />
                      {item.label}
                    </div>
                    <div className="mt-1 text-[11px] uppercase tracking-[0.18em] text-white/34">active spectral lane</div>
                  </div>
                  <div className="w-24">
                    <div className="h-1.5 rounded-full bg-white/6">
                      <motion.div
                        className="h-full rounded-full"
                        style={{
                          width: `${62 + index * 10}%`,
                          background: `linear-gradient(90deg, ${item.tone}, rgba(255,255,255,0.95))`,
                        }}
                        animate={{ opacity: [0.6, 1, 0.6] }}
                        transition={{ duration: 2.4 + index * 0.35, repeat: Infinity, ease: 'easeInOut' }}
                      />
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </PanelShell>
      </div>

      <div className="absolute right-6 top-[104px] grid w-[min(400px,calc(100vw-48px))] gap-4">
        <PanelShell className="pointer-events-auto">
          <div className="mb-4 flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.24em] text-cyan-100/52">
                <Radar size={12} className="text-cyan-200/70" />
                Inspector
              </div>
              <div className="mt-2 truncate text-lg font-semibold tracking-tight text-white">{selectedTitle}</div>
              <div className="mt-1 text-sm leading-6 text-white/48">{selectedSubtitle}</div>
            </div>
            {(selectedStageNode || selectedStageEdge) ? (
              <button
                type="button"
                aria-label="清除当前选择"
                onClick={onClearSelection}
                className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-white/10 bg-white/[0.045] text-white/60 transition hover:border-cyan-200/24 hover:text-white"
              >
                <X size={14} />
              </button>
            ) : null}
          </div>

          <div className="mb-4 flex flex-wrap gap-2">
            <SelectionChip>{selectionType}</SelectionChip>
            <SelectionChip>{currentColorMode}</SelectionChip>
            <SelectionChip>{activeStageTitle}</SelectionChip>
            <SelectionChip>{focusUnits ? `${focusUnits} focus` : 'scene focus'}</SelectionChip>
          </div>

          <div className="rounded-[24px] border border-white/8 bg-white/[0.03] p-1">
            <AnimatedBackground
              value={inspectorTab}
              defaultValue={inspectorTab}
              onValueChange={(value) => value && onInspectorTabChange(value as InspectorTab)}
              className="rounded-[20px] border border-cyan-300/22 bg-[linear-gradient(180deg,rgba(34,211,238,0.2)_0%,rgba(56,189,248,0.08)_100%)] shadow-[0_12px_34px_rgba(14,165,233,0.14)]"
            >
              {inspectorTabs.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    key={item.id}
                    data-id={item.id}
                    type="button"
                    className="inline-flex w-full items-center justify-center gap-2 rounded-[18px] px-3 py-2.5 text-xs font-medium text-white/62 transition hover:text-white data-[state=active]:text-white"
                  >
                    <Icon size={14} />
                    {item.label}
                  </button>
                );
              })}
            </AnimatedBackground>
          </div>

          <TransitionPanel
            activeIndex={Math.max(inspectorIndex, 0)}
            transition={{ duration: 0.28, ease: 'easeOut' }}
            variants={{
              enter: { opacity: 0, y: 10, filter: 'blur(8px)' },
              center: { opacity: 1, y: 0, filter: 'blur(0px)' },
              exit: { opacity: 0, y: -8, filter: 'blur(8px)' },
            }}
            className="mt-4 min-h-[280px]"
          >
            {[
              <div key="details" className="grid gap-3">
                <div className="grid grid-cols-3 gap-2.5">
                  <div className="rounded-[20px] border border-white/8 bg-white/[0.035] p-3">
                    <div className="text-[11px] uppercase tracking-[0.18em] text-white/34">mode</div>
                    <div className="mt-2 text-sm font-medium text-white">{currentColorMode}</div>
                  </div>
                  <div className="rounded-[20px] border border-white/8 bg-white/[0.035] p-3">
                    <div className="text-[11px] uppercase tracking-[0.18em] text-white/34">focus</div>
                    <div className="mt-2 text-sm font-medium text-white">{selectionType}</div>
                  </div>
                  <div className="rounded-[20px] border border-white/8 bg-white/[0.035] p-3">
                    <div className="text-[11px] uppercase tracking-[0.18em] text-white/34">units</div>
                    <div className="mt-2 text-sm font-medium text-white">{focusUnits || 'scene'}</div>
                  </div>
                </div>

                <div className="flex flex-wrap gap-2">
                  {(factItems.length ? factItems : ['selection', currentColorMode, activeStageTitle]).map((item) => (
                    <SelectionChip key={item}>{item}</SelectionChip>
                  ))}
                </div>

                {selectedStageNode?.boundaryKey || selectedStageEdge?.boundaryKey ? (
                  <div className="rounded-[22px] border border-cyan-300/14 bg-cyan-300/[0.05] px-4 py-3 text-xs leading-6 text-cyan-50/80">
                    {selectedStageNode?.boundaryKey ?? selectedStageEdge?.boundaryKey}
                  </div>
                ) : (
                  <div className="rounded-[22px] border border-white/8 bg-white/[0.03] px-4 py-3 text-xs leading-6 text-white/56">
                    Current stage stays fully interactive. Tap a node or crossing path to lock the inspector on a concrete object.
                  </div>
                )}
              </div>,
              <div key="path" className="grid gap-3">
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-[22px] border border-white/8 bg-white/[0.035] p-3">
                    <div className="text-[11px] uppercase tracking-[0.18em] text-white/34">nodes</div>
                    <AnimatedNumber value={stats.nodes} className="mt-2 text-2xl font-semibold tracking-tight text-white" />
                  </div>
                  <div className="rounded-[22px] border border-white/8 bg-white/[0.035] p-3">
                    <div className="text-[11px] uppercase tracking-[0.18em] text-white/34">links</div>
                    <AnimatedNumber value={stats.links} className="mt-2 text-2xl font-semibold tracking-tight text-white" />
                  </div>
                </div>
                <div className="rounded-[22px] border border-white/8 bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02))] p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-[11px] uppercase tracking-[0.18em] text-white/34">route</div>
                    <div className="text-[11px] uppercase tracking-[0.18em] text-cyan-100/54">{focusUnits || 1} active</div>
                  </div>
                  <div className="mt-3 text-sm leading-6 text-white/86">
                    {selectedStageEdge
                      ? `${selectedStageEdge.sourceLabel} -> ${selectedStageEdge.targetLabel}`
                      : selectedStageNode?.label ?? activeStageTitle}
                  </div>
                  <div className="mt-4 h-1.5 rounded-full bg-white/6">
                    <motion.div
                      className="h-full rounded-full bg-[linear-gradient(90deg,#7dd3fc,#e0f2fe)]"
                      style={{ width: `${Math.max(18, Math.min(100, 28 + focusUnits * 14))}%` }}
                      animate={{ opacity: [0.65, 1, 0.65] }}
                      transition={{ duration: 2.8, repeat: Infinity, ease: 'easeInOut' }}
                    />
                  </div>
                </div>
              </div>,
              <div key="investigation" className="grid gap-3">
                <button
                  type="button"
                  onClick={onOpenInvestigation}
                  disabled={!canOpenInvestigation}
                  className="inline-flex items-center justify-between rounded-[22px] border border-cyan-300/24 bg-[linear-gradient(180deg,rgba(34,211,238,0.16),rgba(56,189,248,0.08))] px-4 py-3 text-sm font-medium text-cyan-50 transition hover:border-cyan-200/32 hover:bg-cyan-300/14 disabled:cursor-default disabled:border-white/8 disabled:bg-white/[0.03] disabled:text-white/34"
                >
                  打开调查
                  <ArrowUpRight size={14} />
                </button>
                <button
                  type="button"
                  onClick={onOpenTraceback}
                  className="inline-flex items-center justify-between rounded-[22px] border border-white/8 bg-white/[0.04] px-4 py-3 text-sm font-medium text-white/84 transition hover:border-white/12 hover:bg-white/[0.06]"
                >
                  {traceActionLabel}
                  <GitBranch size={14} />
                </button>
                <button
                  type="button"
                  onClick={onOpenOverview}
                  className="inline-flex items-center justify-between rounded-[22px] border border-white/8 bg-white/[0.04] px-4 py-3 text-sm font-medium text-white/84 transition hover:border-white/12 hover:bg-white/[0.06]"
                >
                  {overviewActionLabel}
                  <ShieldAlert size={14} />
                </button>
              </div>,
            ]}
          </TransitionPanel>
        </PanelShell>

        <PanelShell className="pointer-events-auto">
          <SectionEyebrow icon={Gauge} label="View Rail" meta="graph" />

          <div className="grid gap-3">
            <div className="rounded-[24px] border border-white/8 bg-white/[0.03] p-1">
              <AnimatedBackground
                value={currentColorMode}
                defaultValue={currentColorMode}
                onValueChange={(value) => value && onColorModeChange(value as LargeGraphColorBy)}
                className="rounded-[20px] border border-cyan-300/22 bg-[linear-gradient(180deg,rgba(34,211,238,0.2)_0%,rgba(56,189,248,0.08)_100%)] shadow-[0_12px_34px_rgba(14,165,233,0.14)]"
              >
                {colorModeItems.map((item) => (
                  <button
                    key={item.key}
                    data-id={item.key}
                    type="button"
                    className="group grid w-full gap-1 rounded-[18px] px-3 py-3 text-left transition data-[state=active]:text-white"
                  >
                    <span className="text-sm font-medium text-white/78 transition group-data-[state=active]:text-white">
                      {item.label}
                    </span>
                    <span className="text-[11px] uppercase tracking-[0.16em] text-white/42 transition group-data-[state=active]:text-white/62">
                      {item.hint}
                    </span>
                  </button>
                ))}
              </AnimatedBackground>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-[22px] border border-white/8 bg-white/[0.035] p-3">
                <div className="inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-white/36">
                  <Activity size={12} className="text-cyan-200/70" />
                  stage
                </div>
                <div className="mt-2 text-sm font-medium text-white">{activeStageTitle}</div>
              </div>
              <div className="rounded-[22px] border border-white/8 bg-white/[0.035] p-3">
                <div className="inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-white/36">
                  <Sparkles size={12} className="text-cyan-200/70" />
                  selection
                </div>
                <div className="mt-2 text-sm font-medium text-white">{selectionType}</div>
              </div>
            </div>

            <div className="rounded-[22px] border border-white/8 bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02))] p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div className="text-[11px] uppercase tracking-[0.18em] text-white/34">signal rail</div>
                <div className="text-[11px] uppercase tracking-[0.18em] text-cyan-100/52">{currentColorMode}</div>
              </div>
              <div className="grid gap-3">
                {[
                  { label: 'graph load', value: stats.nodes + stats.links, max: Math.max(1, stats.nodes + stats.links + 4), tone: '#67e8f9' },
                  { label: 'boundary pressure', value: stats.boundaryCrossings, max: Math.max(1, stats.links), tone: '#a5b4fc' },
                ].map((item) => (
                  <div key={item.label}>
                    <div className="mb-1 flex items-center justify-between gap-3 text-[11px] uppercase tracking-[0.18em] text-white/34">
                      <span>{item.label}</span>
                      <span>{item.value}</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-white/6">
                      <motion.div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.max(16, Math.min(100, Math.round((item.value / item.max) * 100)))}%`,
                          background: `linear-gradient(90deg, ${item.tone}, rgba(255,255,255,0.94))`,
                        }}
                        animate={{ opacity: [0.62, 1, 0.62] }}
                        transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </PanelShell>
      </div>
    </div>
  );
}
