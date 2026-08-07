// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import AlertEmptyState from '@/components/investigation/alert-empty-state';

describe('AlertEmptyState', () => {
  it('invites to import / watch when no data source is attached', () => {
    render(<AlertEmptyState dataSource={null} graphNodeCount={0} evidenceCount={0} />);
    expect(screen.getByText('暂无告警数据')).toBeDefined();
    expect(screen.getByText('等待导入数据或实时观测会话接入')).toBeDefined();
  });

  it('reports consumable counts when a real run has a graph but zero alerts', () => {
    render(
      <AlertEmptyState
        dataSource={{ kind: 'db', label: 'collector.responsibility.db', runId: 'run-1' }}
        graphNodeCount={38}
        evidenceCount={55}
      />,
    );
    expect(
      screen.getByText(
        '当前数据源「collector.responsibility.db」未检测到告警（已解析 38 节点 · 55 条证据）',
      ),
    ).toBeDefined();
  });

  it('notes when the data source has no parseable responsibility data', () => {
    render(
      <AlertEmptyState
        dataSource={{ kind: 'db', label: 'empty.db', runId: 'run-2' }}
        graphNodeCount={0}
        evidenceCount={0}
      />,
    );
    expect(
      screen.getByText('当前数据源「empty.db」中没有可解析的责任数据'),
    ).toBeDefined();
  });
});
