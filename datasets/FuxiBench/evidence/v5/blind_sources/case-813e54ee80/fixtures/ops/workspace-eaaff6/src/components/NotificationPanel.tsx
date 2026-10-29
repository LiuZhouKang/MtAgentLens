import { useEffect, useState } from "react"

// Legacy prototype panel. This file is the refactoring target; it is kept as-is
// for reference and is not imported by the checker.
type Notification = any

export function NotificationPanel(props: any) {
  const [items, setItems] = useState<Notification[]>([])
  const [status, setStatus] = useState("loading")
  const [filter, setFilter] = useState("all")

  useEffect(() => {
    fetch(`${import.meta.env.VITE_NOTIFICATION_ENDPOINT}/notifications`)
      .then((res) => res.json())
      .then((data) => {
        setItems(data)
        setStatus("ready")
      })
      .catch(() => setStatus("error"))
    // no cleanup: late responses still write state after unmount
  }, [])

  let unread = 0
  for (const item of items) {
    if (!item.read) unread += 1
  }

  const visible = items.filter((item: any) => filter === "all" || item.kind === filter)

  return (
    <div className="notification-panel">
      <h2>Notifications</h2>
      <p>{unread} unread</p>
      {status === "loading" && <p>Loading…</p>}
      {status === "error" && <p>Could not load notifications.</p>}
      <div>
        {["all", "alert", "mention", "system"].map((value) => (
          <button key={value} onClick={() => setFilter(value)}>
            {value}
          </button>
        ))}
      </div>
      <ul>
        {visible.map((item, index) => (
          <li key={index} onClick={() => props.onSelect?.(item)}>
            <span
              style={{
                color:
                  item.severity === "critical"
                    ? "red"
                    : item.severity === "warning"
                      ? "orange"
                      : "blue",
              }}
            >
              {item.title}
            </span>
            <small>{new Date(item.createdAt).toLocaleString()}</small>
          </li>
        ))}
      </ul>
    </div>
  )
}
