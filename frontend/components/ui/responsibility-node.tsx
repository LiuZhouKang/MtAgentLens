'use client';

import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import { motion } from 'motion/react';
import { Codicon } from '@/components/ui/codicon';

export type ResponsibilityNodeData = {
  label: string;
  nodeType: string;
  selected?: boolean;
};

export type ResponsibilityNodeType = Node<ResponsibilityNodeData, 'responsibility'>;

const NODE_STYLE: Record<string, { color: string; icon: string; label: string }> = {
  Agent: { color: '#60a5fa', icon: 'organization', label: 'Agent' },
  Resource: { color: '#34d399', icon: 'file', label: 'Resource' },
  PolicyBoundary: { color: '#f43f5e', icon: 'shield', label: 'Boundary' },
  Process: { color: '#fbbf24', icon: 'server-process', label: 'Process' },
  ToolCall: { color: '#a78bfa', icon: 'tools', label: 'Tool' },
  Credential: { color: '#fb923c', icon: 'key', label: 'Cred' },
  Context: { color: '#22d3ee', icon: 'symbol-namespace', label: 'Context' },
};

const DEFAULT_STYLE = { color: '#6b7280', icon: 'circle-filled', label: 'Node' };

export function ResponsibilityNode({ data }: NodeProps<ResponsibilityNodeType>) {
  const style = NODE_STYLE[data.nodeType] ?? DEFAULT_STYLE;
  const isSelected = data.selected === true;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.8, filter: 'blur(4px)' }}
      animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      className="group relative flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl border bg-[#141415]/90 backdrop-blur-md transition-all duration-300 min-w-[140px]"
      style={{
        borderColor: isSelected ? style.color : 'rgba(255,255,255,0.08)',
        boxShadow: isSelected
          ? `0 0 24px ${style.color}22, inset 0 0 12px ${style.color}08`
          : '0 0 0 0 transparent',
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!w-2 !h-2 !border !border-white/10 !bg-white/20"
      />

      <div
        className="flex items-center justify-center w-8 h-8 rounded-lg border transition-all duration-300 shrink-0"
        style={{
          borderColor: `${style.color}33`,
          backgroundColor: `${style.color}10`,
        }}
      >
        <span style={{ color: style.color, display: 'inherit' }}>
            <Codicon name={style.icon} size={14} />
          </span>
      </div>

      <div className="flex flex-col gap-0.5 min-w-0">
        <strong className="text-xs font-medium tracking-wide text-white/80 truncate max-w-[120px]">
          {data.label}
        </strong>
        <span
          className="text-[9px] font-medium uppercase tracking-widest truncate"
          style={{ color: `${style.color}99` }}
        >
          {style.label}
        </span>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        className="!w-2 !h-2 !border !border-white/10 !bg-white/20"
      />
    </motion.div>
  );
}
