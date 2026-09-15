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

  const clearOne = async (id) => {
    await api.deleteNotification(id);
    refresh();
  };

  const clearAll = async () => {
    await api.clearAllNotifications();
    refresh();
  };

  return (
    <div>
      <div className="notifications-header">
        <h1>Notifications</h1>
        {notifications.length > 0 && <button onClick={clearAll}>Clear all</button>}
      </div>
      <ul className="notification-list">
        {notifications.map((n) => (
          <li key={n.id} className={n.read ? "read" : "unread"}>
            <span>{n.message}</span>
            <span className="notif-time">{new Date(n.created_at).toLocaleString()}</span>
            {!n.read && <button onClick={() => markRead(n.id)}>Dismiss</button>}
            <button onClick={() => clearOne(n.id)}>Clear</button>
          </li>
        ))}
        {notifications.length === 0 && <li>No notifications yet.</li>}
      </ul>
    </div>
  );
}
