// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import WhyAttribution, { WhyFacetEmptyState } from '@/components/investigation/why-attribution';
import { EMPTY_WHY } from '@/lib/why-facets';

afterEach(() => {
  cleanup();
});

describe('WhyAttribution', () => {
  const why = { ...EMPTY_WHY, facetCounts: { pollution: 2, policy: 0, diagnostic: 1 } };
  const onFacetChange = vi.fn();

  it('0 命中的分面 chip 禁用；生效 chip aria-pressed；折叠钮 aria-controls', () => {
    render(
      <WhyAttribution
        why={why}
        facet="all"
        onFacetChange={onFacetChange}
        alertCount={5}
        dbPresent
      />,
    );
    const pollution = screen.getByRole('button', { name: /污染 2/ });
    const policy = screen.getByRole('button', { name: /违规 0/ });
    expect(pollution).toBeEnabled();
    expect(policy).toBeDisabled();
    expect(pollution).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: /归因/ })).toHaveAttribute(
      'aria-controls',
      'why-attribution-body',
    );
  });

  it('生效分面 chip aria-pressed=true 且可一键清除', () => {
    render(
      <WhyAttribution
        why={why}
        facet="pollution"
        onFacetChange={onFacetChange}
        alertCount={5}
        dbPresent
      />,
    );
    const active = screen.getByRole('button', { name: /污染 2/ });
    expect(active).toHaveAttribute('aria-pressed', 'true');
    const clear = screen.getByRole('button', { name: /清除分面/ });
    clear.click();
    expect(onFacetChange).toHaveBeenCalledWith('all');
  });

  it('dbPresent=false 时 chips 与折叠钮禁用且显示导入提示', () => {
    render(
      <WhyAttribution
        why={EMPTY_WHY}
        facet="all"
        onFacetChange={onFacetChange}
        alertCount={0}
        dbPresent={false}
      />,
    );
    expect(screen.getByText(/导入 responsibility\.db 后启用/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /归因/ })).toBeDisabled();
  });
});

describe('WhyFacetEmptyState', () => {
  it('显示被过滤数与清除按钮', () => {
    const onClear = vi.fn();
    render(<WhyFacetEmptyState facet="pollution" filteredCount={3} onClear={onClear} />);
    expect(screen.getByText(/该分面无告警命中/)).toBeTruthy();
    expect(screen.getByText(/已过滤 3 条告警/)).toBeTruthy();
    screen.getByRole('button', { name: /清除分面/ }).click();
    expect(onClear).toHaveBeenCalled();
  });
});
