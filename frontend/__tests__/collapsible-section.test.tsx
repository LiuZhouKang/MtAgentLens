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

  it('默认收起：内容隐藏（aria-expanded=false + 高度 0）', () => {
    render(
      <CollapsibleSection eyebrow="e" title="t">
        <div data-testid="body">内容</div>
      </CollapsibleSection>,
    );
    const btn = screen.getByRole('button');
    expect(btn.getAttribute('aria-expanded')).toBe('false');
    // 默认收起：内容未渲染
    expect(screen.queryByTestId('body')).toBeNull();
  });

  it('点击头部展开：内容可见 + aria-expanded=true', () => {
    render(
      <CollapsibleSection eyebrow="e" title="t">
        <div data-testid="body">内容</div>
      </CollapsibleSection>,
    );
    const btn = screen.getByRole('button');
    fireEvent.click(btn);
    expect(btn.getAttribute('aria-expanded')).toBe('true');
    // 再点收起
    fireEvent.click(btn);
    expect(btn.getAttribute('aria-expanded')).toBe('false');
  });

  it('Space 键激活展开（WAI-ARIA 按钮约定）', () => {
    render(
      <CollapsibleSection eyebrow="e" title="t">
        <div data-testid="body">内容</div>
      </CollapsibleSection>,
    );
    fireEvent.keyDown(screen.getByRole('button'), { key: ' ' });
    expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('true');
  });

  it('defaultOpen=true 默认展开', () => {
    render(
      <CollapsibleSection eyebrow="e" title="t" defaultOpen>
        <div data-testid="body">内容</div>
      </CollapsibleSection>,
    );
    expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('true');
  });
});
