import type { NotificationSeverity } from "../types"

// Severity colours are themed through CSS custom properties.
export const SEVERITY_TOKENS: Record<NotificationSeverity, string> = {
  info: "var(--notif-info)",
  warning: "var(--notif-warning)",
  critical: "var(--notif-critical)",
}
