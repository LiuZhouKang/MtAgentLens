'use client';

import { useAppStore } from '@/lib/store';
import { ArrowRight } from 'lucide-react';
import { agentColor } from '@/lib/agent-identity';

export default function TracebackChain({
  nodeIds,
  edgeIds,
}: {
  nodeIds: string[];
  edgeIds: string[];
}) {
  const selectNode = useAppStore((s) => s.selectNode);
  const flyToNode = useAppStore((s) => s.flyToNode);

  return (
    <div>
      <h4 className="mb-2 text-[12px] tracking-wider text-[#68687a] uppercase">
        责任追溯链 · {nodeIds.length} nodes · {edgeIds.length} edges
      </h4>
      <div className="flex flex-wrap items-center gap-1">
        {nodeIds.map((id, i) => (
          <span key={id} className="flex items-center gap-1">
            <button
              className="font-mono text-[12px] hover:text-white/90 hover:underline"
              style={{ color: agentColor(id) }}
              onClick={() => {
                selectNode(id);
                flyToNode(id);
              }}
            >
              {id.slice(0, 8)}
            </button>
            {i < nodeIds.length - 1 && <ArrowRight size={10} className="text-[#68687a]" />}
          </span>
        ))}
      </div>
    </div>
  );
}
