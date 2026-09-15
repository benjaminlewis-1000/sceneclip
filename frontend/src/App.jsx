// Top-level shell: nav bar + routes, and the notification poller that
// drives the unread-count badge (the "notifications tab" from the plan).
import React, { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api/client.js";
import ReviewQueue from "./pages/ReviewQueue.jsx";
import TileGrid from "./pages/TileGrid.jsx";
import VideoList from "./pages/VideoList.jsx";
import VideoDetail from "./pages/VideoDetail.jsx";
import NotificationsTab from "./pages/NotificationsTab.jsx";
import Settings from "./pages/Settings.jsx";

const POLL_INTERVAL_MS = 5000;

export default function App() {
  const [unreadCount, setUnreadCount] = useState(0);

  useEffect(() => {
    // Polling, not SSE/websockets -- simplest thing that works for a
    // single-user tool where jobs take minutes, not seconds.
    let cancelled = false;
    const poll = async () => {
      try {
        const notifications = await api.listNotifications();
        if (!cancelled) {
          setUnreadCount(notifications.filter((n) => !n.read).length);
        }
      } catch {
        // transient network/auth hiccup -- next poll will retry
      }
    };
    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="app-shell">
      <nav className="app-nav">
        <span className="app-title">SceneClip</span>
        <NavLink to="/" end>Videos</NavLink>
        <NavLink to="/review">Review Queue</NavLink>
        <NavLink to="/grid">Unreviewed Grid</NavLink>
        <NavLink to="/notifications">
          Notifications{unreadCount > 0 ? ` (${unreadCount})` : ""}
        </NavLink>
        <NavLink to="/settings">Settings</NavLink>
        {/* Full Authelia SSO logout, not just this app's session -- see
        config/urls.py:logout_view. Plain <a>, not a fetch call, since the
        browser needs to actually follow the redirect chain out to Authelia
        and back. */}
        <a className="logout-link" href="/api/logout/">Log out</a>
      </nav>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<VideoList />} />
          <Route path="/videos/:videoId" element={<VideoDetail />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/grid" element={<TileGrid />} />
          <Route path="/notifications" element={<NotificationsTab />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
