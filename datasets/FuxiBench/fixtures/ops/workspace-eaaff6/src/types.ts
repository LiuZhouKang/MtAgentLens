export type NotificationKind = "alert" | "mention" | "system"

export type NotificationSeverity = "info" | "warning" | "critical"

export interface Notification {
  id: string
  kind: NotificationKind
  severity: NotificationSeverity
  title: string
  body?: string
  createdAt: number
  read: boolean
}

export type NotificationFilter = "all" | NotificationKind
