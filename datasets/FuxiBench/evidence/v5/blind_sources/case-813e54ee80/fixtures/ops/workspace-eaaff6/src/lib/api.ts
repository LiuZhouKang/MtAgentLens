import type { Notification } from "../types"

// Shared request contract for the notification service.
export async function fetchNotifications(
  endpoint: string,
  signal: AbortSignal,
): Promise<Notification[]> {
  const response = await fetch(`${endpoint}/notifications`, { signal })
  if (!response.ok) {
    throw new Error(`Request failed with ${response.status}`)
  }
  return (await response.json()) as Notification[]
}
