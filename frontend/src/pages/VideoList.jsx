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

// Bubble order: finished-detecting (actionable -- ready to review) first,
// then actively-detecting (worth watching), then not-yet-started, then
// fully exported (least relevant, nothing left to do). Marked-done videos
// sink below everything else regardless of status, independent of this.
const STATUS_ORDER = { reviewing: 0, detecting: 1, pending: 2, exported: 3 };

const FILTERS = [
  { key: "all", label: "All" },
  { key: "reviewing", label: "Ready to review" },
  { key: "detecting", label: "Detecting" },
  { key: "pending", label: "Not started" },
  { key: "exported", label: "Exported" },
  { key: "done", label: "Marked done" },
];

export default function VideoList() {
  const [videos, setVideos] = useState([]);
  const [syncing, setSyncing] = useState(true);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [filter, setFilter] = useState("all");

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

  // Kicks off detection for every video that hasn't been run yet -- videos
  // already mid-detection, under review, or exported are left alone so
  // this can't clobber in-progress work.
  const processAll = async () => {
    const targets = videos.filter((v) => v.status === "pending");
    await Promise.all(targets.map((v) => api.detectVideo(v.id, v.detection_params_override || undefined)));
    refresh();
  };
  const pendingCount = videos.filter((v) => v.status === "pending").length;

  const sortedVideos = [...videos].sort((a, b) => {
    if (a.marked_done !== b.marked_done) return a.marked_done ? 1 : -1;
    return (STATUS_ORDER[a.status] ?? 99) - (STATUS_ORDER[b.status] ?? 99);
  });
  const visibleVideos = sortedVideos.filter((v) => {
    if (filter === "all") return true;
    if (filter === "done") return v.marked_done;
    return v.status === filter;
  });

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
        <button onClick={processAll} disabled={pendingCount === 0}>
          Process all ({pendingCount} pending)
        </button>
      </div>

      <div className="filter-toolbar">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={filter === f.key ? "active" : ""}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {browserOpen && <DirectoryBrowser onPick={addVideo} onClose={() => setBrowserOpen(false)} />}

      <div className="video-cards">
        {visibleVideos.map((v) => (
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
        {videos.length > 0 && visibleVideos.length === 0 && <p>No videos match this filter.</p>}
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
            {video.duration_seconds != null && ` · ${formatDuration(video.duration_seconds)}`}
          </span>
        </div>
      </div>

      {inProgress && video.detection_progress_percent >= 100 ? (
        // PySceneDetect itself finishes (100%) before the task is done --
        // saving candidate boundaries, carrying forward prior review
        // verdicts, and rebuilding derived scenes all still happen after,
        // which can take a real moment for a video with a lot of
        // boundaries or review history. A bar frozen at 100% reads as
        // stuck, so drop it and just say what's still happening.
        <p className="progress-finishing">Finishing up (saving results)...</p>
      ) : (
        inProgress && <ProgressBar label="Detecting" percent={video.detection_progress_percent} />
      )}
      {exporting && <ProgressBar label="Exporting" percent={video.export_progress_percent} />}

      <div className="video-card-actions">
        <button onClick={onReprocess} disabled={inProgress}>
          Reprocess
        </button>
        <Link to={`/videos/${video.id}`}>
          <button>Adjust params</button>
        </Link>
        {/* A disabled <button> inside a <Link> would still navigate on
        click (the surrounding <a> catches it), so render a plain disabled
        button instead of a Link at all when there's nothing to do yet. */}
        {video.has_boundaries ? (
          <Link to={`/review?video=${video.id}`}>
            <button>Review this video</button>
          </Link>
        ) : (
          <button disabled title="No candidate scene boundaries detected yet -- run detection first">
            Review this video
          </button>
        )}
        <button
          onClick={onExport}
          disabled={exporting || !video.has_approved_boundaries}
          title={!video.has_approved_boundaries ? "No approved boundaries yet -- review at least one first" : undefined}
        >
          Export clips
        </button>
        <button onClick={onToggleDone}>
          {video.marked_done ? "Mark not done" : "Mark done"}
        </button>
      </div>
    </div>
  );
}

function formatDuration(seconds) {
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(h > 0 ? 2 : 1, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
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
