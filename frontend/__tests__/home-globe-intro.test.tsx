// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import HomeGlobeIntro from '@/components/ui/home-globe-intro';

function mockReducedMotion(matches: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches,
      media: '(prefers-reduced-motion: reduce)',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

describe('HomeGlobeIntro', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockReducedMotion(false);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('renders the intro iframe shell', () => {
    render(<HomeGlobeIntro src="/globe/index.html?embed=intro" onComplete={vi.fn()} />);

    expect(screen.getByTitle('MtAgentLens 开场地球')).toBeDefined();
    expect(screen.getByLabelText('MtAgentLens 开场序章')).toBeDefined();
  });

  it('auto-completes after the production startup budget', async () => {
    const onComplete = vi.fn();
    render(
      <HomeGlobeIntro
        src="/globe/index.html?embed=intro"
        onComplete={onComplete}
        exitDurationMs={200}
        maxDisplayMs={900}
      />,
    );

    fireEvent.load(screen.getByTitle('MtAgentLens 开场地球'));
    await vi.advanceTimersByTimeAsync(899);
    expect(onComplete).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(201);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('lets users skip the intro explicitly', async () => {
    const onComplete = vi.fn();
    render(
      <HomeGlobeIntro
        src="/globe/index.html?embed=intro"
        onComplete={onComplete}
        exitDurationMs={120}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '跳过开场序章' }));

    await vi.advanceTimersByTimeAsync(119);
    expect(onComplete).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('respects reduced motion by bypassing the cinematic intro', async () => {
    mockReducedMotion(true);
    const onComplete = vi.fn();
    render(
      <HomeGlobeIntro
        src="/globe/index.html?embed=intro"
        onComplete={onComplete}
        exitDurationMs={80}
      />,
    );

    await vi.advanceTimersByTimeAsync(79);
    expect(onComplete).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('completes when intro requests entering main stage', async () => {
    const onComplete = vi.fn();
    render(
      <HomeGlobeIntro
        src="/globe/index.html?embed=intro"
        onComplete={onComplete}
        exitDurationMs={120}
      />,
    );

    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'mtagentlens:intro-enter-main-stage' },
      }),
    );

    await vi.advanceTimersByTimeAsync(119);
    expect(onComplete).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('completes when intro requests direct stage access', async () => {
    const onComplete = vi.fn();
    render(
      <HomeGlobeIntro
        src="/globe/index.html?embed=intro"
        onComplete={onComplete}
        exitDurationMs={120}
      />,
    );

    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'mtagentlens:intro-skip' },
      }),
    );

    await vi.advanceTimersByTimeAsync(119);
    expect(onComplete).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });
});
