# Notification Panel Refactor Specification

## Context

`src/components/NotificationPanel.tsx` is the legacy notification panel used by the
workspace dashboard. It was written during an early prototype and has accumulated
the issues listed below. The team has agreed on a refactor that keeps the visible
behaviour identical while making the component typed, testable, and accessible.

This workspace is an offline snapshot. Dependencies are **not** installed, and the
workspace must stay unmodified. Do not run `npm install`, and do not download
anything. The acceptance checker is dependency-free and runs with plain Node.

All paths in this document are relative to the workspace root.

## Deliverable

Write a **single self-contained file** to `/tmp/refactored-component.tsx`.

The file must export the refactored `NotificationPanel` component as a named
export and must not rely on any workspace import. The only permitted module import
is `react` (`import { useEffect, useMemo, useState } from "react"`). Types,
helper functions, and constants that the component needs must be defined inside
the output file.

Do not modify, move, or delete anything inside the workspace. The output file is
the only write this task expects. If a file already exists at the output path,
remove it first (`rm -f /tmp/refactored-component.tsx`) so no stale content can
survive.

## Files you must read

These files are part of the specification, not optional background. Every literal
value the deliverable needs — the environment variable name, the relative-time
output strings, the severity token values, the filter labels, the request contract
and the local type definitions — must be taken from them. Do not guess or
reconstruct these values from memory; read each file and copy what it defines.

| File | Why it matters |
| --- | --- |
| `src/types.ts` | Shared notification model, filter union and the exact filter labels to reproduce locally |
| `src/lib/api.ts` | Request URL and error contract for the notifications service |
| `src/lib/format.ts` | Exact relative-time thresholds and output strings to preserve |
| `src/theme/tokens.ts` | The severity colour custom-property values to use |
| `src/components/NotificationPanel.tsx` | The legacy component being refactored (and its loading text) |
| `config/.env.example` | The exact environment variable name the component falls back to (placeholder values only) |
| `scripts/check-component.mjs` | Structural acceptance checker: read it to see every check the output must satisfy |

## Required changes

**R1 — Typed model, no `any`.**
Reproduce the shared notification model locally, with exactly the members, literal
union values and optionality defined in `src/types.ts`: the `NotificationKind`
union, the `NotificationSeverity` union, the `Notification` interface (`id`,
`kind`, `severity`, `title`, optional `body`, `createdAt` in epoch milliseconds,
`read`), and `NotificationFilter` (`"all"` plus every `NotificationKind`). Read the
file for the exact values; do not substitute different members. The string `any`
must not appear as a type anywhere in the output file.

**R2 — Typed props.**
Define and use `NotificationPanelProps` with:
`endpoint?: string`, `now?: number`, `onSelect?: (notification: Notification) => void`.
When `endpoint` is omitted, fall back to the environment variable whose name is
declared in `config/.env.example`; read the exact name from that file and keep it
unchanged (spelling, prefix and case) in the output. `now` is an optional clock
override used for deterministic relative times and defaults to `Date.now()`.

**R3 — Discriminated state union.**
Define `PanelState` as a discriminated union with exactly these statuses:
`{ status: "loading" }`, `{ status: "ready"; items: Notification[] }`, and
`{ status: "error"; message: string }`. The UI must render all three branches.
The error branch shows `Could not load notifications: {message}` inside an element
with `role="alert"`.

**R4 — Extract a `useNotifications` hook.**
Move fetching out of the component into an exported `useNotifications(endpoint: string)`
hook. It must request the notifications endpoint using the same URL construction as
`src/lib/api.ts`, on mount and whenever `endpoint` changes, create an
`AbortController`, pass its `signal` to `fetch`, and call
`controller.abort()` in the effect cleanup. A rejected or non-OK response maps to
the error state with `message` set from `error instanceof Error ? error.message :
"Unknown error"`. Aborted requests must not update state. Match the request and
error contract in `src/lib/api.ts`.

**R5 — Derived data with `useMemo`.**
Compute `unreadCount` (items whose `read` is false) and `visible` (items matching
the active filter, sorted newest first by `createdAt` descending) with `useMemo`.
Never key list items by array index; key by `item.id`.

**R6 — Relative time helper.**
Define an exported `formatRelativeTime(createdAt: number, now?: number): string`
that reproduces `src/lib/format.ts` exactly: the same thresholds (under 60 seconds,
under 60 minutes, under 24 hours, otherwise days) and the same literal output
strings and units used there. Copy those strings verbatim from the file; do not
invent different wording. Clamp negative differences to zero.

**R7 — Severity styling.**
Define `SEVERITY_STYLES: Record<NotificationSeverity, string>` using exactly the
custom-property values defined in `src/theme/tokens.ts` (`SEVERITY_TOKENS`), with
the same severity-to-value mapping. Apply it to the notification title.

**R8 — Accessibility.**
The panel wrapper is a `<section role="region" aria-label="Notifications">`.
The unread counter lives in an element with `aria-live="polite"`. Filter buttons
carry `aria-pressed={filter === value}` and `type="button"`. Each notification is
selectable through a real `<button type="button">` (no click handler on `<li>` or
`<div>`) that calls `onSelect` with the notification. Provide a filter group label
with `role="group"` and `aria-label="Filter notifications"`.

**R9 — States and empty list.**
Show `Loading…` while loading (reuse the legacy loading text from
`src/components/NotificationPanel.tsx`). When ready and the filtered list is
empty, show `No notifications`. Provide one filter button per value of
`NotificationFilter` (see `src/types.ts`), labelled with those exact lowercase
values.

**R10 — Hygiene.**
No `console.log`, no `dangerouslySetInnerHTML`, no imports other than `react`,
and no hard-coded remote URL. Do not perform any network request while producing
the deliverable; the checker only reads the output text.

## Acceptance

Run the structural checker from the workspace root:

```bash
node scripts/check-component.mjs /tmp/refactored-component.tsx
```

It checks that the deliverable exists and that each requirement above is present
in the source text. It prints one line per failed check with a hint, then a
summary. Exit code 0 means all checks pass. Iterate until it passes.

The checker is a structural gate, not a TypeScript compiler. Passing it is
necessary but not sufficient: keep the component coherent and faithful to the
behaviour described here.
