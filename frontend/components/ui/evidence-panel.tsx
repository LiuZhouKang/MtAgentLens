'use client';

import type { CorrelationEvidence, Intent, Action, NonLinkDiagnostic } from '@/lib/types';
import { X } from 'lucide-react';
import IntentActionMatch from './intent-action-match';
import NonLinkDiagnosticView from './non-link-diagnostic';
import TracebackChain from './traceback-chain';

interface EvidencePanelProps {
  evidence: CorrelationEvidence[];
  intents: Intent[];
  actions: Action[];
  nonLinks: NonLinkDiagnostic[];
  tracebackNodeIds?: string[];
  tracebackEdgeIds?: string[];
  onClose: () => void;
}

export default function EvidencePanel({
  evidence, nonLinks, tracebackNodeIds, tracebackEdgeIds, onClose,
}: EvidencePanelProps) {
  return (
    <div
      className="absolute right-0 top-0 bottom-0 w-[60%] z-40 overflow-y-auto"
      style={{
        background: 'rgba(8, 12, 24, 0.92)',
        backdropFilter: 'blur(32px)',
        borderLeft: '1px solid rgba(255,255,255,0.06)',
      }}
    >
      <div className="p-6">
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-sm font-semibold tracking-wider text-white/80">证据取证</h3>
          <button onClick={onClose} className="text-white/40 hover:text-white/80"><X size={20} /></button>
        </div>
        {tracebackNodeIds && tracebackEdgeIds && (
          <section className="mb-6">
            <TracebackChain nodeIds={tracebackNodeIds} edgeIds={tracebackEdgeIds} />
          </section>
        )}
        <section className="mb-6">
          <h4 className="text-[12px] uppercase tracking-wider text-[#68687a] mb-3">
            关联证据 ({evidence.length})
          </h4>
          {evidence.map((ev) => <IntentActionMatch key={ev.evidence_id} evidence={ev} />)}
        </section>
        <section>
          <h4 className="text-[12px] uppercase tracking-wider text-[#68687a] mb-3">
            失败关联诊断 ({nonLinks.length})
          </h4>
          {nonLinks.map((nl) => <NonLinkDiagnosticView key={nl.diagnostic_id} diagnostic={nl} />)}
        </section>
      </div>
    </div>
  );
}
