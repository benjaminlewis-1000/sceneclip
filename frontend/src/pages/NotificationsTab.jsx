// Full notification list backing the nav badge in App.jsx.
import React, { useEffect, useState } from "react";
import { api } from "../api/client.js";

export default function NotificationsTab() {
  const [notifications, setNotifications] = useState([]);

  const refresh = () => api.listNotifications().then(setNotifications);

  useEffect(() => {
    refresh();
  }, []);

  const markRead = async (id) => {
    await api.markNotificationRead(id);
    refresh();
  };

  return (
    <div>
      <h1>Notifications</h1>
      <ul className="notification-list">
        {notifications.map((n) => (
          <li key={n.id} className={n.read ? "read" : "unread"}>
            <span>{n.message}</span>
            <span className="notif-time">{new Date(n.created_at).toLocaleString()}</span>
            {!n.read && <button onClick={() => markRead(n.id)}>Dismiss</button>}
          </li>
        ))}
        {notifications.length === 0 && <li>No notifications yet.</li>}
      </ul>
    </div>
  );
}
