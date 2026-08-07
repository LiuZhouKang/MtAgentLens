'use client';

import { useMemo, useState } from 'react';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { buildAgentFamilyTree, type AgentFamilyNode } from '@/lib/identity-lineage';
import type { SubagentBoundary } from '@/lib/types';

const T = { xs: 9.5, sm: 10.5, md: 12 };

/** 委派树节点：递归渲染 parent→child，reason + childPid 标注。 */
function FamilyNode({ node, tree }: { node: AgentFamilyNode; tree: Map<string, AgentFamilyNode> }) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = node.children.length > 0;
  return (
    <div>
      <button
        type="button"
        onClick={() => hasChildren && setExpanded(!expanded)}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, background: 'none', border: 'none',
          cursor: hasChildren ? 'pointer' : 'default', padding: '3px 0', color: 'var(--ln-text)',
          fontSize: T.sm, textAlign: 'left',
        }}
      >
        <span style={{ color: 'var(--ln-text-muted)', width: 12 }}>{hasChildren ? (expanded ? '▾' : '▸') : '·'}</span>
        <span style={{ fontFamily: 'var(--ln-mono)', fontWeight: 600 }}>{node.agentId}</span>
        {!hasChildren && <span style={{ fontSize: T.xs, color: 'var(--ln-text-muted)' }}>无委派</span>}
      </button>
      {expanded && hasChildren && (
        <div style={{ marginLeft: 18, borderLeft: '1px solid var(--ln-border)', paddingLeft: 10, display: 'flex', flexDirection: 'column', gap: 2 }}>
          {node.children.map((child) => {
            const childNode = tree.get(child.childAgentId);
            return (
              <div key={child.childAgentId} style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
                {childNode && <FamilyNode node={childNode} tree={tree} />}
                <span style={{ fontSize: 9, color: 'var(--ln-text-muted)', marginLeft: 22, fontFamily: 'var(--ln-mono)' }}>
                  {child.reason} · pid {child.childPid}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export type AgentFamilyTreeProps = {
  subagentBoundaries: SubagentBoundary[];
};

/**
 * 委派家族树面板——由 subagent_boundaries 组 parent→child 委派树，从根（无父）开始递归。
 */
export default function AgentFamilyTree({ subagentBoundaries }: AgentFamilyTreeProps) {
  const tree = useMemo(() => buildAgentFamilyTree(subagentBoundaries), [subagentBoundaries]);
  const roots = useMemo(() => {
    const childSet = new Set(subagentBoundaries.map((b) => b.child_agent_id));
    return Array.from(tree.keys()).filter((id) => !childSet.has(id)).sort();
  }, [tree, subagentBoundaries]);

  return (
    <SectionPanel eyebrow="身份血缘" title="委派家族" bodyClassName="ln-panel-body">
      {tree.size === 0 ? (
        <div style={{ fontSize: T.sm, color: 'var(--ln-text-muted)' }}>此数据源无委派边界</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {roots.map((id) => (
            <FamilyNode key={id} node={tree.get(id)!} tree={tree} />
          ))}
        </div>
      )}
    </SectionPanel>
  );
}
