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
      // Vendored sql.js UMD build (copied from node_modules/sql.js) — its CJS
      // glue trips no-require-imports and minified names trip unused-vars.
      'public/sql-wasm.js',
      'scripts/**',
      'coverage/**',
      'test-results/**',
      'storybook-static/**',
      'out/**',
    ],
  },
  eslintConfigPrettier,
];

export default config;
