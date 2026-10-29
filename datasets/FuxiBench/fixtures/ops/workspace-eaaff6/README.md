# notifications-workspace

Offline snapshot of the dashboard notification package. The legacy panel in
`src/components/NotificationPanel.tsx` is scheduled for refactoring; the agreed
specification is in `docs.md`.

Dependencies are not vendored in this snapshot. The only runnable tool is the
dependency-free structural checker:

```bash
node scripts/check-component.mjs /tmp/refactored-component.tsx
```

Do not run `npm install`; this snapshot is intentionally offline.
