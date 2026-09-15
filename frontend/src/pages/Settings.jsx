// Settings: danger zone ("clear the database") plus a live view of
// everything the Celery worker is currently detecting or encoding, so a
// big backlog (see VideoList's "Queued" vs "Detecting" split) can be
// checked in one place instead of hunting through individual video cards.
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";

const CONFIRM_PHRASE = "CLEAR";
const QUEUE_POLL_MS = 4000;

export default function Settings() {
  const [confirmText, setConfirmText] = useState("");
  const [status, setStatus] = useState(null); // null | "clearing" | {deleted}
  const [queue, setQueue] = useState({ detection: [], encoding: [] });

  const clearDatabase = async () => {
    setStatus("clearing");
    const result = await api.clearDatabase();
    setStatus(result);
    setConfirmText("");
  };

  useEffect(() => {
    let cancelled = false;
    const refresh = () => api.taskQueue().then((q) => !cancelled && setQueue(q));
    refresh();
    const interval = setInterval(refresh, QUEUE_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div>
      <h1>Settings</h1>

      <section>
        <h2>Task queue</h2>
        <h3>Detection</h3>
        <table className="scene-table">
          <thead>
            <tr>
              <th>Video</th>
              <th>Status</th>
              <th>Progress</th>
            </tr>
          </thead>
          <tbody>
            {queue.detection.map((r) => (
              <tr key={r.run_id}>
                <td>
                  <Link to={`/videos/${r.video_id}`}>{r.video_path}</Link>
                </td>
                <td>{r.status === "running" ? "Detecting" : "Queued"}</td>
                <td>{r.status === "running" ? `${r.progress_percent}%` : "--"}</td>
              </tr>
            ))}
            {queue.detection.length === 0 && (
              <tr>
                <td colSpan="3">Nothing detecting or queued.</td>
              </tr>
            )}
          </tbody>
        </table>

        <h3>Encoding</h3>
        <table className="scene-table">
          <thead>
            <tr>
              <th>Video</th>
              <th>Scene</th>
              <th>Status</th>
              <th>Progress</th>
            </tr>
          </thead>
          <tbody>
            {queue.encoding.map((s) => (
              <tr key={s.scene_id}>
                <td>
                  <Link to={`/videos/${s.video_id}`}>{s.video_path}</Link>
                </td>
                <td>
                  {formatTime(s.start_seconds)}-{formatTime(s.end_seconds)}
                </td>
                <td>{s.status === "encoding" ? "Encoding" : "Queued"}</td>
                <td>{s.status === "encoding" ? `${s.progress_percent}%` : "--"}</td>
              </tr>
            ))}
            {queue.encoding.length === 0 && (
              <tr>
                <td colSpan="4">Nothing encoding or queued.</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="danger-zone">
        <h2>Danger zone</h2>
        <p>
          Clears every video, detection run, boundary, scene, and notification from the database.
          Source files on disk are untouched -- this just resets the library back to "nothing
          scanned yet," useful for re-testing the directory scan from a clean slate. This cannot
          be undone.
        </p>
        <label className="danger-confirm-label">
          Type <code>{CONFIRM_PHRASE}</code> to enable the button
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder={CONFIRM_PHRASE}
          />
        </label>
        <button
          className="danger-button"
          disabled={confirmText !== CONFIRM_PHRASE || status === "clearing"}
          onClick={clearDatabase}
        >
          {status === "clearing" ? "Clearing..." : "Clear database"}
        </button>
        {status && status !== "clearing" && (
          <p className="danger-result">Deleted {status.deleted} video(s) and everything derived from them.</p>
        )}
      </section>
    </div>
  );
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
