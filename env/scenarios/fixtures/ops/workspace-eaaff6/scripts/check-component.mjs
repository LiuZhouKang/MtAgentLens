#!/usr/bin/env node
// Dependency-free structural acceptance checker for the notification panel refactor.
// Usage: node scripts/check-component.mjs [output-file]
// It inspects source text only; it never executes the component or performs I/O
// beyond reading the target file.
import { existsSync, readFileSync } from "node:fs"

const target = process.argv[2] ?? "/tmp/refactored-component.tsx"

if (!existsSync(target)) {
  console.error(`FAIL file-exists: ${target} does not exist`)
  console.error("0 checks passed, 1 failed")
  process.exit(1)
}

const raw = readFileSync(target, "utf8")

function stripLineComments(line) {
  let quote = null
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i]
    if (quote) {
      if (ch === "\\") {
        i += 1
        continue
      }
      if (ch === quote) quote = null
      continue
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      quote = ch
      continue
    }
    if (ch === "/" && line[i + 1] === "/") return line.slice(0, i)
  }
  return line
}

const source = raw
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .split("\n")
  .map(stripLineComments)
  .join("\n")

const imports = [...source.matchAll(/from\s+["']([^"']+)["']/g)].map((m) => m[1])

const checks = [
  ["non-empty", raw.trim().length > 500, "write a complete component (the file is nearly empty)"],
  [
    "named-export",
    /export\s+(async\s+)?function\s+NotificationPanel\b|export\s+const\s+NotificationPanel\b/.test(source),
    "export NotificationPanel as a named export",
  ],
  [
    "imports-react-only",
    imports.length > 0 && imports.every((specifier) => specifier === "react"),
    "import hooks from \"react\" and nothing else",
  ],
  [
    "no-any",
    !/(:\s*any\b|\bas\s+any\b|<any>)/.test(source),
    "remove every `any` type usage",
  ],
  [
    "no-console-log",
    !/console\s*\.\s*log\s*\(/.test(source),
    "remove console.log calls",
  ],
  [
    "no-dangerous-html",
    !/dangerouslySetInnerHTML/.test(source),
    "do not use dangerouslySetInnerHTML",
  ],
  [
    "no-hardcoded-url",
    !/https?:\/\//.test(source),
    "do not hard-code a remote endpoint URL",
  ],
  [
    "notification-type",
    /(?:interface|type)\s+Notification\b/.test(source),
    "define the local Notification type",
  ],
  [
    "notification-filter-type",
    /(?:interface|type)\s+NotificationFilter\b/.test(source),
    "define NotificationFilter as \"all\" plus NotificationKind",
  ],
  [
    "panel-state-union",
    /(?:interface|type)\s+PanelState\b/.test(source) &&
      /status\s*:\s*["']loading["']/.test(source) &&
      /status\s*:\s*["']ready["']/.test(source) &&
      /status\s*:\s*["']error["']/.test(source),
    "define PanelState as the loading | ready | error discriminated union",
  ],
  [
    "props-type",
    /(?:interface|type)\s+NotificationPanelProps\b/.test(source) &&
      /:\s*NotificationPanelProps\b/.test(source),
    "define and use NotificationPanelProps",
  ],
  [
    "now-prop",
    /now\?\s*:\s*number/.test(source),
    "add the optional now?: number clock override",
  ],
  [
    "use-notifications-hook",
    /(?:export\s+)?function\s+useNotifications\b|const\s+useNotifications\b/.test(source),
    "extract an exported useNotifications hook",
  ],
  [
    "abort-controller",
    /new\s+AbortController\s*\(/.test(source) && /\.abort\s*\(/.test(source),
    "create an AbortController and call abort() in cleanup",
  ],
  [
    "abort-signal",
    /signal\s*[:,}]/.test(source),
    "pass the controller signal to fetch",
  ],
  [
    "use-memo",
    /\buseMemo\s*\(/.test(source),
    "derive unreadCount and visible with useMemo",
  ],
  [
    "sort-newest-first",
    /\.sort\s*\(/.test(source) && /createdAt/.test(source),
    "sort visible notifications by createdAt descending",
  ],
  [
    "format-relative-time",
    /(?:export\s+)?function\s+formatRelativeTime\b|const\s+formatRelativeTime\b/.test(source),
    "define formatRelativeTime",
  ],
  [
    "just-now-string",
    /["']just now["']/.test(source),
    "preserve the \"just now\" output for sub-minute ages",
  ],
  [
    "severity-styles",
    /SEVERITY_STYLES\b/.test(source),
    "define SEVERITY_STYLES as Record<NotificationSeverity, string>",
  ],
  [
    "severity-tokens",
    /--notif-info/.test(source) && /--notif-warning/.test(source) && /--notif-critical/.test(source),
    "use the var(--notif-*) severity tokens",
  ],
  [
    "region-label",
    /role\s*=\s*["']region["']/.test(source) && /aria-label\s*=\s*["']Notifications["']/.test(source),
    "wrap the panel in role=\"region\" aria-label=\"Notifications\"",
  ],
  [
    "live-region",
    /aria-live\s*=\s*["']polite["']/.test(source),
    "announce the unread count with aria-live=\"polite\"",
  ],
  [
    "filter-pressed",
    /aria-pressed\s*=/.test(source),
    "mark the active filter button with aria-pressed",
  ],
  [
    "button-type",
    /type\s*=\s*["']button["']/.test(source),
    "give notification and filter controls type=\"button\"",
  ],
  [
    "key-by-id",
    /key\s*=\s*\{\s*[A-Za-z_$][\w$]*\.id\s*\}/.test(source) &&
      !/key\s*=\s*\{\s*(?:index|i)\s*\}/.test(source),
    "key list items by item.id, never by array index",
  ],
  [
    "on-select",
    /onSelect\b/.test(source),
    "support the onSelect prop",
  ],
  [
    "error-alert",
    /role\s*=\s*["']alert["']/.test(source),
    "render the error message inside role=\"alert\"",
  ],
  [
    "empty-state",
    /No notifications/.test(source),
    "render the \"No notifications\" empty state",
  ],
  [
    "loading-state",
    /Loading/.test(source),
    "render the loading state",
  ],
  [
    "filter-labels",
    ["all", "alert", "mention", "system"].every((label) =>
      new RegExp(`["']${label}["']`).test(source),
    ),
    "expose the all / alert / mention / system filter labels",
  ],
  [
    "endpoint-env-name",
    /VITE_NOTIFICATION_ENDPOINT/.test(source),
    "keep the VITE_NOTIFICATION_ENDPOINT environment variable name",
  ],
]

let failed = 0

for (const [id, ok, hint] of checks) {
  if (ok) {
    console.log(`PASS ${id}`)
  } else {
    failed += 1
    console.error(`FAIL ${id}: ${hint}`)
  }
}

const passed = checks.length - failed
console.log(`\n${passed} checks passed, ${failed} failed`)

if (failed > 0) {
  console.error("Fix the failures above and run the checker again.")
  process.exit(1)
}

console.log("All structural checks passed.")
