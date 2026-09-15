import React, { useEffect, useState } from "react";
import { api } from "../api/client.js";

// Backup review view: every pending boundary as a tile, grouped by source
// video with an alternating left-border color per video so it's obvious at
// a glance where one file's boundaries end and the next begins.
const GROUP_COLORS = ["#4f8ef7", "#f7974f", "#4fbf7f", "#c34ff7"];

export default function TileGrid() {
  const [boundaries, setBoundaries] = useState([]);

  const refresh = () => api.listBoundaries({ status: "pending" }).then(setBoundaries);

  useEffect(() => {
    refresh();
  }, []);

  const decide = async (id, verdict) => {
    await api.reviewBoundary(id, verdict);
    refresh();
  };

  const videoOrder = [];
  for (const b of boundaries) {
    if (!videoOrder.includes(b.video)) videoOrder.push(b.video);
  }
  const colorFor = (videoId) => GROUP_COLORS[videoOrder.indexOf(videoId) % GROUP_COLORS.length];

  return (
    <div>
      <h1>Unreviewed Boundaries</h1>
      <div className="tile-grid">
        {boundaries.map((b) => (
          <div
            key={b.id}
            className="tile"
            style={{ borderLeft: `6px solid ${colorFor(b.video)}` }}
          >
            <div className="tile-header">{b.video_path}</div>
            <video src={`/api/boundaries/${b.id}/clip/`} controls style={{ width: "100%" }} />
            <div className="tile-meta">{formatTime(b.timestamp_seconds)}</div>
            <div className="tile-actions">
              <button className="reject" onClick={() => decide(b.id, "rejected")}>
                No
              </button>
              <button className="approve" onClick={() => decide(b.id, "approved")}>
                Yes
              </button>
            </div>
          </div>
        ))}
        {boundaries.length === 0 && <p>Nothing pending review.</p>}
      </div>
    </div>
  );
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
