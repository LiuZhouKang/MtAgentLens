'use client';

import { Bot } from 'lucide-react';
import { agentGradient, agentGlyphSpec, agentInitials } from '@/lib/agent-identity';
import AgentGlyph from '@/components/ui/agent-glyph';

type AgentAvatarProps = {
  agentId: string;
  /** Diameter in px. */
  size?: number;
  /** Subtle dark inset ring so the fill reads on near-black surfaces. */
  ring?: boolean;
};

export default function AgentAvatar({ agentId, size = 18, ring = true }: AgentAvatarProps) {
  const [from, to] = agentGradient(agentId);
  const initials = agentInitials(agentId);
  const glyph = initials === null ? agentGlyphSpec(agentId) : null;
  const glyphSize = Math.round(size * 0.55);
  return (
    <span
      role="img"
      aria-label={agentId || 'unknown agent'}
      title={agentId || undefined}
      style={{
        width: size,
        height: size,
        minWidth: size,
        borderRadius: '50%',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: `linear-gradient(135deg, ${from}, ${to})`,
        boxShadow: ring ? 'inset 0 0 0 1px rgba(0,0,0,0.35)' : undefined,
        fontSize: Math.round(size * 0.44),
        fontWeight: 600,
        color: '#fff',
        lineHeight: 1,
        userSelect: 'none',
        flexShrink: 0,
      }}
    >
      {glyph ? (
        <AgentGlyph spec={glyph} size={glyphSize} />
      ) : initials === null ? (
        // Unreachable for current id shapes (uuid → glyph, readable → initials),
        // kept as a defensive fallback against future id formats.
        <Bot size={glyphSize} aria-hidden="true" strokeWidth={2} />
      ) : (
        initials
      )}
    </span>
  );
}
