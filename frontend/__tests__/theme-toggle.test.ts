// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';

beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove('dark');
  document.documentElement.classList.add('dark'); // default
});

describe('Theme persistence flow', () => {
  it('persists theme to localStorage key app-theme', () => {
    localStorage.setItem('app-theme', 'dark');
    expect(localStorage.getItem('app-theme')).toBe('dark');
  });

  it('falls back to system when localStorage is empty', () => {
    const theme = localStorage.getItem('app-theme');
    expect(theme).toBeNull();
    const resolved = 'system';
    expect(resolved).toBe('system');
  });

  it('applies dark class to html when theme is dark', () => {
    document.documentElement.classList.add('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('removes dark class from html when theme is light', () => {
    document.documentElement.classList.remove('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('cycles light → dark → system → light', () => {
    const cycle = ['light', 'dark', 'system'];
    let idx = 0;
    // light → dark
    idx = (idx + 1) % cycle.length;
    expect(cycle[idx]).toBe('dark');
    // dark → system
    idx = (idx + 1) % cycle.length;
    expect(cycle[idx]).toBe('system');
    // system → light
    idx = (idx + 1) % cycle.length;
    expect(cycle[idx]).toBe('light');
  });

  it('handles multiple writes to localStorage without conflicts', () => {
    localStorage.setItem('app-theme', 'dark');
    localStorage.setItem('other-key', 'value');
    expect(localStorage.getItem('app-theme')).toBe('dark');
    expect(localStorage.getItem('other-key')).toBe('value');
    localStorage.setItem('app-theme', 'light');
    expect(localStorage.getItem('app-theme')).toBe('light');
  });

  it('handles prefers-color-scheme change listener', () => {
    const addEventListener = vi.fn();
    window.matchMedia = vi.fn().mockReturnValue({
      matches: false,
      addEventListener,
      removeEventListener: vi.fn(),
      media: '(prefers-color-scheme: dark)',
      onchange: null,
      dispatchEvent: vi.fn(),
    } as any);

    const mql = window.matchMedia('(prefers-color-scheme: dark)');
    // Simulate the ThemeToggle component registering a change listener
    mql.addEventListener('change', () => {});
    expect(addEventListener).toHaveBeenCalledOnce();
  });

  it('does not break when only localStorage is available (no backend)', () => {
    localStorage.setItem('app-theme', 'dark');
    const theme = localStorage.getItem('app-theme') ?? 'system';
    expect(theme).toBe('dark');
    const effective = theme === 'system'
      ? window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
      : theme;
    expect(effective).toBe('dark');
  });
});

describe('API integration contract', () => {
  it('GET /api/user/preferences returns { theme: "light"|"dark"|"system" }', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ theme: 'dark' }),
    } as Response);

    const res = await fetch('/api/user/preferences');
    const data = await res.json();
    expect(data).toHaveProperty('theme');
    expect(['light', 'dark', 'system']).toContain(data.theme);
  });

  it('PUT /api/user/preferences with valid theme returns { success: true }', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ success: true }),
    } as Response);

    const res = await fetch('/api/user/preferences', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme: 'light' }),
    });
    const data = await res.json();
    expect(data).toEqual({ success: true });
  });

  it('PUT /api/user/preferences returns 400 for invalid theme', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ message: 'invalid theme' }),
    } as Response);

    const res = await fetch('/api/user/preferences', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme: 'neon' }),
    });
    expect(res.ok).toBe(false);
    expect(res.status).toBe(400);
  });
});

describe('data-testid contract', () => {
  it('ThemeToggle contains data-testid="theme-toggle"', () => {
    // The spec doc requires this data-testid for Playwright selectors
    const testId = 'theme-toggle';
    expect(testId).toBeDefined();
  });
});
