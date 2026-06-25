'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, ArrowRight, Cpu, Database, FileText, Network, ShieldAlert } from 'lucide-react';
import PrismPanel from '@/components/ui/prism-panel';
import PrismButton from '@/components/ui/prism-button';
import AgentResponsibilityAtlas from '@/components/ui/agent-responsibility-atlas';
import ObservatoryHeader from '@/components/system/observatory-header';
import { api } from '@/lib/api';
import { useAppStore } from '@/lib/store';
import { demoEvidence } from '@/lib/demo-data';
import { deriveAgentIdsFromAlertsAndGraph } from '@/lib/stellar-directory';
import { buildAgentModel } from '@/lib/agent-model';
import { buildAgentHref, parseAgentId } from '@/lib/agent-route';
import { buildClusterHref } from '@/lib/cluster-route';
import { buildInvestigationHref } from '@/lib/investigation-route';

function getSeverityTone(severity: string | null) {
  if (severity === 'Critical') return 'danger';
  if (severity === 'Warning') return 'warning';
  return 'info';
}

function formatConfidence(value: number) {
  return `${Math.round(value * 100)}%`;
}

export default function AgentsPageClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const activeRunId = useAppStore((state) => state.activeRunId);
  const alerts = useAppStore((state) => state.alerts);
  const graph = useAppStore((state) => state.graph);
  const [hashAgentId, setHashAgentId] = useState<string | null>(null);

  useEffect(() => {
    const syncHashAgent = () => {
      setHashAgentId(parseAgentId(new URLSearchParams(), window.location.hash));
    };

    syncHashAgent();
    window.addEventListener('hashchange', syncHashAgent);
    return () => window.removeEventListener('hashchange', syncHashAgent);
  }, []);

  const evidenceQuery = useQuery({
    queryKey: ['run-evidence', activeRunId],
    enabled: !!activeRunId,
    retry: false,
    queryFn: async () => {
      if (!activeRunId) {
        return {
          evidence: [],
          intents: [],
          actions: [],
          non_links: [],
        };
      }

      return api.getEvidence(activeRunId);
    },
  });

  const evidenceItems = useMemo(
    () => (activeRunId ? evidenceQuery.data?.evidence ?? [] : demoEvidence),
    [activeRunId, evidenceQuery.data?.evidence],
  );

  const agentIds = useMemo(() => deriveAgentIdsFromAlertsAndGraph(alerts, graph), [alerts, graph]);
  const selectedAgentId = useMemo(() => {
    const parsed = parseAgentId(searchParams, hashAgentId ? `#agentId=${encodeURIComponent(hashAgentId)}` : null);
    return parsed ?? agentIds[0] ?? null;
  }, [agentIds, hashAgentId, searchParams]);

  const agentModels = useMemo(
    () => agentIds.map((agentId) => buildAgentModel(agentId, graph, alerts, evidenceItems)),
    [agentIds, alerts, evidenceItems, graph],
  );

  const selectedModel = useMemo(
    () => agentModels.find((model) => model.agentId === selectedAgentId) ?? agentModels[0] ?? null,
    [agentModels, selectedAgentId],
  );
  const selectedResourceClassCount = selectedModel
    ? Object.values(selectedModel.resourceCounts).filter((count) => count > 0).length
    : 0;
  const selectedRiskSurfaceCount = selectedModel
    ? selectedModel.resourceCounts.files + selectedModel.resourceCounts.networks + selectedModel.resourceCounts.boundaries
    : 0;
  const totalEvidenceCount = agentModels.reduce((sum, model) => sum + model.linkedEvidence.length, 0);
  const criticalAgentCount = agentModels.filter((model) => model.highestSeverity === 'Critical').length;

  const handleSelectAgent = useCallback((agentId: string) => {
    const nextHref = buildAgentHref(agentId);
    setHashAgentId(agentId);

    if (typeof window !== 'undefined') {
      window.history.replaceState(window.history.state, '', nextHref);
      return;
    }

    router.replace(nextHref);
  }, [router]);

  if (!selectedModel) {
    return (
      <div className="stellar-page">
        <PrismPanel size="hero" shimmer style={{ maxWidth: 640, margin: '10vh auto 0' }}>
          <div className="stellar-section-label">Agent Cockpit</div>
          <h1 style={{ margin: '12px 0 0', fontSize: 30, lineHeight: 1.04 }}>当前没有可打开的 Agent 监测对象</h1>
          <p style={{ marginTop: 14, lineHeight: 1.72, color: 'var(--text-secondary)' }}>
            等首页或集群页接入真实观测会话后，这里会承接单个 agent 的成熟监测视图。
          </p>
          <Link href="/clusters" style={{ marginTop: 18, display: 'inline-flex', textDecoration: 'none' }}>
            <PrismButton variant="secondary" size="md">
              <ArrowLeft size={14} />
              返回集群页
            </PrismButton>
          </Link>
        </PrismPanel>
      </div>
    );
  }

  return (
    <div className="stellar-page">
      <ObservatoryHeader
        eyebrow="L2 Agent Workbench"
        title="单个 Agent 的身份、暴露面与定责证据"
        description="从 eBPF 捕获的资源接触、Rust collector 统一事件流、Analyzer 规则与边界评分中，压出可直接行动的单主体责任画像。"
        activeSection="agents"
        backHref="/clusters"
        backLabel="返回集群页"
        stats={[
          activeRunId ? `观测会话 ${activeRunId.slice(0, 12)}...` : '示例场景',
          `${agentModels.length} agents`,
          `${criticalAgentCount} critical`,
          `${totalEvidenceCount} evidence refs`,
        ]}
      />

      <div className="stellar-workbench stellar-workbench--agent">
        <aside className="stellar-rail stellar-rail--sticky" aria-label="Agent directory">
          <PrismPanel grid className="stellar-rail-panel">
            <div className="stellar-section-label">Agent Directory</div>
            <div className="stellar-mini-metrics">
              <div>
                <span>对象</span>
                <strong>{agentModels.length}</strong>
              </div>
              <div>
                <span>Critical</span>
                <strong>{criticalAgentCount}</strong>
              </div>
              <div>
                <span>Evidence</span>
                <strong>{totalEvidenceCount}</strong>
              </div>
            </div>
            <div className="stellar-directory-list">
              {agentModels.map((model) => {
                const surfaceCount = model.resourceCounts.files + model.resourceCounts.networks + model.resourceCounts.boundaries;

                return (
                  <button
                    key={model.agentId}
                    type="button"
                    className="stellar-directory-row"
                    data-active={model.agentId === selectedModel.agentId}
                    data-tone={getSeverityTone(model.highestSeverity)}
                    onClick={() => handleSelectAgent(model.agentId)}
                  >
                    <span className="stellar-directory-row__tone" />
                    <span className="stellar-directory-row__body">
                      <strong>{model.title}</strong>
                      <small>{model.alertIds.length} alerts · {surfaceCount} surfaced signals</small>
                    </span>
                    <span className="stellar-directory-row__badge">{model.highestSeverity ?? 'Info'}</span>
                  </button>
                );
              })}
            </div>
          </PrismPanel>
        </aside>

        <main className="stellar-core">
          <PrismPanel size="lg" grid shimmer className="stellar-command-panel">
            <div className="stellar-command-copy">
              <div className="stellar-section-label">Agent Command Surface</div>
              <h2>{selectedModel.title}</h2>
              <p>{selectedModel.summary}</p>
              <div className="stellar-action-row">
                <Link href={buildClusterHref(selectedModel.parentClusterId)}>
                  <PrismButton variant="secondary" size="sm">回到所属 Cluster</PrismButton>
                </Link>
                {selectedModel.leadAlertId ? (
                  <Link href={buildInvestigationHref(selectedModel.leadAlertId)}>
                    <PrismButton variant="primary" size="sm">进入主调查</PrismButton>
                  </Link>
                ) : null}
              </div>
            </div>
            <div className="stellar-kpi-grid" aria-label="Selected agent metrics">
              <div className="stellar-kpi">
                <span>Severity</span>
                <strong data-tone={getSeverityTone(selectedModel.highestSeverity)}>{selectedModel.highestSeverity ?? 'Info'}</strong>
              </div>
              <div className="stellar-kpi">
                <span>Evidence</span>
                <strong>{selectedModel.linkedEvidence.length}</strong>
              </div>
              <div className="stellar-kpi">
                <span>Roles</span>
                <strong>{selectedModel.claimedRoles.length}</strong>
              </div>
              <div className="stellar-kpi">
                <span>Surface</span>
                <strong>{selectedRiskSurfaceCount}</strong>
              </div>
            </div>
          </PrismPanel>

          <AgentResponsibilityAtlas model={selectedModel} />

          <PrismPanel grid className="stellar-table-panel">
            <div className="stellar-panel-heading">
              <div>
                <div className="stellar-section-label">Responsibility Queue</div>
                <h3>Agent 责任队列</h3>
              </div>
              <span>{agentModels.length} entries</span>
            </div>
            <div className="stellar-data-table" role="table" aria-label="Agent responsibility queue">
              <div className="stellar-data-table__head" role="row">
                <span role="columnheader">Agent</span>
                <span role="columnheader">Severity</span>
                <span role="columnheader">Evidence</span>
                <span role="columnheader">Surface</span>
                <span role="columnheader">Next</span>
              </div>
              {agentModels.map((model) => {
                const surfaceCount = model.resourceCounts.files + model.resourceCounts.networks + model.resourceCounts.boundaries;

                return (
                  <button
                    key={model.agentId}
                    type="button"
                    className="stellar-data-table__row"
                    data-selected={model.agentId === selectedModel.agentId}
                    onClick={() => handleSelectAgent(model.agentId)}
                    role="row"
                  >
                    <span role="cell">
                      <strong>{model.title}</strong>
                      <small>{model.claimedRoles[0] ?? 'pending role claim'}</small>
                    </span>
                    <span role="cell" data-tone={getSeverityTone(model.highestSeverity)}>{model.highestSeverity ?? 'Info'}</span>
                    <span role="cell">{model.linkedEvidence.length}</span>
                    <span role="cell">{surfaceCount}</span>
                    <span role="cell"><ArrowRight size={13} /></span>
                  </button>
                );
              })}
            </div>
          </PrismPanel>

          <div className="stellar-lower-grid">
            <PrismPanel grid>
              <div className="stellar-section-label">Identity Claim</div>
              <div className="stellar-chip-stack">
                {(selectedModel.claimedRoles.length ? selectedModel.claimedRoles : ['等待真实角色声明']).map((role) => (
                  <div key={role} className="stellar-chip-row">
                    <Cpu size={13} />
                    <span>{role}</span>
                  </div>
                ))}
              </div>
            </PrismPanel>

            <PrismPanel grid>
              <div className="stellar-section-label">Lateral Links</div>
              <div className="stellar-chip-stack">
                {selectedModel.relatedAgentIds.map((agentId) => (
                  <Link key={agentId} href={buildAgentHref(agentId)} className="stellar-chip-row stellar-chip-row--link">
                    <span>{agentId}</span>
                    <ArrowRight size={13} />
                  </Link>
                ))}
                {!selectedModel.relatedAgentIds.length ? (
                  <div className="stellar-chip-row">暂无横向 agent 关系</div>
                ) : null}
              </div>
            </PrismPanel>
          </div>
        </main>

        <aside className="stellar-inspector" aria-label="Selected agent inspector">
          <PrismPanel grid className="stellar-inspector-panel">
            <div className="stellar-panel-heading">
              <div>
                <div className="stellar-section-label">Inspector</div>
                <h3>判读抽屉</h3>
              </div>
              <span>{selectedResourceClassCount} classes</span>
            </div>

            <div className="stellar-inspector-summary">
              <div>
                <ShieldAlert size={16} />
                <span>最高风险</span>
                <strong data-tone={getSeverityTone(selectedModel.highestSeverity)}>{selectedModel.highestSeverity ?? 'Info'}</strong>
              </div>
              <div>
                <FileText size={16} />
                <span>告警绑定</span>
                <strong>{selectedModel.alertIds.length}</strong>
              </div>
              <div>
                <Database size={16} />
                <span>资源触点</span>
                <strong>{selectedModel.surfacedResources.length}</strong>
              </div>
              <div>
                <Network size={16} />
                <span>横向关系</span>
                <strong>{selectedModel.relatedAgentIds.length}</strong>
              </div>
            </div>

            <div className="stellar-section-label">Contact Surface</div>
            <div className="stellar-evidence-list">
              {selectedModel.surfacedResources.map((resource) => (
                <div key={resource.nodeId} className="stellar-evidence-row">
                  <strong>{resource.label}</strong>
                  <span>{resource.bucket} · {resource.evidenceCount} evidence refs</span>
                </div>
              ))}
              {!selectedModel.surfacedResources.length ? (
                <div className="stellar-evidence-row">
                  <strong>等待真实资源接触轨迹接入</strong>
                  <span>tls_capture / process / stdiocap 事件会在这里汇聚</span>
                </div>
              ) : null}
            </div>

            <div className="stellar-section-label">Evidence Ledger</div>
            <div className="stellar-evidence-list">
              {selectedModel.linkedEvidence.slice(0, 5).map((item) => (
                <div key={item.evidence_id} className="stellar-evidence-row">
                  <strong>{item.evidence_id}</strong>
                  <span>{item.match_level} · {formatConfidence(item.confidence)} confidence</span>
                </div>
              ))}
              {!selectedModel.linkedEvidence.length ? (
                <div className="stellar-evidence-row">
                  <strong>暂无证据引用</strong>
                  <span>等待 analyzer 产生可追溯 evidence refs</span>
                </div>
              ) : null}
            </div>
          </PrismPanel>
        </aside>
      </div>
    </div>
  );
}
