// Library landing page. Auto-syncs and lists every video under VIDEO_ROOT
// on load, plus a directory browser for adding a file from elsewhere under
// that same mount (the container can't see outside it). Each video is a
// card: thumbnail + path on one line, its actions on the next -- reprocess,
// jump to per-video params, review this video, toggle "done". Encoding
// clips is scene-by-scene now (automatic once a scene closes, or manual
// for the trailing open one), so it lives on the video review page, not
// here as a whole-video action.
import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { compareByRecordedDate, SortControl, useVideoSortPreference } from "../videoSort.jsx";

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
  { key: "no_date", label: "No recorded date" },
];

export default function VideoList() {
  const [videos, setVideos] = useState([]);
  const [syncing, setSyncing] = useState(true);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [filter, setFilter] = useState("all");
  // "status" (default) groups by review progress; "date" is a flat sort by
  // recorded_date (nulls last) -- the point of recorded_date in the first
  // place is spotting duplicate/re-digitized captures of the same tape by
  // seeing which videos share (or nearly share) a filming date. Shared
  // (localStorage-persisted) with the same control on Review Queue/Verify
  // Queue's video lists, so the choice is the same wherever you are.
  const { mode: sortMode, direction: sortDirection, setMode: setSortMode, setDirection: setSortDirection } =
    useVideoSortPreference();

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
      (v) => IN_PROGRESS_STATUSES.has(v.status) || v.duration_seconds == null
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

  // An edit that changes a video's sort rank (date, marked-done) re-sorts
  // the whole list on refresh -- the card itself moving is fine (that's
  // the point), but without this the *viewport* stays at the same scroll
  // offset while different content slides underneath it, reading as a
  // jarring jump to a random spot in the page. Keeps the edited card
  // anchored at the same screen position; everything else reflows around
  // it instead of around the user's scroll position.
  const withScrollAnchor = async (videoId, action) => {
    const el = document.getElementById(`video-card-${videoId}`);
    const prevTop = el ? el.getBoundingClientRect().top : null;
    await action();
    if (prevTop != null) {
      requestAnimationFrame(() => {
        const newEl = document.getElementById(`video-card-${videoId}`);
        if (newEl) window.scrollBy(0, newEl.getBoundingClientRect().top - prevTop);
      });
    }
  };

  const toggleDone = (video) =>
    withScrollAnchor(video.id, async () => {
      await api.setVideoDone(video.id, !video.marked_done);
      await refresh();
    });

  const updateRecordedDate = (video, date) =>
    withScrollAnchor(video.id, async () => {
      await api.setVideoRecordedDate(video.id, date);
      await refresh();
    });

  const undoDuplicate = async (video) => {
    const proceed = window.confirm(
      "Undo the duplicate marking? Scenes that had their clip deleted will start re-encoding."
    );
    if (!proceed) return;
    await api.unmarkDuplicate(video.id);
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
    const targets = videos.filter((v) => v.status === "pending" && !v.duplicate_of);
    await Promise.all(targets.map((v) => api.detectVideo(v.id, v.detection_params_override || undefined)));
    refresh();
  };
  const pendingCount = videos.filter((v) => v.status === "pending" && !v.duplicate_of).length;

  const sortedVideos =
    sortMode === "date"
      ? [...videos].sort((a, b) => compareByRecordedDate(a, b, sortDirection))
      : [...videos].sort((a, b) => {
          if (a.marked_done !== b.marked_done) return a.marked_done ? 1 : -1;
          const statusDiff = (STATUS_ORDER[a.status] ?? 99) - (STATUS_ORDER[b.status] ?? 99);
          if (statusDiff !== 0) return statusDiff;
          // Within "detecting", a run actually being worked on is more
          // worth seeing than one just sitting behind a worker-concurrency
          // backlog.
          const runRank = (v) => (v.detection_run_status === "running" ? 0 : 1);
          if (a.status === "detecting") return runRank(a) - runRank(b);
          // Within "pending", a video that's exhausted its automatic
          // retries needs a human to look at it -- a normal pending video
          // will just get auto-queued on its own soon.
          if (a.status === "pending") return (a.detection_exhausted ? 0 : 1) - (b.detection_exhausted ? 0 : 1);
          return 0;
        });
  const visibleVideos = sortedVideos.filter((v) => {
    if (filter === "all") return true;
    if (filter === "done") return v.marked_done;
    if (filter === "no_date") return !v.recorded_date;
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

      <SortControl
        mode={sortMode}
        direction={sortDirection}
        setMode={setSortMode}
        setDirection={setSortDirection}
      />

      {browserOpen && <DirectoryBrowser onPick={addVideo} onClose={() => setBrowserOpen(false)} />}

      <div className="video-cards">
        {visibleVideos.map((v) => (
          <VideoCard
            key={v.id}
            video={v}
            onReprocess={() => reprocess(v)}
            onToggleDone={() => toggleDone(v)}
            onUndoDuplicate={() => undoDuplicate(v)}
            onUpdateRecordedDate={(date) => updateRecordedDate(v, date)}
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

function VideoCard({ video, onReprocess, onToggleDone, onUndoDuplicate, onUpdateRecordedDate }) {
  const inProgress = IN_PROGRESS_STATUSES.has(video.status);
  // duration_seconds is set by the same background task that generates the
  // thumbnail (see tasks.py:generate_video_metadata_task) -- using it as
  // the "ready" signal avoids requesting a thumbnail that would otherwise
  // still have to be generated synchronously on this request.
  const metadataReady = video.duration_seconds != null;

  return (
    <div id={`video-card-${video.id}`} className={`video-card${video.marked_done ? " done" : ""}`}>
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
            {video.duplicate_of_path
              ? `Duplicate of ${video.duplicate_of_path}`
              : video.marked_done
              ? "Done"
              : video.status}
            {video.duration_seconds != null && ` · ${formatDuration(video.duration_seconds)}`}
          </span>
          <label className="video-recorded-date">
            Recorded:{" "}
            <input
              type="date"
              defaultValue={video.recorded_date || ""}
              onBlur={(e) => onUpdateRecordedDate(e.target.value || null)}
            />
          </label>
        </div>
      </div>

      {video.detection_exhausted && (
        <p className="video-stuck" title={video.last_detection_error || undefined}>
          Stuck -- detection failed repeatedly and won't auto-retry again. Click Reprocess, or hover
          for the last error.
        </p>
      )}

      {inProgress && video.detection_run_status === "queued" ? (
        // Queued but not yet picked up by a worker (worker concurrency is
        // 2 -- a big "Process all" or the orphan-sweep's auto-retry can
        // queue dozens at once). A 0% bar here would read as stuck; it's
        // just waiting its turn.
        <p className="progress-queued">Queued -- waiting for a worker slot...</p>
      ) : inProgress && video.detection_progress_percent >= 100 ? (
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
          onClick={onToggleDone}
          disabled={!video.marked_done && !video.ready_to_mark_done}
          title={
            !video.marked_done && !video.ready_to_mark_done
              ? "Every candidate boundary must be reviewed and every scene verified first"
              : undefined
          }
        >
          {video.marked_done ? "Mark not done" : "Mark done"}
        </button>
        {video.duplicate_of_path && (
          <button onClick={onUndoDuplicate}>Undo duplicate marking</button>
        )}
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
