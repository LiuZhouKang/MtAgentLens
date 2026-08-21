'use client';

import { useMemo, useState } from 'react';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import AgentAvatar from '@/components/ui/agent-avatar';
import { buildAgentFamilyTree, type AgentFamilyNode } from '@/lib/identity-lineage';
import type { SubagentBoundary } from '@/lib/types';

const T = { xs: 9.5, sm: 10.5, md: 12 };

/** 委派树节点：AgentAvatar + 递归 parent→child，reason + childPid 标注。 */
function FamilyNode({
  node,
  tree,
  depth = 0,
}: {
  node: AgentFamilyNode;
  tree: Map<string, AgentFamilyNode>;
  depth?: number;
}) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = node.children.length > 0;
  return (
    <div>
      <button
        type="button"
        onClick={() => hasChildren && setExpanded(!expanded)}
        className="ln-tree-node"
        style={{ paddingLeft: depth > 0 ? 10 : 4 }}
      >
        <span style={{ color: 'var(--ln-text-muted)', width: 12, flexShrink: 0 }}>
          {hasChildren ? (expanded ? '▾' : '▸') : '·'}
        </span>
        <AgentAvatar agentId={node.agentId} size={16} ring={false} />
        <span style={{ fontFamily: 'var(--ln-mono)', fontWeight: 600, fontSize: T.sm }}>
          {node.agentId}
        </span>
        {!hasChildren && (
          <span style={{ fontSize: T.xs, color: 'var(--ln-text-muted)' }}>无委派</span>
        )}
      </button>
      {expanded && hasChildren && (
        <div className="ln-tree-children">
          {node.children.map((child) => {
            const childNode = tree.get(child.childAgentId);
            return (
              <div key={child.childAgentId} style={{ display: 'flex', flexDirection: 'column' }}>
                {childNode && <FamilyNode node={childNode} tree={tree} depth={depth + 1} />}
                <span
                  style={{
                    fontSize: 9,
                    color: 'var(--ln-text-muted)',
                    marginLeft: 32,
                    fontFamily: 'var(--ln-mono)',
                  }}
                >
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
 * 委派家族树面板——由 subagent_boundaries 组 parent→child 委派树。
 * AgentAvatar 渐变圆徽章 + reason/pid 标注，视觉与进程谱系统一。
 */
export default function AgentFamilyTree({ subagentBoundaries }: AgentFamilyTreeProps) {
  const tree = useMemo(() => buildAgentFamilyTree(subagentBoundaries), [subagentBoundaries]);
  const roots = useMemo(() => {
    const childSet = new Set(subagentBoundaries.map((b) => b.child_agent_id));
    return Array.from(tree.keys())
      .filter((id) => !childSet.has(id))
      .sort();
  }, [tree, subagentBoundaries]);

  return (
    <SectionPanel eyebrow="身份血缘" title="委派家族" bodyClassName="ln-panel-body">
      {tree.size === 0 ? (
        <div style={{ fontSize: T.sm, color: 'var(--ln-text-muted)' }}>此数据源无委派边界</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {roots.map((id) => (
            <FamilyNode key={id} node={tree.get(id)!} tree={tree} />
          ))}
        </div>
      )}
    </SectionPanel>
  );
}
