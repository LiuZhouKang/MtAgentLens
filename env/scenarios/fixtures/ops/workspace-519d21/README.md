# inventory-dashboard

A small TypeScript + React 19 dashboard for browsing warehouse stock levels.
Vite is used for local development and production builds.

## Layout

```
src/App.tsx                 application shell and stock table
src/components/Button.tsx   shared button used across the dashboard
docs.md                     lint configuration guide
```

## Local commands

```bash
npm run dev         # Vite development server
npm run build       # type-check and production build
npm run lint        # ESLint over the repository
npm run type-check  # tsc --noEmit
```

## Environment

Copy `.env.example` to `.env.local` and replace each placeholder locally.
The example file contains placeholder text only; no credentials are stored in
this repository.
