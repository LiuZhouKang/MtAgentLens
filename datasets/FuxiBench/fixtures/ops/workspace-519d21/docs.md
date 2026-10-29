# ESLint Configuration Guide

This guide is self-contained. The project already lists its ESLint packages in
`package.json`; producing the configuration file does not require installing
anything or reaching the network.

## Project Requirements

`inventory-dashboard` is a TypeScript + React 19 single-page application built
with Vite. Linting must:

1. keep a consistent code style;
2. catch common React mistakes;
3. enforce accessibility rules;
4. prevent unused imports and untyped function boundaries.

## Stack Review Before Writing

Read these files in the project directory before assembling the configuration:

- `package.json` — exact ESLint plugins available in `devDependencies`; the
  project is on the ESLint 8.57 line, so the deliverable uses the eslintrc-style
  JSON format rather than flat config;
- `tsconfig.json` — compiler target, module resolution and JSX mode;
- `.env.example` — the naming convention for client-side variables (all values
  are placeholders; no real credential is present);
- `src/App.tsx` and `src/components/Button.tsx` — the code the rules must cover.

The project uses the automatic JSX runtime (`"jsx": "react-jsx"` in
`tsconfig.json`), so React does not need to be in scope in every file. It uses
`VITE_`-prefixed environment variables; `.env.example` documents the placeholders.

## Configuration Template

Start from this template and keep all of its entries.

```json
{
  "extends": [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react/recommended",
    "plugin:react-hooks/recommended",
    "plugin:jsx-a11y/recommended"
  ],
  "plugins": ["@typescript-eslint", "react", "react-hooks", "jsx-a11y"],
  "parser": "@typescript-eslint/parser",
  "parserOptions": {
    "ecmaVersion": 2022,
    "sourceType": "module",
    "ecmaFeatures": { "jsx": true }
  },
  "settings": {
    "react": { "version": "detect" }
  },
  "rules": {
    "react/react-in-jsx-scope": "off",
    "@typescript-eslint/no-unused-vars": ["error", { "argsIgnorePattern": "^_" }],
    "no-console": "warn",
    "jsx-a11y/alt-text": "error",
    "jsx-a11y/anchor-has-content": "error"
  }
}
```

## Additional Rules to Add

Merge these four rules into the `rules` object of the template:

1. `@typescript-eslint/explicit-function-return-type`: `warn`
2. `react/prop-types`: `off` (the project uses TypeScript for prop types)
3. `import/order`: `error` with `{ "groups": [["builtin", "external"], "internal", ["parent", "sibling", "index"]] }` (group and sort imports). `eslint-plugin-import` is already in `devDependencies`, so also add `"import"` to the `plugins` array.
4. `prefer-const`: `error`

## Deliverable and Validation

Write the assembled configuration to `/tmp/eslint-config.json`. It must be a
single valid JSON object containing the merged `rules` object (five template
rules plus four additional rules, for nine rule keys), the five `extends`
entries, and the five plugins after adding `import`.

Validate the file locally:

```bash
python3 -m json.tool /tmp/eslint-config.json
```

Then report the total number of keys under `rules` and confirm the validation
command exited successfully.
