// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { LandingCta } from '@/components/landing/landing-cta';
import { landingCopy } from '@/lib/landing/landing-copy';

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));

afterEach(() => {
  cleanup();
  push.mockClear();
});

describe('LandingCta — 世界入口 + 页脚', () => {
  it('renders the heading and stack line', () => {
    render(<LandingCta />);
    // 标题（h2）与按钮文案现统一为「进入观测台」——用 role 精确匹配标题，
    // 避免 getByText 同时命中按钮/按钮 hover 标签的 strict 冲突
    expect(screen.getByRole('heading', { name: landingCopy.cta.heading })).toBeTruthy();
    expect(screen.getByText(landingCopy.cta.stack)).toBeTruthy();
    expect(screen.getByText(landingCopy.cta.footer)).toBeTruthy();
  });

  it('navigates to the console on click', () => {
    render(<LandingCta />);
    fireEvent.click(screen.getByTestId('landing-cta'));
    expect(push).toHaveBeenCalledWith('/console');
  });

  it('rollover swaps the button label (data-hovered toggles)', () => {
    render(<LandingCta />);
    const btn = screen.getByTestId('landing-cta');
    expect(btn.getAttribute('data-hovered')).toBe('false');
    fireEvent.mouseEnter(btn);
    expect(btn.getAttribute('data-hovered')).toBe('true');
    fireEvent.mouseLeave(btn);
    expect(btn.getAttribute('data-hovered')).toBe('false');
  });
});
