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
        activeRunId={null}
        availableAlerts={demoAlerts}
        onSelectAlert={vi.fn()}
        evidenceLoading={false}
        evidenceError={null}
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

  it('渲染取证链面板（引擎判定 + 污染链 + 策略矩阵 + 诊断），空源显示空态', () => {
    // 前提：真实 store 未导入 db（dbAssets=null）→ 取证链三区显示「无」而非伪造
    const model = buildInvestigationModel(demoAlerts[0], demoGraph, demoEvidence);

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

    // 取证链面板标题（SectionPanel eyebrow）
    expect(screen.getByText('取证链')).toBeDefined();
    // 空源空态：pollution/policy/nld 均为空 → 显示「无污染传播」而非伪造
    expect(screen.getByText(/无污染传播/i)).toBeDefined();
    expect(screen.getByText(/无策略判定/i)).toBeDefined();
    expect(screen.getByText(/无失配诊断/i)).toBeDefined();
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
        activeRunId={null}
        availableAlerts={demoAlerts}
        onSelectAlert={vi.fn()}
        evidenceLoading={false}
        evidenceError={null}
      />,
    );

    expect(screen.getByText('暂无时间线数据')).toBeDefined();
  });
});
