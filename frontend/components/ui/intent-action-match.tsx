'use client';

import type { CorrelationEvidence } from '@/lib/types';

const matchColors: Record<string, string> = {
  L1Exact: '#34d399',
  L2Normalized: '#60a5fa',
  L3Containment: '#f59e0b',
  L4Basename: '#f59e0b',
  L5Substring: '#f43f5e',
};

export default function IntentActionMatch({ evidence }: { evidence: CorrelationEvidence }) {
  return (
    <div className="mb-2 rounded-lg p-3" style={{ background: 'rgba(255,255,255,0.03)' }}>
      <div className="mb-1 flex items-center gap-2">
        <span
          className="rounded px-1.5 py-0.5 text-[12px] font-semibold"
          style={{
            background: (matchColors[evidence.match_level] ?? '#68687a') + '22',
            color: matchColors[evidence.match_level],
          }}
        >
          {evidence.match_level}
        </span>
        <span className="text-[12px] text-[#68687a]">置信度 {evidence.confidence.toFixed(2)}</span>
      </div>
      <div className="font-mono text-[12px] text-white/60">
        intent:{evidence.intent_id.slice(0, 8)} → action:{evidence.action_id.slice(0, 8)}
      </div>
    </div>
  );
}
