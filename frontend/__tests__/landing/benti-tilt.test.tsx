// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { BentoTilt } from '@/components/landing/benti-tilt';

function mockRect(card: HTMLElement, w: number, h: number) {
  card.getBoundingClientRect = () =>
    ({
      left: 0,
      top: 0,
      width: w,
      height: h,
      right: w,
      bottom: h,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }) as DOMRect;
}

function setReducedMotion(matches: boolean) {
  window.matchMedia = ((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

afterEach(() => cleanup());

describe('BentoTilt — 3D 鼠标倾斜卡片', () => {
  it('renders its children', () => {
    render(<BentoTilt data-testid="card">content</BentoTilt>);
    expect(screen.getByTestId('card').textContent).toBe('content');
  });

  it('tilts toward the cursor (right of center → positive rotateY)', () => {
    render(<BentoTilt data-testid="card">content</BentoTilt>);
    const card = screen.getByTestId('card');
    mockRect(card, 100, 100);

    fireEvent.mouseMove(card, { clientX: 75, clientY: 50 });
    expect(card.style.transform).toContain('rotateY(5deg)');
    expect(card.style.transform).toContain('rotateX(0deg)');
    expect(card.style.transform).toContain('scale(1.02)');
  });

  it('clamps to max tilt at the top-left corner', () => {
    render(<BentoTilt data-testid="card">content</BentoTilt>);
    const card = screen.getByTestId('card');
    mockRect(card, 100, 100);

    fireEvent.mouseMove(card, { clientX: 0, clientY: 0 });
    expect(card.style.transform).toContain('rotateX(10deg)');
    expect(card.style.transform).toContain('rotateY(-10deg)');
  });

  it('injects spotlight CSS variables at the cursor position', () => {
    render(<BentoTilt data-testid="card">content</BentoTilt>);
    const card = screen.getByTestId('card');
    mockRect(card, 100, 100);

    fireEvent.mouseMove(card, { clientX: 75, clientY: 50 });
    // 供 .landing-pipeline__card::after 的 radial-gradient(… at var(--spot-x) var(--spot-y)) 消费
    expect(card.style.getPropertyValue('--spot-x')).toBe('75px');
    expect(card.style.getPropertyValue('--spot-y')).toBe('50px');
  });

  it('resets the transform on mouse leave', () => {
    render(<BentoTilt data-testid="card">content</BentoTilt>);
    const card = screen.getByTestId('card');
    mockRect(card, 100, 100);

    fireEvent.mouseMove(card, { clientX: 75, clientY: 50 });
    expect(card.style.transform).not.toBe('');
    fireEvent.mouseLeave(card);
    expect(card.style.transform).toBe('');
  });

  it('stays static under prefers-reduced-motion', () => {
    setReducedMotion(true);
    render(<BentoTilt data-testid="card">content</BentoTilt>);
    const card = screen.getByTestId('card');
    mockRect(card, 100, 100);

    fireEvent.mouseMove(card, { clientX: 75, clientY: 50 });
    expect(card.style.transform).toBe('');
  });
});
