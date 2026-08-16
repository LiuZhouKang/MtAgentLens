// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import InvestigationWorkbench from '@/components/investigation/investigation-workbench';
import { buildInvestigationModel } from '@/lib/investigation';
import { demoAlerts, demoEvidence, demoGraph } from '@/lib/demo-data';

afterEach(() => {
  cleanup();
});

describe('InvestigationWorkbench', () => {
  it('renders the workbench with alert queue, timeline and evidence chain', () => {
    const model = buildInvestigationModel(demoAlerts[0], demoGraph, demoEvidence);

    render(
      <InvestigationWorkbench
        alert={demoAlerts[0]}
        model={model}
        availableAlerts={demoAlerts}
        onSelectAlert={vi.fn()}
      />,
    );

    // Left: alert queue
    expect(screen.getByText('告警队列')).toBeDefined();
    expect(screen.getAllByText(demoAlerts[0].title).length).toBeGreaterThan(0);
    // Center: timeline + evidence chain
    expect(screen.getByText('事件时间线')).toBeDefined();
    expect(screen.getByText(`${model.timelineSteps.length} 步`)).toBeDefined();
    expect(screen.getAllByText('证据链').length).toBeGreaterThan(0);
    // Right: verdict + subject + actions
    expect(screen.getByText('判决')).toBeDefined();
    expect(screen.getByText('主体信息')).toBeDefined();
    expect(screen.getByText('后续步骤')).toBeDefined();
  });

  it('shows an empty state when the timeline has no steps', () => {
    const model = {
      ...buildInvestigationModel(demoAlerts[0], demoGraph, demoEvidence),
      timelineSteps: [],
    };

    render(
      <InvestigationWorkbench
        alert={demoAlerts[0]}
        model={model}
        availableAlerts={demoAlerts}
        onSelectAlert={vi.fn()}
      />,
    );

    expect(screen.getByText('暂无时间线数据')).toBeDefined();
  });
});
