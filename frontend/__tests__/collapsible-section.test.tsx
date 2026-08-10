// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { CollapsibleSection } from '@/components/ui/collapsible-section';

afterEach(() => {
  cleanup();
});

describe('CollapsibleSection —— 折叠分节', () => {
  it('渲染 eyebrow + title + meta', () => {
    render(
      <CollapsibleSection eyebrow="深度调查" title="身份血缘" meta="2 视图">
        <div>进程树</div>
      </CollapsibleSection>,
    );
    expect(screen.getByText('深度调查')).toBeTruthy();
    expect(screen.getByText('身份血缘')).toBeTruthy();
    expect(screen.getByText('2 视图')).toBeTruthy();
  });

  it('默认收起：内容不渲染，aria-expanded=false', () => {
    render(
      <CollapsibleSection eyebrow="e" title="t">
        <div data-testid="body">内容</div>
      </CollapsibleSection>,
    );
    expect(screen.queryByTestId('body')).toBeNull();
    expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('false');
  });

  it('点击头部展开：内容渲染 + aria-expanded=true', () => {
    render(
      <CollapsibleSection eyebrow="e" title="t">
        <div data-testid="body">内容</div>
      </CollapsibleSection>,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByTestId('body')).toBeTruthy();
    expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('true');
    // 再点收起
    fireEvent.click(screen.getByRole('button'));
    expect(screen.queryByTestId('body')).toBeNull();
  });

  it('Space 键激活展开（WAI-ARIA 按钮约定）', () => {
    render(
      <CollapsibleSection eyebrow="e" title="t">
        <div data-testid="body">内容</div>
      </CollapsibleSection>,
    );
    fireEvent.keyDown(screen.getByRole('button'), { key: ' ' });
    expect(screen.getByTestId('body')).toBeTruthy();
  });

  it('defaultOpen=true 默认展开', () => {
    render(
      <CollapsibleSection eyebrow="e" title="t" defaultOpen>
        <div data-testid="body">内容</div>
      </CollapsibleSection>,
    );
    expect(screen.getByTestId('body')).toBeTruthy();
  });
});
