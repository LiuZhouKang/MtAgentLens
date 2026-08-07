// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import AgentAvatar from '@/components/ui/agent-avatar';

afterEach(() => cleanup());

describe('AgentAvatar', () => {
  it('renders the monogram initials for an agent id', () => {
    render(<AgentAvatar agentId="agent-security" />);
    expect(screen.getByText('S')).toBeDefined();
  });

  it('is a non-interactive img with the agent id as its accessible name', () => {
    render(<AgentAvatar agentId="agent-security" />);
    const avatar = screen.getByRole('img', { name: 'agent-security' });
    expect(avatar).toBeDefined();
  });

  it('applies the requested size as diameter', () => {
    render(<AgentAvatar agentId="agent-security" size={26} />);
    const avatar = screen.getByRole('img', { name: 'agent-security' });
    expect(avatar.style.width).toBe('26px');
    expect(avatar.style.height).toBe('26px');
  });

  it('falls back to a question mark for empty ids', () => {
    render(<AgentAvatar agentId="" />);
    expect(screen.getByText('?')).toBeDefined();
  });

  it('renders a derived geometric glyph (not digits, not a generic bot) for uuid-like ids', () => {
    render(<AgentAvatar agentId="86091f07-4694-4292-92d1-3522ad8acd25" />);
    // uuid initials are meaningless digits — never render them
    expect(screen.queryByText('84')).toBeNull();
    // the generic lucide bot is gone — a unique, derived geometric glyph takes its place
    expect(document.querySelector('svg.lucide-bot')).toBeNull();
    expect(document.querySelector('[data-agent-glyph]')).not.toBeNull();
    expect(screen.getByRole('img', { name: '86091f07-4694-4292-92d1-3522ad8acd25' })).toBeDefined();
  });
});
