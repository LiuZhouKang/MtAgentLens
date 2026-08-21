export type ThemePreference = 'light' | 'dark' | 'system';
export type EffectiveTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'app-theme';
const VALID_THEMES: ThemePreference[] = ['light', 'dark', 'system'];

export function isThemePreference(value: string | null): value is ThemePreference {
  return value != null && VALID_THEMES.includes(value as ThemePreference);
}

export function readStoredTheme(
  storage: Pick<Storage, 'getItem'> | null | undefined,
): ThemePreference {
  const raw = storage?.getItem(THEME_STORAGE_KEY) ?? null;
  return isThemePreference(raw) ? raw : 'dark';
}

export function resolveEffectiveTheme(
  theme: ThemePreference,
  prefersDark: boolean,
): EffectiveTheme {
  if (theme === 'system') {
    return prefersDark ? 'dark' : 'light';
  }

  return theme;
}

export function applyThemeToDocument(doc: Document, effective: EffectiveTheme) {
  const root = doc.documentElement;
  root.classList.toggle('dark', effective === 'dark');
  root.dataset.theme = effective;
  root.style.colorScheme = effective;
}

export function getThemeBootstrapScript() {
  return `
    (function () {
      try {
        var key = '${THEME_STORAGE_KEY}';
        var stored = localStorage.getItem(key);
        var theme = stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'dark';
        var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
        var effective = theme === 'system' ? (prefersDark ? 'dark' : 'light') : theme;
        var root = document.documentElement;
        if (effective === 'dark') {
          root.classList.add('dark');
        } else {
          root.classList.remove('dark');
        }
        root.dataset.theme = effective;
        root.style.colorScheme = effective;
      } catch (error) {
        document.documentElement.classList.add('dark');
        document.documentElement.dataset.theme = 'dark';
        document.documentElement.style.colorScheme = 'dark';
      }
    })();
  `;
}
