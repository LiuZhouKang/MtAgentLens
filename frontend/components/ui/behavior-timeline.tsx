'use client';

import type { BehaviorTimeline as BT } from '@/lib/types';
import { Clock } from 'lucide-react';

const opColor: Record<string, string> = {
  FileRead: '#60a5fa', FileWrite: '#f59e0b', NetworkConnect: '#34d399',
  ProcessExec: '#a78bfa', Delegation: '#818cf8',
};

export default function BehaviorTimeline({ timeline }: { timeline: BT }) {
  return (
    <div>
      <h4 style={{ fontSize: 11, fontWeight: 500, color: '#818cf8', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 4, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        <Clock size={13} /> Behavior Timeline &middot; {timeline.total_duration_ms}ms
      </h4>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingLeft: 12, borderLeft: '1px solid rgba(255,255,255,0.06)' }}>
        {timeline.steps.map((step, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
            <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'rgba(255,255,255,0.3)', whiteSpace: 'nowrap', marginTop: 1 }}>
              T+{(step.timestamp_ns / 1e9).toFixed(1)}s
            </span>
            <span style={{
              width: 6, height: 6, borderRadius: '50%', marginTop: 4, flexShrink: 0,
              background: opColor[step.operation ?? ''] ?? 'rgba(255,255,255,0.2)',
            }} />
            <div>
              <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.7)' }}>{step.description}</span>
              {step.resource && (
                <code style={{ fontSize: 11, color: 'rgba(255,255,255,0.3)', marginLeft: 6 }}>
                  {step.resource}
                </code>
              )}
              {step.source_event_id && (
                <code style={{ fontSize: 10, color: 'rgba(255,255,255,0.2)', marginLeft: 6 }}>
                  [{step.source_event_id.slice(0, 8)}]
                </code>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
