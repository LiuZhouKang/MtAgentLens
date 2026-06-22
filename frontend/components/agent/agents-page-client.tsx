'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
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
import { ArrowLeft, ArrowRight, Cpu } from 'lucide-react';

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
      <div style={{ minHeight: '100vh', background: 'var(--investigation-stage-bg)', padding: 24, color: 'var(--text-primary)' }}>
        <PrismPanel size="hero" shimmer style={{ maxWidth: 640, margin: '10vh auto 0' }}>
          <div style={{ fontSize: 12, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
            Agent Cockpit
          </div>
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
    <div style={{ minHeight: '100vh', background: 'var(--investigation-stage-bg)', color: 'var(--text-primary)', padding: 24 }}>
      <ObservatoryHeader
        eyebrow="L2 Agent Cockpit"
        title="把单个 Agent 的身份、暴露面与定责线索压成一张判断页"
        description="这一层不再只是读取节点详情，而是把 claimed role、资源接触、横向关系与证据抓握力收束成单主体责任画像。"
        activeSection="agents"
        backHref="/clusters"
        backLabel="返回集群页"
        stats={[
          activeRunId ? `观测会话 ${activeRunId.slice(0, 12)}...` : '示例场景',
          `${agentModels.length} agent entries`,
          `${selectedModel.linkedEvidence.length} linked evidence`,
        ]}
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 300px) minmax(0, 1fr)', gap: 16, marginTop: 18 }}>
        <PrismPanel grid style={{ display: 'grid', gap: 10, alignSelf: 'start' }}>
          <div style={{ fontSize: 12, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
            Agent Directory
          </div>
          {agentModels.map((model) => (
            <button
              key={model.agentId}
              type="button"
              onClick={() => handleSelectAgent(model.agentId)}
              style={{
                textAlign: 'left',
                padding: '12px 14px',
                borderRadius: 16,
                border: model.agentId === selectedModel.agentId ? '1px solid var(--brand-primary)' : '1px solid var(--border-soft)',
                background: model.agentId === selectedModel.agentId ? 'rgba(66, 153, 225, 0.12)' : 'var(--surface-soft)',
                color: 'var(--text-primary)',
                cursor: 'pointer',
              }}
            >
              <div style={{ fontSize: 15, fontWeight: 600 }}>{model.title}</div>
              <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
                {model.alertIds.length} alerts · {model.touchedResources.length} touched resources
              </div>
            </button>
          ))}
        </PrismPanel>

        <div style={{ display: 'grid', gap: 16 }}>
          <PrismPanel size="hero" grid shimmer>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
              <div style={{ maxWidth: 760 }}>
                <div style={{ fontSize: 11, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                  Agent Dossier
                </div>
                <h2
                  className="prism-display"
                  style={{ margin: '12px 0 0', fontSize: 'clamp(26px, 3vw, 40px)', lineHeight: 0.98, letterSpacing: '-0.04em' }}
                >
                  {selectedModel.title}
                </h2>
                <p style={{ marginTop: 14, color: 'var(--text-secondary)', lineHeight: 1.74, fontSize: 14 }}>
                  {selectedModel.summary}
                </p>
                <div className="prism-editorial-note" style={{ marginTop: 16, maxWidth: 620 }}>
                  <div className="prism-editorial-note__label">Reading Cue</div>
                  <div className="prism-editorial-note__copy">
                    这里的目标不是补充更多节点资料，而是迅速确认这个 agent 的身份声明、实际接触面与证据抓握力是否已经足够支撑下一步定责。
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 16 }}>
                  <Link href={buildClusterHref(selectedModel.parentClusterId)} style={{ textDecoration: 'none' }}>
                    <PrismButton variant="secondary" size="sm">回到所属 Cluster</PrismButton>
                  </Link>
                  {selectedModel.leadAlertId ? (
                    <Link href={buildInvestigationHref(selectedModel.leadAlertId)} style={{ textDecoration: 'none' }}>
                      <PrismButton variant="primary" size="sm">进入主调查</PrismButton>
                    </Link>
                  ) : null}
                </div>
              </div>
              <div style={{ display: 'grid', gap: 10, minWidth: 220 }}>
                <div className="prism-meta-inline__item">{selectedModel.highestSeverity ?? 'No Alert'}</div>
                <div className="prism-meta-inline__item">{selectedModel.linkedEvidence.length} linked evidence</div>
                <div className="prism-meta-inline__item">{selectedModel.claimedRoles.length || 0} claimed roles</div>
                <div className="prism-meta-inline__item">{selectedModel.touchedResources.length} touched resources</div>
              </div>
            </div>
          </PrismPanel>

          <AgentResponsibilityAtlas model={selectedModel} />

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 14 }}>
            <PrismPanel grid>
              <div style={{ fontSize: 11, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                Identity Claim
              </div>
              <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
                {(selectedModel.claimedRoles.length ? selectedModel.claimedRoles : ['等待真实角色声明']).map((role) => (
                  <div key={role} className="prism-meta-inline__item">
                    <Cpu size={12} />
                    {role}
                  </div>
                ))}
              </div>
            </PrismPanel>

            <PrismPanel grid>
              <div style={{ fontSize: 11, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                Lateral Links
              </div>
              <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
                {selectedModel.relatedAgentIds.map((agentId) => (
                  <Link key={agentId} href={buildAgentHref(agentId)} style={{ textDecoration: 'none' }}>
                    <div className="prism-meta-inline__item" style={{ justifyContent: 'space-between' }}>
                      <span>{agentId}</span>
                      <ArrowRight size={12} />
                    </div>
                  </Link>
                ))}
              </div>
            </PrismPanel>

            <PrismPanel grid>
              <div style={{ fontSize: 11, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                Contact Surface
              </div>
              <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
                {selectedModel.touchedResources.map((resource) => (
                  <div key={resource} className="prism-meta-inline__item">{resource}</div>
                ))}
              </div>
            </PrismPanel>
          </div>

          <PrismPanel grid>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 11, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                  Next Hop
                </div>
                <div style={{ marginTop: 8, color: 'var(--text-secondary)', lineHeight: 1.7, fontSize: 13 }}>
                  从单个 agent 可继续回到所属 cluster，或直接进入主调查确认最终 verdict。
                </div>
              </div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <Link href={buildClusterHref(selectedModel.parentClusterId)} style={{ textDecoration: 'none' }}>
                  <PrismButton variant="secondary" size="sm">回到所属 Cluster</PrismButton>
                </Link>
                {selectedModel.leadAlertId ? (
                  <Link href={buildInvestigationHref(selectedModel.leadAlertId)} style={{ textDecoration: 'none' }}>
                    <PrismButton variant="primary" size="sm">进入主调查</PrismButton>
                  </Link>
                ) : null}
              </div>
            </div>
          </PrismPanel>
        </div>
      </div>
    </div>
  );
}
