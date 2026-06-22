// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import HomeGlobeIntro from '@/components/ui/home-globe-intro';

describe('HomeGlobeIntro', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('renders the intro iframe shell', () => {
    render(<HomeGlobeIntro src="/globe/index.html?embed=intro" onComplete={vi.fn()} />);

    expect(screen.getByTitle('MtAgentPrism 开场地球')).toBeDefined();
    expect(screen.getByLabelText('MtAgentPrism 开场序章')).toBeDefined();
  });

  it('does not auto-complete after iframe load and minimum duration', async () => {
    const onComplete = vi.fn();
    render(
      <HomeGlobeIntro
        src="/globe/index.html?embed=intro"
        onComplete={onComplete}
        minimumVisibleMs={1200}
        exitDurationMs={200}
      />,
    );

    fireEvent.load(screen.getByTitle('MtAgentPrism 开场地球'));
    await vi.advanceTimersByTimeAsync(3000);
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('completes when iframe asks to enter main stage', async () => {
    const onComplete = vi.fn();
    render(<HomeGlobeIntro src="/globe/index.html?embed=intro" onComplete={onComplete} exitDurationMs={120} />);

    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'agentsight:intro-enter-main-stage' },
      }),
    );

    await vi.advanceTimersByTimeAsync(119);
    expect(onComplete).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('completes when iframe asks to skip', async () => {
    const onComplete = vi.fn();
    render(<HomeGlobeIntro src="/globe/index.html?embed=intro" onComplete={onComplete} exitDurationMs={120} />);

    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'agentsight:intro-skip' },
      }),
    );

    await vi.advanceTimersByTimeAsync(119);
    expect(onComplete).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });
});
