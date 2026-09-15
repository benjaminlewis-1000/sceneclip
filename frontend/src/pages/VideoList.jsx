// Library landing page. Auto-syncs and lists every video under VIDEO_ROOT
// on load, plus a directory browser for adding a file from elsewhere under
// that same mount (the container can't see outside it). Each video is a
// card: thumbnail + path on one line, its actions on the next -- reprocess,
// jump to per-video params, review just this video, toggle "done", export.
import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";

// Videos in these states have a job running -- poll a bit faster while any
// of them are visible so the progress bar actually moves on screen.
const IN_PROGRESS_STATUSES = new Set(["detecting"]);
const POLL_MS = 3000;

export default function VideoList() {
  const [videos, setVideos] = useState([]);
  const [syncing, setSyncing] = useState(true);
  const [browserOpen, setBrowserOpen] = useState(false);

  const refresh = useCallback(() => api.listVideos().then(setVideos), []);

  useEffect(() => {
    // First load: sync the default directory into the DB, then show it --
    // this is what makes the page "just show up populated."
    api
      .syncVideos()
      .catch(() => null)
      .finally(() => refresh().finally(() => setSyncing(false)));
  }, [refresh]);

  useEffect(() => {
    // Also keeps polling while any video is still waiting on its background
    // metadata backfill (duration_seconds null), so a thumbnail placeholder
    // flips over to the real image without a manual refresh.
    const hasPendingWork = videos.some(
      (v) =>
        IN_PROGRESS_STATUSES.has(v.status) ||
        v.export_progress_percent != null ||
        v.duration_seconds == null
    );
    if (!hasPendingWork) return undefined;
    const interval = setInterval(refresh, POLL_MS);
    return () => clearInterval(interval);
  }, [videos, refresh]);

  const rescan = async () => {
    setSyncing(true);
    await api.syncVideos();
    await refresh();
    setSyncing(false);
  };

  const reprocess = async (video) => {
    await api.detectVideo(video.id, video.detection_params_override || undefined);
    refresh();
  };

  const exportClips = async (video) => {
    await api.exportVideo(video.id);
    refresh();
  };

  const toggleDone = async (video) => {
    await api.setVideoDone(video.id, !video.marked_done);
    refresh();
  };

  const addVideo = async (path) => {
    await api.createVideo(path);
    setBrowserOpen(false);
    refresh();
  };

  return (
    <div>
      <h1>Videos</h1>
      <div className="library-toolbar">
        <button onClick={rescan} disabled={syncing}>
          {syncing ? "Scanning..." : "Rescan default directory"}
        </button>
        <button onClick={() => setBrowserOpen((v) => !v)}>
          {browserOpen ? "Close browser" : "Add from a different path..."}
        </button>
      </div>

      {browserOpen && <DirectoryBrowser onPick={addVideo} onClose={() => setBrowserOpen(false)} />}

      <div className="video-cards">
        {videos.map((v) => (
          <VideoCard
            key={v.id}
            video={v}
            onReprocess={() => reprocess(v)}
            onExport={() => exportClips(v)}
            onToggleDone={() => toggleDone(v)}
          />
        ))}
        {videos.length === 0 && !syncing && (
          <p>No videos found under the default directory. Use "Add from a different path" to add one.</p>
        )}
      </div>
    </div>
  );
}

function VideoCard({ video, onReprocess, onExport, onToggleDone }) {
  const inProgress = IN_PROGRESS_STATUSES.has(video.status);
  const exporting = video.export_progress_percent != null;
  // duration_seconds is set by the same background task that generates the
  // thumbnail (see tasks.py:generate_video_metadata_task) -- using it as
  // the "ready" signal avoids requesting a thumbnail that would otherwise
  // still have to be generated synchronously on this request.
  const metadataReady = video.duration_seconds != null;

  return (
    <div className={`video-card${video.marked_done ? " done" : ""}`}>
      <div className="video-card-row">
        {metadataReady ? (
          <img
            className="video-thumb"
            src={api.videoThumbnailUrl(video.id)}
            alt=""
            loading="lazy"
          />
        ) : (
          <div className="video-thumb video-thumb-pending">Processing...</div>
        )}
        <div className="video-card-info">
          <Link to={`/videos/${video.id}`}>{video.path}</Link>
          <span className="video-status">
            {video.marked_done ? "Done" : video.status}
          </span>
        </div>
      </div>

      {inProgress && (
        <ProgressBar label="Detecting" percent={video.detection_progress_percent} />
      )}
      {exporting && <ProgressBar label="Exporting" percent={video.export_progress_percent} />}

      <div className="video-card-actions">
        <button onClick={onReprocess} disabled={inProgress}>
          Reprocess
        </button>
        <Link to={`/videos/${video.id}`}>
          <button>Adjust params</button>
        </Link>
        <Link to={`/review?video=${video.id}`}>
          <button>Review this video</button>
        </Link>
        <button onClick={onExport} disabled={exporting}>
          Export clips
        </button>
        <button onClick={onToggleDone}>
          {video.marked_done ? "Mark not done" : "Mark done"}
        </button>
      </div>
    </div>
  );
}

function ProgressBar({ label, percent }) {
  const value = percent ?? 0;
  return (
    <div className="progress-row">
      <span className="progress-label">{label}</span>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${value}%` }} />
      </div>
      <span className="progress-percent">{percent == null ? "..." : `${value}%`}</span>
    </div>
  );
}

// Simple drill-down browser scoped to VIDEO_ROOT -- picking a file fills in
// the create-video call directly rather than a text input, since the whole
// point is not having to type a container path by hand.
function DirectoryBrowser({ onPick, onClose }) {
  const [listing, setListing] = useState(null);

  const load = useCallback((path) => {
    api.browseVideos(path).then(setListing);
  }, []);

  useEffect(() => {
    load("");
  }, [load]);

  if (!listing) return <p>Loading directory...</p>;

  return (
    <div className="directory-browser">
      <div className="directory-browser-header">
        <strong>/{listing.path}</strong>
        <button onClick={onClose}>Close</button>
      </div>
      <ul>
        {listing.parent !== null && (
          <li>
            <button onClick={() => load(listing.parent)}>.. (up)</button>
          </li>
        )}
        {listing.entries.map((entry) =>
          entry.type === "dir" ? (
            <li key={entry.name}>
              <button onClick={() => load(`${listing.path}/${entry.name}`.replace(/^\//, ""))}>
                📁 {entry.name}
              </button>
            </li>
          ) : (
            <li key={entry.name}>
              <button onClick={() => onPick(entry.path)}>🎞 {entry.name}</button>
            </li>
          )
        )}
        {listing.entries.length === 0 && <li>Empty directory.</li>}
      </ul>
    </div>
  );
}
