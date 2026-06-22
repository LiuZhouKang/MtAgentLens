'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { Sun, Moon, Monitor } from 'lucide-react';
import {
  THEME_STORAGE_KEY,
  applyThemeToDocument,
  readStoredTheme,
  resolveEffectiveTheme,
  type ThemePreference,
} from '@/lib/theme';

export interface ThemeToggleProps {
  initialTheme?: ThemePreference;
  onChange?: (theme: 'light' | 'dark') => void;
}

const THEME_CYCLE: ThemePreference[] = ['light', 'dark', 'system'];

export default function ThemeToggle({ initialTheme, onChange }: ThemeToggleProps) {
  const [theme, setTheme] = useState<ThemePreference>(initialTheme ?? 'dark');
  const [mounted, setMounted] = useState(false);
  const [loading, setLoading] = useState(false);
  const mqlRef = useRef<MediaQueryList | null>(null);
  const systemChangeRef = useRef<((e: MediaQueryListEvent) => void) | null>(null);

  useEffect(() => {
    setMounted(true);
    const resolved = readStoredTheme(window.localStorage);
    setTheme(resolved);
    const effective = resolveEffectiveTheme(
      resolved,
      window.matchMedia('(prefers-color-scheme: dark)').matches,
    );
    applyThemeToDocument(document, effective);
  }, []);

  useEffect(() => {
    if (theme !== 'system') {
      if (mqlRef.current && systemChangeRef.current) {
        mqlRef.current.removeEventListener('change', systemChangeRef.current);
      }
      return;
    }

    const mql = window.matchMedia('(prefers-color-scheme: dark)');
    mqlRef.current = mql;

    const handleSystemChange = (event: MediaQueryListEvent) => {
      const effective = resolveEffectiveTheme('system', event.matches);
      applyThemeToDocument(document, effective);
      onChange?.(effective);
    };
    systemChangeRef.current = handleSystemChange;

    mql.addEventListener('change', handleSystemChange);
    return () => mql.removeEventListener('change', handleSystemChange);
  }, [theme, onChange]);

  const handleToggle = useCallback(() => {
    setLoading(true);
    const nextIndex = (THEME_CYCLE.indexOf(theme) + 1) % THEME_CYCLE.length;
    const nextTheme = THEME_CYCLE[nextIndex];

    localStorage.setItem(THEME_STORAGE_KEY, nextTheme);

    const effective = resolveEffectiveTheme(
      nextTheme,
      window.matchMedia('(prefers-color-scheme: dark)').matches,
    );
    applyThemeToDocument(document, effective);

    setTheme(nextTheme);
    setLoading(false);
    onChange?.(effective);
  }, [theme, onChange]);

  const icons: Record<ThemePreference, React.ReactNode> = {
    light: <Sun size={14} />,
    dark: <Moon size={14} />,
    system: <Monitor size={14} />,
  };

  const labels: Record<ThemePreference, string> = {
    light: '浅色模式',
    dark: '深色模式',
    system: '跟随系统',
  };
  const nextTheme = THEME_CYCLE[(THEME_CYCLE.indexOf(theme) + 1) % THEME_CYCLE.length];
  const fallbackTheme = initialTheme ?? 'dark';
  const fallbackNextTheme = THEME_CYCLE[(THEME_CYCLE.indexOf(fallbackTheme) + 1) % THEME_CYCLE.length];

  if (!mounted) {
    return (
      <button
        type="button"
        data-testid="theme-toggle"
        disabled
        aria-label={`当前主题: ${labels[fallbackTheme]}。点击切换到${labels[fallbackNextTheme]}。`}
        aria-live="polite"
        title={`当前: ${labels[fallbackTheme]}，下一个: ${labels[fallbackNextTheme]}`}
        className="prism-button prism-button--ghost"
        style={{
          minHeight: 36,
          padding: '8px 12px',
          fontSize: 11,
          opacity: 0.56,
          cursor: 'wait',
        }}
      >
        {icons[fallbackTheme]}
        <span className="hidden sm:inline">{labels[fallbackTheme]}</span>
      </button>
    );
  }

  return (
    <button
      type="button"
      data-testid="theme-toggle"
      onClick={handleToggle}
      disabled={loading}
      aria-label={`当前主题: ${labels[theme]}。点击切换到${labels[nextTheme]}。`}
      aria-live="polite"
      title={`当前: ${labels[theme]}，下一个: ${labels[nextTheme]}`}
      className="prism-button prism-button--ghost"
      style={{
        minHeight: 36,
        padding: '8px 12px',
        fontSize: 11,
        opacity: loading ? 0.56 : 1,
        cursor: loading ? 'wait' : 'pointer',
      }}
    >
      {icons[theme]}
      <span className="hidden sm:inline">{labels[theme]}</span>
    </button>
  );
}
