import nextCoreWebVitals from 'eslint-config-next/core-web-vitals';
import nextTypeScript from 'eslint-config-next/typescript';

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
      'out/**',
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
];

export default config;
