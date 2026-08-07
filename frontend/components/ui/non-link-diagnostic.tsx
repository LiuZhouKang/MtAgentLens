'use client';

import type { NonLinkDiagnostic } from '@/lib/types';
import { Link2Off } from 'lucide-react';

const reasonLabels: Record<string, string> = {
  no_argument_match: '参数不匹配',
  candidate_not_available: '候选不可用',
  below_confidence_cutoff: '低于置信度阈值',
  outside_time_window: '超出时间窗口',
  ambiguous_competition: '匹配歧义',
  intent_not_primary_eligible: 'Intent 不符合主匹配条件',
  observer_pending: '等待 Observer LLM',
};

export default function NonLinkDiagnosticView({ diagnostic }: { diagnostic: NonLinkDiagnostic }) {
  return (
    <div
      className="mb-2 flex items-start gap-3 rounded-lg p-3"
      style={{ background: 'rgba(255,255,255,0.02)' }}
    >
      <Link2Off size={14} className="mt-0.5 shrink-0 text-[#f43f5e]" />
      <div>
        <span className="text-xs text-white/60">
          {reasonLabels[diagnostic.reason] ?? diagnostic.reason}
        </span>
        <span className="ml-2 text-[12px] text-[#68687a]">{diagnostic.stage}</span>
      </div>
    </div>
  );
}
