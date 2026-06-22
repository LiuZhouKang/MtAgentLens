'use client';

import { useAppStore } from '@/lib/store';
import { ArrowRight } from 'lucide-react';

export default function TracebackChain({ nodeIds, edgeIds }: { nodeIds: string[]; edgeIds: string[] }) {
  const selectNode = useAppStore((s) => s.selectNode);
  const flyToNode = useAppStore((s) => s.flyToNode);

  return (
    <div>
      <h4 className="text-[12px] uppercase tracking-wider text-[#68687a] mb-2">
        责任追溯链 · {nodeIds.length} nodes
      </h4>
      <div className="flex items-center flex-wrap gap-1">
        {nodeIds.map((id, i) => (
          <span key={id} className="flex items-center gap-1">
            <button
              className="text-[12px] font-mono text-[#818cf8] hover:text-white/90 hover:underline"
              onClick={() => { selectNode(id); flyToNode(id); }}
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
