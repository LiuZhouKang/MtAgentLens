import { describe, expect, it } from 'vitest';
import { dockItems, buildWorkspaceAction } from '@/components/command-center/incident-rail';
import type { ResponsibilityAlert } from '@/lib/types';

// The rail was unified with the 4-page structure (态势 / 仪表盘 / 告警 / 追踪).
// Its dock now mirrors that: 事件 / 证据 / 链路 — and every workspace action
// must route to a page that still exists (no /agents, /handoff, /clusters).

const alert = {
  alert_id: 'demo-alert-1',
} as unknown as ResponsibilityAlert;

describe('incident rail dock', () => {
  it('exposes exactly the 3 unified tools — no agent/handoff remnants', () => {
    expect(dockItems.map((item) => item.id)).toEqual(['incident', 'evidence', 'trace']);
    // Cast to string: the DockTool type already excludes these, so a literal
    // comparison would be a TS2367 no-overlap error. The runtime guard stays.
    const ids = dockItems.map((item) => item.id as string);
    expect(ids).not.toContain('agent');
    expect(ids).not.toContain('handoff');
  });
});

describe('incident rail workspace actions', () => {
  it('routes incident to the investigations page', () => {
    expect(buildWorkspaceAction('incident', alert)).toEqual({
      href: '/investigations#alertId=demo-alert-1',
      label: '打开调查',
    });
  });

  it('routes evidence to the investigations page', () => {
    expect(buildWorkspaceAction('evidence', alert)).toEqual({
      href: '/investigations#alertId=demo-alert-1',
      label: '打开调查',
    });
  });

  it('routes trace to the traces page', () => {
    expect(buildWorkspaceAction('trace', alert)).toEqual({
      href: '/traces#alertId=demo-alert-1',
      label: '打开链路',
    });
  });
});
