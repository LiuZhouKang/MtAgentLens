const MINUTE = 60
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

// Relative time used across the dashboard. Keep the exact output strings.
export function formatRelativeTime(createdAt: number, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.floor((now - createdAt) / 1000))
  if (seconds < MINUTE) return "just now"
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)}m ago`
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)}h ago`
  return `${Math.floor(seconds / DAY)}d ago`
}
