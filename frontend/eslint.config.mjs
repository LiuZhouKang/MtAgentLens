import nextCoreWebVitals from 'eslint-config-next/core-web-vitals';
import nextTypeScript from 'eslint-config-next/typescript';
import eslintConfigPrettier from 'eslint-config-prettier';

const config = [
  ...nextCoreWebVitals,
  ...nextTypeScript,
  {
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-this-alias': 'off',
      'react-hooks/purity': 'off',
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/preserve-manual-memoization': 'off',
      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          varsIgnorePattern: '^_',
          argsIgnorePattern: '^_',
          destructuredArrayIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
    },
  },
  {
    ignores: [
      'code.jquery.com/**',
      'public/globe/**',
      'scripts/**',
      'fonts.googleapis.com/**',
      'index.html',
      'style.css',
      'select2.css',
      '.lighthouseci/**',
      '.trae/reports/eslint-report.json',
      'coverage/**',
      'test-results/**',
      'storybook-static/**',
      'out/**',
      '**/*.cjs', // legacy OSS capture/debug scripts (CommonJS)
    ],
  },
  {
    files: [
      'lib/*contract.ts',
      'components/ui/agent-*-dashboard.tsx',
      'components/ui/analyzer-dashboard.tsx',
      'components/ui/data-exfiltration-dashboard.tsx',
      'components/ui/dedup-diagnostics-panel.tsx',
      'components/ui/intent-verifier-dashboard.tsx',
      'components/ui/llm-api-*-dashboard.tsx',
      'components/ui/session-health-dashboard.tsx',
      'components/ui/sse-content-scanner-dashboard.tsx',
      'engine/**',
    ],
    rules: {
      '@typescript-eslint/no-unused-vars': 'off',
    },
  },
  eslintConfigPrettier,
];

export default config;
