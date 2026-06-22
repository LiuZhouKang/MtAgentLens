// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ObservatoryHeader from '@/components/system/observatory-header';

vi.mock('@/components/ui/theme-toggle', () => ({
  default: function MockThemeToggle() {
    return <button type="button">主题切换</button>;
  },
}));

describe('ObservatoryHeader', () => {
  it('renders back link, active nav item, stats and right slot', () => {
    render(
      <ObservatoryHeader
        eyebrow="L1 Cluster Observatory"
        title="把一个 Agent 集群为什么危险讲清楚"
        description="这一层承接 cluster 的 fan-out、边界漂移、证据密度与下一步入口。"
        activeSection="clusters"
        backHref="/overview"
        backLabel="返回总览"
        stats={['示例场景', '3 cluster entries']}
        rightSlot={<span>实时后端</span>}
      />,
    );

    expect(screen.getByRole('link', { name: '返回总览' }).getAttribute('href')).toBe('/overview');
    expect(screen.getByRole('link', { name: 'Clusters' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('navigation', { name: '观测层级导航' })).toBeDefined();
    expect(screen.getByText('示例场景')).toBeDefined();
    expect(screen.getByText('3 cluster entries')).toBeDefined();
    expect(screen.getByText('实时后端')).toBeDefined();
    expect(screen.getByRole('button', { name: '主题切换' })).toBeDefined();
  });
});
