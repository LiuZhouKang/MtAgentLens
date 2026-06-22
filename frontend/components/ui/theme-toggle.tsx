'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { Sun, Moon, Monitor } from 'lucide-react';

export interface ThemeToggleProps {
  initialTheme?: 'light' | 'dark' | 'system';
  onChange?: (theme: 'light' | 'dark') => void;
}

type Theme = 'light' | 'dark' | 'system';

const THEME_KEY = 'app-theme';
const THEME_CYCLE: Theme[] = ['light', 'dark', 'system'];

/** Apply or remove `dark` class on <html> based on effective theme */
function applyTheme(effective: 'light' | 'dark') {
  if (effective === 'dark') {
    document.documentElement.classList.add('dark');
  } else {
    document.documentElement.classList.remove('dark');
  }
}

/** Resolve 'system' to 'light'|'dark' using prefers-color-scheme */
function resolveSystem(): 'light' | 'dark' {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export default function ThemeToggle({ initialTheme, onChange }: ThemeToggleProps) {
  const [theme, setTheme] = useState<Theme>(initialTheme ?? 'system');
  const [loading, setLoading] = useState(true);
  const mqlRef = useRef<MediaQueryList | null>(null);

  // ── On mount: read localStorage, fall back to system preference ──
  useEffect(() => {
    const localTheme = localStorage.getItem(THEME_KEY) as Theme | null;

    // Use localStorage or navigator preference
    const resolved = localTheme ?? 'system';
    setTheme(resolved as Theme);
    const effective = resolved === 'system' ? resolveSystem() : resolved;
    applyTheme(effective);
    setLoading(false);
  }, []);

  // ── Listen for prefers-color-scheme changes when in 'system' mode ──
  useEffect(() => {
    if (theme !== 'system') {
      mqlRef.current?.removeEventListener('change', handleSystemChange);
      return;
    }

    const mql = window.matchMedia('(prefers-color-scheme: dark)');
    mqlRef.current = mql;

    function handleSystemChange(e: MediaQueryListEvent) {
      applyTheme(e.matches ? 'dark' : 'light');
    }

    mql.addEventListener('change', handleSystemChange);
    return () => mql.removeEventListener('change', handleSystemChange);
  }, [theme]);

  // ── Toggle handler: cycle light → dark → system → light ──
  const handleToggle = useCallback(() => {
    setLoading(true);
    const nextIndex = (THEME_CYCLE.indexOf(theme) + 1) % THEME_CYCLE.length;
    const nextTheme = THEME_CYCLE[nextIndex];

    // Persist to localStorage
    localStorage.setItem(THEME_KEY, nextTheme);

    // Apply to DOM
    const effective = nextTheme === 'system' ? resolveSystem() : nextTheme;
    applyTheme(effective);

    setTheme(nextTheme);
    setLoading(false);

    // Notify parent if onChange provided
    if (onChange) {
      onChange(effective);
    }
  }, [theme, onChange]);

  // ── Icons for each theme state ──
  const icons: Record<Theme, React.ReactNode> = {
    light: <Sun size={14} />,
    dark: <Moon size={14} />,
    system: <Monitor size={14} />,
  };

  const labels: Record<Theme, string> = {
    light: '浅色模式',
    dark: '深色模式',
    system: '跟随系统',
  };

  return (
    <button
      data-testid="theme-toggle"
      onClick={handleToggle}
      disabled={loading}
      aria-label={`当前主题: ${labels[theme]}。点击切换`}
      title={`当前: ${labels[theme]}`}
      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium
                 text-gray-600 hover:text-gray-900 dark:text-white/60 dark:hover:text-white/90
                 border border-gray-200 hover:border-gray-400 dark:border-white/[0.06] dark:hover:border-white/[0.15]
                 bg-transparent hover:bg-gray-100 dark:hover:bg-white/[0.04] transition-all duration-150
                 disabled:opacity-50 disabled:cursor-wait cursor-pointer"
    >
      {icons[theme]}
      <span className="hidden sm:inline">{labels[theme]}</span>
    </button>
  );
}
