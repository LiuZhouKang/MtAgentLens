'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  Activity,
  AlertTriangle,
  Braces,
  ChevronRight,
  CheckCircle2,
  Clock3,
  FileSearch,
  GitBranch,
  Layers3,
  Network,
  ShieldAlert,
  Sparkles,
  TerminalSquare,
} from 'lucide-react';
import ObservatoryHeader from '@/components/system/observatory-header';
import PrismButton from '@/components/ui/prism-button';
import PrismPanel from '@/components/ui/prism-panel';
import { demoAlerts, demoEvidence } from '@/lib/demo-data';
import { buildInvestigationHref } from '@/lib/investigation-route';
import { buildTraceRunModel, type TraceEventKind, type TraceRunModel } from '@/lib/trace-workbench';

const kindIcon: Record<TraceEventKind, typeof Activity> = {
  agent: GitBranch,
  llm: Sparkles,
  tool: TerminalSquare,
  file: FileSearch,
  network: Network,
  boundary: ShieldAlert,
  verdict: CheckCircle2,
};

const kindLabel: Record<TraceEventKind, string> = {
  agent: 'Agent',
  llm: 'LLM',
  tool: 'Tool',
  file: 'File',
  network: 'Network',
  boundary: 'Boundary',
  verdict: 'Verdict',
};

const filterOrder: Array<TraceEventKind | 'all'> = [
  'all',
  'agent',
  'file',
  'tool',
  'network',
  'boundary',
  'llm',
  'verdict',
];

function formatDuration(ms: number) {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function severityTone(severity: TraceRunModel['severity'] | 'Normal') {
  if (severity === 'Critical') return 'danger';
  if (severity === 'Warning') return 'warning';
  if (severity === 'Info') return 'info';
  return 'success';
}

function filterLabel(filter: TraceEventKind | 'all') {
  if (filter === 'all') return 'All';
  return kindLabel[filter];
}

function severityBaseScore(severity: TraceRunModel['severity']) {
  if (severity === 'Critical') return 78;
  if (severity === 'Warning') return 56;
  return 24;
}

function pickDefaultEventId(events: TraceRunModel['timeline']) {
  const preferredEvent =
    events.find((event) => event.severity === 'Critical' && event.evidenceRef) ??
    events.find((event) => event.severity !== 'Normal' && event.evidenceRef) ??
    events.find((event) => event.kind === 'boundary') ??
    events.find((event) => event.evidenceRef) ??
    events[0];

  return preferredEvent?.id ?? '';
}

export default function TracesPageClient() {
  const runs = useMemo(
    () => demoAlerts.map((alert) => buildTraceRunModel(alert, demoEvidence)),
    [],
  );
  const [activeRunId, setActiveRunId] = useState(runs[0]?.id ?? '');
  const [activeFilter, setActiveFilter] = useState<TraceEventKind | 'all'>('all');
  const activeRun = runs.find((run) => run.id === activeRunId) ?? runs[0];
  const filteredTimeline = useMemo(
    () => activeFilter === 'all'
      ? activeRun.timeline
      : activeRun.timeline.filter((event) => event.kind === activeFilter),
    [activeFilter, activeRun.timeline],
  );
  const [selectedEventId, setSelectedEventId] = useState(pickDefaultEventId(activeRun.timeline));

  useEffect(() => {
    setActiveFilter('all');
    setSelectedEventId(pickDefaultEventId(activeRun.timeline));
  }, [activeRun.id, activeRun.timeline]);

  useEffect(() => {
    if (!filteredTimeline.length) {
      setSelectedEventId('');
      return;
    }

    if (!filteredTimeline.some((event) => event.id === selectedEventId)) {
      setSelectedEventId(pickDefaultEventId(filteredTimeline));
    }
  }, [filteredTimeline, selectedEventId]);

  const selectedEvent = filteredTimeline.find((event) => event.id === selectedEventId) ?? filteredTimeline[0] ?? null;
  const highRiskEvents = activeRun.timeline.filter((event) => event.severity !== 'Normal');
  const evidenceEvents = activeRun.timeline.filter((event) => event.evidenceRef);
  const maxOffset = Math.max(...activeRun.timeline.map((event) => event.offsetMs), 1);
  const riskScore = Math.min(
    99,
    severityBaseScore(activeRun.severity) + activeRun.boundaryEvents * 8 + highRiskEvents.length * 3,
  );
  const traceControlStats = [
    {
      label: 'Signal Density',
      value: `${(activeRun.timeline.length / Math.max(activeRun.durationMs / 1000, 1)).toFixed(1)}/s`,
      delta: `${activeRun.timeline.length} steps`,
    },
    {
      label: 'Evidence Load',
      value: `${Math.round((evidenceEvents.length / Math.max(activeRun.timeline.length, 1)) * 100)}%`,
      delta: `${evidenceEvents.length} linked`,
    },
    {
      label: 'Boundary Hits',
      value: `${activeRun.boundaryEvents}`,
      delta: `${highRiskEvents.length} high risk`,
    },
    {
      label: 'Trace Confidence',
      value: `${Math.round(activeRun.confidence * 100)}%`,
      delta: activeRun.severity === 'Critical' ? 'requires review' : 'stable',
    },
  ];
  const riskDrivers = [
    {
      label: '授权漂移',
      value: `${Math.min(96, 28 + activeRun.boundaryEvents * 22)}%`,
      tone: activeRun.boundaryEvents > 0 ? 'high' : 'low',
    },
    {
      label: '证据连续性',
      value: `${Math.min(94, 36 + evidenceEvents.length * 11)}%`,
      tone: evidenceEvents.length >= 2 ? 'medium' : 'low',
    },
    {
      label: '执行身份偏差',
      value: `${Math.min(92, 22 + highRiskEvents.length * 18)}%`,
      tone: highRiskEvents.length >= 2 ? 'high' : 'medium',
    },
    {
      label: '边界压力',
      value: `${Math.round((activeRun.boundaryEvents / Math.max(activeRun.timeline.length, 1)) * 100)}%`,
      tone: activeRun.boundaryEvents > 0 ? 'high' : 'low',
    },
  ];
  const selectedEvidenceEvents = selectedEvent?.evidenceRef
    ? evidenceEvents.filter((event) => event.evidenceRef === selectedEvent.evidenceRef)
    : evidenceEvents;
  const relatedRuleIds = Array.from(
    new Set(
      selectedEvent?.ruleIds?.length
        ? selectedEvent.ruleIds
        : selectedEvent?.evidenceRef
          ? selectedEvidenceEvents.flatMap((event) => event.ruleIds)
          : [],
    ),
  );
  const relatedPatternIds = Array.from(
    new Set(
      selectedEvent?.patternId
        ? [selectedEvent.patternId]
        : selectedEvent?.evidenceRef
          ? selectedEvidenceEvents.flatMap((event) => event.patternId ? [event.patternId] : [])
          : [],
    ),
  );

  return (
    <div className="trace-page prism-grid-surface">
      <div className="trace-page__inner">
        <ObservatoryHeader
          eyebrow="Trace Workbench"
          title="把一次 Agent 运行读成可定位、可追责的证据链"
          description="从运行队列进入单次 trace，按时间看 agent、工具、文件、边界和 verdict 的推进关系。风险不再散在日志里，而是落到具体事件、材料和责任主体上。"
          activeSection="traces"
          backHref="/"
          backLabel="返回首页"
          stats={[
            `${runs.length} demo runs`,
            `${filteredTimeline.length}/${activeRun.timeline.length} timeline events`,
            `${activeRun.evidenceCount} evidence links`,
          ]}
        />

        <section className="trace-control-strip" aria-label="Trace command metrics">
          <div className="trace-control-strip__title">
            <span>Trace Control Strip</span>
            <strong>{activeRun.primaryAgent}</strong>
          </div>
          <div className="trace-control-strip__grid">
            {traceControlStats.map((item) => (
              <div key={item.label} className="trace-strip-card">
                <span>{item.label}</span>
                <strong>{item.value}</strong>
                <small>{item.delta}</small>
              </div>
            ))}
          </div>
          <div className="trace-control-strip__window">
            <span>Window</span>
            <strong>Last {formatDuration(activeRun.durationMs)}</strong>
            <small>{activeRun.severity === 'Critical' ? 'Live escalation' : 'Replay aligned'}</small>
          </div>
        </section>

        <section className="trace-hero">
          <PrismPanel className="trace-hero__summary" size="hero" grid shimmer>
            <div className="trace-hero__kicker">
              <span className="trace-live-dot" />
              Active Trace
            </div>
            <div className="trace-hero__copy">
              <h2 className="trace-hero__title">{activeRun.title}</h2>
              <p>{activeRun.subtitle}</p>
            </div>

            <div className="trace-scoreboard" aria-label="Trace key metrics">
              <div>
                <span>Decision</span>
                <strong>{activeRun.decision}</strong>
              </div>
              <div>
                <span>Duration</span>
                <strong>{formatDuration(activeRun.durationMs)}</strong>
              </div>
              <div>
                <span>Confidence</span>
                <strong>{Math.round(activeRun.confidence * 100)}%</strong>
              </div>
              <div>
                <span>Boundary</span>
                <strong>{activeRun.boundaryEvents}</strong>
              </div>
            </div>

            <div className="trace-lens-grid" aria-label="Trace operational lenses">
              <div className="trace-lens-card">
                <span>Primary Agent</span>
                <strong>{activeRun.primaryAgent}</strong>
                <small>当前运行的责任锚点</small>
              </div>
              <div className="trace-lens-card">
                <span>Event Classes</span>
                <strong>{Object.values(activeRun.kindCounts).filter((count) => count > 0).length}</strong>
                <small>时间线上可切换的事件类型</small>
              </div>
              <div className="trace-lens-card">
                <span>Evidence Pressure</span>
                <strong>{evidenceEvents.length}</strong>
                <small>带证据引用的关键事件</small>
              </div>
            </div>
          </PrismPanel>

          <PrismPanel className="trace-verdict-card" size="lg" grid>
            <div className="trace-verdict-card__head">
              <AlertTriangle size={18} />
              <span>{activeRun.severity} Verdict</span>
            </div>
            <h3>{activeRun.verdict.headline}</h3>
            <p>{activeRun.verdict.body}</p>
            <div className="trace-material-lock">
              <FileSearch size={16} />
              <span>{activeRun.verdict.evidence}</span>
            </div>
          </PrismPanel>
        </section>

        <section className="trace-workbench">
          <PrismPanel className="trace-run-list" size="lg">
            <div className="trace-section-head">
              <span>Live Trace Feed</span>
              <strong>{runs.length}</strong>
            </div>
            <div className="trace-run-list__summary">
              <div>
                <span>Active trace</span>
                <strong>{activeRun.id}</strong>
              </div>
              <div>
                <span>Pressure</span>
                <strong>{riskScore}/100</strong>
              </div>
            </div>
            <div className="trace-run-list__items">
              {runs.map((run) => (
                <button
                  key={run.id}
                  className="trace-run-row"
                  data-active={run.id === activeRun.id}
                  type="button"
                  onClick={() => setActiveRunId(run.id)}
                >
                  <span className="trace-run-row__tone" data-tone={severityTone(run.severity)} />
                  <span className="trace-run-row__body">
                    <span className="trace-run-row__meta">
                      <em data-tone={severityTone(run.severity)}>
                        {run.id === activeRun.id ? 'Active' : run.severity === 'Info' ? 'Completed' : 'Queued'}
                      </em>
                      <i>Risk {Math.min(99, severityBaseScore(run.severity) + run.boundaryEvents * 8)}</i>
                    </span>
                    <strong>{run.title}</strong>
                    <small>{run.primaryAgent} · {formatDuration(run.durationMs)} · {run.evidenceCount} evidence</small>
                  </span>
                </button>
              ))}
            </div>

            <div className="trace-case-actions">
              <Link href={buildInvestigationHref(activeRun.id)}>
                <PrismButton variant="primary" size="sm">
                  <ShieldAlert size={14} />
                  打开调查
                </PrismButton>
              </Link>
              <Link href="/overview">
                <PrismButton variant="secondary" size="sm">回到总览</PrismButton>
              </Link>
            </div>
          </PrismPanel>

          <PrismPanel className="trace-timeline-panel" size="lg" grid shimmer>
            <div className="trace-section-head">
              <span>Event Timeline</span>
              <strong>{activeFilter === 'all' ? formatDuration(maxOffset) : `${filterLabel(activeFilter)} Lens`}</strong>
            </div>
            <div className="trace-timeline-panel__intro">
              <p>按时间回放一次运行里谁发起、谁执行、谁触达资源，以及哪一步把风险真正推过边界。</p>
              <div className="trace-timeline-panel__legend">
                <span data-tone="success">Normal</span>
                <span data-tone="warning">Escalating</span>
                <span data-tone="danger">Boundary</span>
              </div>
            </div>

            <div className="trace-filter-row" role="toolbar" aria-label="Trace event filters">
              {filterOrder.map((filter) => {
                const count = filter === 'all'
                  ? activeRun.timeline.length
                  : activeRun.kindCounts[filter];

                if (count === 0 && filter !== 'all') {
                  return null;
                }

                return (
                  <button
                    key={filter}
                    type="button"
                    className="trace-filter-chip"
                    data-active={filter === activeFilter}
                    onClick={() => setActiveFilter(filter)}
                    aria-pressed={filter === activeFilter}
                    aria-label={`${filterLabel(filter)} ${count}`}
                  >
                    <span>{filterLabel(filter)}</span>
                    <strong>{count}</strong>
                  </button>
                );
              })}
            </div>

            <div className="trace-pressure-rail" aria-hidden="true">
              {filteredTimeline.map((event) => (
                <span
                  key={event.id}
                  data-tone={severityTone(event.severity)}
                  style={{ left: `${Math.min(100, (event.offsetMs / maxOffset) * 100)}%` }}
                />
              ))}
            </div>

            <div className="trace-timeline">
              {filteredTimeline.map((event) => {
                const Icon = kindIcon[event.kind];
                return (
                  <button
                    key={event.id}
                    type="button"
                    className="trace-event"
                    data-tone={severityTone(event.severity)}
                    data-selected={event.id === selectedEvent?.id}
                    onClick={() => setSelectedEventId(event.id)}
                    aria-label={event.title}
                  >
                    <div className="trace-event__time">
                      <Clock3 size={13} />
                      T+{formatDuration(event.offsetMs)}
                    </div>
                    <div className="trace-event__node">
                      <Icon size={16} />
                    </div>
                    <div className="trace-event__body">
                      <div className="trace-event__meta">
                        <span>{kindLabel[event.kind]}</span>
                        <span>{event.actor}</span>
                        <span>{Math.round(event.confidence * 100)}%</span>
                      </div>
                      <h3>{event.title}</h3>
                      <p>{event.material}</p>
                    </div>
                  </button>
                );
              })}
            </div>
          </PrismPanel>

          <aside className="trace-evidence-stack">
            <PrismPanel size="lg" className="trace-evidence-panel trace-risk-board">
              <div className="trace-section-head">
                <span>Risk Verdict Board</span>
                <strong>{activeRun.severity}</strong>
              </div>
              <div className="trace-risk-board__hero">
                <div className="trace-risk-board__score">
                  <span>Risk Score</span>
                  <strong>{riskScore}</strong>
                  <small>/100</small>
                </div>
                <div className="trace-risk-board__copy">
                  <h3>{activeRun.verdict.headline}</h3>
                  <p>{activeRun.verdict.body}</p>
                </div>
              </div>
              <div className="trace-risk-board__drivers">
                <span>Key Risk Drivers</span>
                <div className="trace-risk-driver-list">
                  {riskDrivers.map((driver) => (
                    <div key={driver.label} className="trace-risk-driver">
                      <div className="trace-risk-driver__label">
                        <strong>{driver.label}</strong>
                        <em data-tone={driver.tone}>{driver.tone === 'high' ? 'High' : driver.tone === 'medium' ? 'Medium' : 'Low'}</em>
                      </div>
                      <div className="trace-risk-driver__rail">
                        <span style={{ width: driver.value }} data-tone={driver.tone} />
                      </div>
                      <small>{driver.value}</small>
                    </div>
                  ))}
                </div>
              </div>
            </PrismPanel>

            <PrismPanel size="lg" className="trace-evidence-panel">
              <div className="trace-section-head">
                <span>Selected Event</span>
                <strong>{selectedEvent ? kindLabel[selectedEvent.kind] : 'Idle'}</strong>
              </div>
              {selectedEvent ? (
                <div className="trace-selected-event">
                  <div className="trace-selected-event__title">
                    <Layers3 size={16} />
                    <strong>{selectedEvent.title}</strong>
                  </div>
                  <div className="trace-selected-event__meta">
                    <span>{selectedEvent.actor}</span>
                    <span>{selectedEvent.operation ?? 'No operation tag'}</span>
                    <span>{selectedEvent.evidenceLevel ?? 'Derived evidence'}</span>
                  </div>
                  <div className="trace-selected-event__material">{selectedEvent.material}</div>
                  <div className="trace-selected-event__rules">
                    <div>
                      <span>Rules</span>
                      <strong>{relatedRuleIds[0] ?? 'No direct rule'}</strong>
                    </div>
                    <div>
                      <span>Pattern</span>
                      <strong>{relatedPatternIds[0] ?? 'No pattern'}</strong>
                    </div>
                  </div>
                  {selectedEvent.evidenceRef ? (
                    <div className="trace-selected-event__evidence">
                      <span>Evidence Ref</span>
                      <strong>{selectedEvent.evidenceRef}</strong>
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="trace-risk-copy">
                  <Braces size={18} />
                  <p>当前筛选器下没有可选事件。</p>
                </div>
              )}
            </PrismPanel>

            <PrismPanel size="lg" className="trace-evidence-panel">
              <div className="trace-section-head">
                <span>Evidence Waterfall</span>
                <strong>{selectedEvidenceEvents.length}</strong>
              </div>
                <div className="trace-evidence-list">
                  {selectedEvidenceEvents.map((event) => (
                    <div key={event.id} className="trace-evidence-item">
                    <span>{event.evidenceRef}</span>
                    <strong>{event.material}</strong>
                    <small>{event.actor} · {kindLabel[event.kind]}</small>
                  </div>
                ))}
              </div>
            </PrismPanel>

            <PrismPanel size="lg" className="trace-evidence-panel trace-evidence-panel--risk">
              <div className="trace-section-head">
                <span>Escalation Focus</span>
                <strong>{highRiskEvents.length}</strong>
              </div>
              <div className="trace-risk-copy">
                <Braces size={18} />
                <p>
                  {highRiskEvents[0]
                    ? `${highRiskEvents[0].actor} 在 ${highRiskEvents[0].material} 上触发最高优先级事件，当前 trace 应优先验证它的授权来源和执行身份。`
                    : '当前 trace 没有边界级风险，适合继续查看工具参数和上下文流向。'}
                </p>
              </div>
              <div className="trace-risk-list">
                {highRiskEvents.slice(0, 3).map((event) => (
                  <button
                    key={event.id}
                    type="button"
                    className="trace-risk-row"
                    onClick={() => {
                      setActiveFilter(event.kind);
                      setSelectedEventId(event.id);
                    }}
                  >
                    <span>{event.actor}</span>
                    <strong>{event.title}</strong>
                    <ChevronRight size={14} />
                  </button>
                ))}
              </div>
            </PrismPanel>
          </aside>
        </section>
      </div>
    </div>
  );
}
