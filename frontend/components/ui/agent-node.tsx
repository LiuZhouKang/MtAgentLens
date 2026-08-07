'use client';

import { Handle, Position, NodeProps, Node } from '@xyflow/react';
import { motion } from 'motion/react';
import { Codicon } from '@/components/ui/codicon';

export type AgentNodeData = {
  label: string;
  meta?: string;
  icon?: string;
  type?: 'source' | 'target' | 'default';
};

export type AgentNodeType = Node<AgentNodeData, 'agent'>;

export function AgentNode({ data, selected, dragging }: NodeProps<AgentNodeType>) {
  return (
    <motion.div
      initial={{ opacity: 0, filter: 'blur(4px)' }}
      animate={{ opacity: 1, filter: 'blur(0px)' }}
      transition={{ duration: 0.4 }}
      className={`group relative flex items-center gap-3 px-4 py-2.5 rounded-xl border bg-[#141415]/80 backdrop-blur-md transition-all duration-300 ${
        dragging ? 'shadow-[0_0_30px_rgba(255,255,255,0.2)] border-white/50 scale-105 z-50' : 
        selected ? 'border-white/40 shadow-[0_0_20px_rgba(255,255,255,0.1)]' : 'border-white/10 shadow-xl hover:border-white/25 hover:bg-[#1C1C1E]'
      }`}
    >
      <Handle type="target" position={Position.Left} className="!w-2 !h-2 !bg-white/20 !border-white/10" />
      
      <div className={`flex items-center justify-center w-8 h-8 rounded-full border border-white/10 bg-white/[0.03] transition-colors duration-300 ${selected ? 'border-white/30 bg-white/[0.08]' : 'group-hover:border-white/20 group-hover:bg-white/[0.06]'}`}>
        <Codicon name={data.icon || 'gist-commit'} size={14} className="text-white/60 group-hover:text-white transition-colors duration-300" />
      </div>
      
      <div className="flex flex-col gap-0.5 min-w-0 pr-2">
        <strong className="text-sm font-medium tracking-wide text-white/80 group-hover:text-white transition-colors duration-300 truncate">{data.label}</strong>
        {data.meta && (
          <span className="text-[9px] font-medium uppercase tracking-widest text-white/30 truncate">{data.meta}</span>
        )}
      </div>

      <Handle type="source" position={Position.Right} className="!w-2 !h-2 !bg-white/20 !border-white/10" />
    </motion.div>
  );
}
