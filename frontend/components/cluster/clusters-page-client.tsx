'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import PrismPanel from '@/components/ui/prism-panel';
import PrismButton from '@/components/ui/prism-button';
import ClusterDetailAtlas from '@/components/ui/cluster-detail-atlas';
import ObservatoryHeader from '@/components/system/observatory-header';
import { api } from '@/lib/api';
import { useAppStore } from '@/lib/store';
import { demoEvidence } from '@/lib/demo-data';
import { deriveClusterIds } from '@/lib/stellar-directory';
import { buildClusterModel } from '@/lib/cluster-model';
import { buildClusterHref, parseClusterId } from '@/lib/cluster-route';
import { buildAgentHref } from '@/lib/agent-route';
import { buildInvestigationHref } from '@/lib/investigation-route';
import { ArrowLeft, ArrowRight, Radar } from 'lucide-react';

export default function ClustersPageClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const activeRunId = useAppStore((state) => state.activeRunId);
  const alerts = useAppStore((state) => state.alerts);
  const graph = useAppStore((state) => state.graph);
  const [hashClusterId, setHashClusterId] = useState<string | null>(null);

  useEffect(() => {
    const syncHashCluster = () => {
      setHashClusterId(parseClusterId(new URLSearchParams(), window.location.hash));
    };

    syncHashCluster();
    window.addEventListener('hashchange', syncHashCluster);
    return () => window.removeEventListener('hashchange', syncHashCluster);
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

  const clusterIds = useMemo(() => deriveClusterIds(alerts, graph), [alerts, graph]);
  const selectedClusterId = useMemo(() => {
    const parsed = parseClusterId(searchParams, hashClusterId ? `#clusterId=${encodeURIComponent(hashClusterId)}` : null);
    return parsed ?? clusterIds[0] ?? null;
  }, [clusterIds, hashClusterId, searchParams]);

  const clusterModels = useMemo(
    () => clusterIds.map((clusterId) => buildClusterModel(clusterId, graph, alerts, evidenceItems)),
    [alerts, clusterIds, evidenceItems, graph],
  );

  const selectedModel = useMemo(
    () => clusterModels.find((model) => model.clusterId === selectedClusterId) ?? clusterModels[0] ?? null,
    [clusterModels, selectedClusterId],
  );

  const handleSelectCluster = useCallback((clusterId: string) => {
    const nextHref = buildClusterHref(clusterId);
    setHashClusterId(clusterId);

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
            Cluster Observatory
          </div>
          <h1 style={{ margin: '12px 0 0', fontSize: 30, lineHeight: 1.04 }}>当前没有可钻取的 Agent 集群</h1>
          <p style={{ marginTop: 14, lineHeight: 1.72, color: 'var(--text-secondary)' }}>
            等首页接入真实观测会话或示例场景后，这里会承接责任星图中的 cluster 深钻路径。
          </p>
          <Link href="/" style={{ marginTop: 18, display: 'inline-flex', textDecoration: 'none' }}>
            <PrismButton variant="secondary" size="md">
              <ArrowLeft size={14} />
              返回首页
            </PrismButton>
          </Link>
        </PrismPanel>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--investigation-stage-bg)', color: 'var(--text-primary)', padding: 24 }}>
      <ObservatoryHeader
        eyebrow="L1 Cluster Observatory"
        title="把一个责任集群的扩散形态压成可以直接判断的总谱"
        description="这一层不再只是列出 cluster 成员，而是先交代 fan-out 压力、边界撕裂、证据饱和度与下一步最值得进入的 agent。"
        activeSection="clusters"
        backHref="/overview"
        backLabel="返回总览"
        stats={[
          activeRunId ? `观测会话 ${activeRunId.slice(0, 12)}...` : '示例场景',
          `${clusterModels.length} cluster entries`,
          `${selectedModel.linkedEvidence.length} linked evidence`,
        ]}
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 300px) minmax(0, 1fr)', gap: 16, marginTop: 18 }}>
        <PrismPanel grid style={{ display: 'grid', gap: 10, alignSelf: 'start' }}>
          <div style={{ fontSize: 12, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
            Cluster Directory
          </div>
          {clusterModels.map((model) => (
            <button
              key={model.clusterId}
              type="button"
              onClick={() => handleSelectCluster(model.clusterId)}
              style={{
                textAlign: 'left',
                padding: '12px 14px',
                borderRadius: 16,
                border: model.clusterId === selectedModel.clusterId ? '1px solid var(--brand-primary)' : '1px solid var(--border-soft)',
                background: model.clusterId === selectedModel.clusterId ? 'rgba(66, 153, 225, 0.12)' : 'var(--surface-soft)',
                color: 'var(--text-primary)',
                cursor: 'pointer',
              }}
            >
              <div style={{ fontSize: 15, fontWeight: 600 }}>{model.title}</div>
              <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
                {model.alertIds.length} alerts · {model.linkedEvidence.length} evidence
              </div>
            </button>
          ))}
        </PrismPanel>

        <div style={{ display: 'grid', gap: 16 }}>
          <PrismPanel size="hero" grid shimmer>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
              <div style={{ maxWidth: 760 }}>
                <div style={{ fontSize: 11, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                  Cluster Dossier
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
                    先判断这个 cluster 是否已经形成真正的责任压力面，再决定是沿着主调查进入，还是沿着 agent 钻取去看哪一条委托链真正触发了越界。
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 16 }}>
                  {selectedModel.leadAlertId ? (
                    <Link href={buildInvestigationHref(selectedModel.leadAlertId)} style={{ textDecoration: 'none' }}>
                      <PrismButton variant="primary" size="sm">进入主调查</PrismButton>
                    </Link>
                  ) : null}
                  <Link href="/handoff" style={{ textDecoration: 'none' }}>
                    <PrismButton variant="ghost" size="sm">查看 Handoff 专题</PrismButton>
                  </Link>
                </div>
              </div>
              <div style={{ display: 'grid', gap: 10, minWidth: 220 }}>
                <div className="prism-meta-inline__item">{selectedModel.highestSeverity ?? 'No Alert'}</div>
                <div className="prism-meta-inline__item">{selectedModel.boundaryCrossingCount} boundary crossings</div>
                <div className="prism-meta-inline__item">{selectedModel.highlightEdgeIds.length} highlighted edges</div>
                <div className="prism-meta-inline__item">{selectedModel.relatedAgentIds.length} active agents</div>
              </div>
            </div>
          </PrismPanel>

          <ClusterDetailAtlas model={selectedModel} />

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 14 }}>
            <PrismPanel grid>
              <div style={{ fontSize: 11, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                Conductor Ring
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
                Investigation Aperture
              </div>
              <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
                {selectedModel.leadAlertId ? (
                  <Link href={buildInvestigationHref(selectedModel.leadAlertId)} style={{ textDecoration: 'none' }}>
                    <PrismButton variant="primary" size="sm">进入主调查</PrismButton>
                  </Link>
                ) : (
                  <div className="prism-meta-inline__item">等待后续风险事件接入</div>
                )}
                <Link href="/handoff" style={{ textDecoration: 'none' }}>
                  <PrismButton variant="ghost" size="sm">查看 Handoff 专题</PrismButton>
                </Link>
              </div>
            </PrismPanel>

            <PrismPanel grid>
              <div style={{ fontSize: 11, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--text-quaternary)' }}>
                Evidence Field
              </div>
              <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
                <div className="prism-meta-inline__item">{selectedModel.linkedEvidence.length} linked evidence</div>
                <div className="prism-meta-inline__item">{selectedModel.alertIds.length} related alerts</div>
                <div className="prism-meta-inline__item">
                  <Radar size={12} />
                  当前 cluster id: {selectedModel.clusterId}
                </div>
                <div className="prism-meta-inline__item">
                  当前 cluster 已经具备从结构压力、边界裂缝到证据密度的连续读取路径。
                </div>
              </div>
            </PrismPanel>
          </div>
        </div>
      </div>
    </div>
  );
}
