// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ThemeToggle from '@/components/ui/theme-toggle';
import {
  THEME_STORAGE_KEY,
  applyThemeToDocument,
  readStoredTheme,
  resolveEffectiveTheme,
} from '@/lib/theme';

type MockMql = MediaQueryList & {
  __emitChange: (matches: boolean) => void;
};

function createMockMatchMedia(initialMatches = false) {
  let listener: ((event: MediaQueryListEvent) => void) | null = null;

  const mql: MockMql = {
    matches: initialMatches,
    media: '(prefers-color-scheme: dark)',
    onchange: null,
    addEventListener: vi.fn((_type, cb: EventListenerOrEventListenerObject) => {
      listener = cb as (event: MediaQueryListEvent) => void;
    }),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
    __emitChange(matches: boolean) {
      Object.defineProperty(this, 'matches', { value: matches, configurable: true });
      listener?.({ matches } as MediaQueryListEvent);
    },
  };

  vi.stubGlobal('matchMedia', vi.fn(() => mql));
  return mql;
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  document.documentElement.className = 'dark';
  document.documentElement.dataset.theme = 'dark';
  document.documentElement.style.colorScheme = 'dark';
});

describe('theme utilities', () => {
  it('reads stored theme safely', () => {
    expect(readStoredTheme(window.localStorage)).toBe('dark');
    localStorage.setItem(THEME_STORAGE_KEY, 'light');
    expect(readStoredTheme(window.localStorage)).toBe('light');
    localStorage.setItem(THEME_STORAGE_KEY, 'invalid');
    expect(readStoredTheme(window.localStorage)).toBe('dark');
  });

  it('resolves effective theme from preference and system state', () => {
    expect(resolveEffectiveTheme('dark', false)).toBe('dark');
    expect(resolveEffectiveTheme('light', true)).toBe('light');
    expect(resolveEffectiveTheme('system', true)).toBe('dark');
    expect(resolveEffectiveTheme('system', false)).toBe('light');
  });

  it('applies theme class and dataset to html root', () => {
    applyThemeToDocument(document, 'light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(document.documentElement.style.colorScheme).toBe('light');
  });
});

describe('ThemeToggle', () => {
  it('renders with data-testid contract', async () => {
    createMockMatchMedia(false);
    render(<ThemeToggle />);

    expect(await screen.findByTestId('theme-toggle')).toBeDefined();
  });

  it('cycles light -> dark -> system -> light and persists value', async () => {
    createMockMatchMedia(false);
    localStorage.setItem(THEME_STORAGE_KEY, 'light');
    const user = userEvent.setup();
    render(<ThemeToggle />);

    const toggle = await screen.findByTestId('theme-toggle');
    await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));

    await user.click(toggle);
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');

    await user.click(toggle);
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('system');
    expect(document.documentElement.classList.contains('dark')).toBe(false);

    await user.click(toggle);
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('reacts to system theme changes when in system mode', async () => {
    const mql = createMockMatchMedia(false);
    localStorage.setItem(THEME_STORAGE_KEY, 'system');
    const onChange = vi.fn();

    render(<ThemeToggle onChange={onChange} />);
    await screen.findByTestId('theme-toggle');

    mql.__emitChange(true);
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(onChange).toHaveBeenCalledWith('dark');
  });

  it('supports keyboard interaction', async () => {
    createMockMatchMedia(false);
    localStorage.setItem(THEME_STORAGE_KEY, 'light');
    const user = userEvent.setup();

    render(<ThemeToggle />);
    const toggle = await screen.findByTestId('theme-toggle');
    toggle.focus();
    await user.keyboard('{Enter}');

    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });
});
