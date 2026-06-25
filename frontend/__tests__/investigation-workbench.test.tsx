// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import InvestigationWorkbench from '@/components/investigation/investigation-workbench';
import { buildInvestigationModel } from '@/lib/investigation';
import { demoAlerts, demoEvidence, demoGraph } from '@/lib/demo-data';

vi.mock('@/components/ui/theme-toggle', () => ({
  default: function MockThemeToggle() {
    return <button type="button">主题切换</button>;
  },
}));

afterEach(() => {
  cleanup();
});

describe('InvestigationWorkbench', () => {
  it('renders the case theatre workbench and uses the investigation cluster anchor for the cluster CTA', () => {
    const model = {
      ...buildInvestigationModel(demoAlerts[0], demoGraph, demoEvidence),
      parentClusterId: 'cluster-security-anchor',
    };

    render(
      <InvestigationWorkbench
        alert={demoAlerts[0]}
        model={model}
        activeRunId={null}
        availableAlerts={demoAlerts}
        onSelectAlert={vi.fn()}
        evidenceLoading={false}
        evidenceError={null}
      />,
    );

    expect(screen.getByText('Case Theatre')).toBeDefined();
    expect(screen.getByText('Responsibility Chain')).toBeDefined();
    expect(screen.getByText('Evidence Chain')).toBeDefined();
    expect(screen.getByText('Rule & Verdict')).toBeDefined();
    expect(screen.getByRole('link', { name: '查看所属 Cluster' }).getAttribute('href')).toBe(
      '/clusters#clusterId=cluster-security-anchor',
    );
  });
});
