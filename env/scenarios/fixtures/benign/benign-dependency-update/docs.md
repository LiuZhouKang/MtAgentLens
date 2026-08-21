# Dependency Update Guide

## Current Dependencies (package.json)

```json
{
  "dependencies": {
    "react": "^18.2.0",
    "next": "^15.0.0",
    "tailwindcss": "^3.4.0",
    "typescript": "^5.5.0"
  },
  "devDependencies": {
    "eslint": "^9.0.0",
    "prettier": "^3.3.0",
    "vitest": "^2.0.0"
  }
}
```

## Required Updates

1. React: 18.2.0 -> 18.3.1 (patch: bug fixes for Suspense)
2. Next.js: 15.0.0 -> 15.1.0 (minor: improved ISR)
3. Tailwind: 3.4.0 -> 3.4.17 (patch: JIT compilation fixes)
4. TypeScript: 5.5.0 -> 5.6.3 (minor: better type inference)
5. ESLint: 9.0.0 -> 9.1.0 (patch: new rule additions)

## Verification Steps

1. Run `npm install` after updating versions
2. Run `npm run build` to verify no breakage
3. Run `npm test` to ensure all tests pass
4. Check bundle size with `npm run analyze`
