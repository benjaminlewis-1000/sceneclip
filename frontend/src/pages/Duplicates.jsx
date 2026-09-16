// Pick two videos from the library that are actually the same footage
// (e.g. a re-digitized re-capture of the same tape), compare them side by
// side, and keep one -- the other gets its encoded clips deleted from disk
// (services/duplicates.py:mark_duplicate) but stays in the DB, reversible
// via "Undo duplicate marking" on the library page.
import React, { useEffect, useState } from "react";
import { api } from "../api/client.js";
import BackButton from "../components/BackButton.jsx";

// Above this, the two videos probably aren't actually the same tape --
// still lets the reviewer proceed (they know their own footage better than
// a threshold can), just flags it for a second look.
const DURATION_WARNING_PERCENT = 5;

export default function Duplicates() {
  const [videos, setVideos] = useState([]);
  const [filterA, setFilterA] = useState("");
  const [filterB, setFilterB] = useState("");
  const [idA, setIdA] = useState(null);
  const [idB, setIdB] = useState(null);
  const [statsA, setStatsA] = useState(null);
  const [statsB, setStatsB] = useState(null);
  const [busy, setBusy] = useState(false);

  const refreshVideos = () => api.listVideos().then(setVideos);

  useEffect(() => {
    refreshVideos();
  }, []);

  const videoA = videos.find((v) => v.id === idA) || null;
  const videoB = videos.find((v) => v.id === idB) || null;

  const loadStats = async (id, setter) => {
    if (!id) {
      setter(null);
      return;
    }
    const [boundaries, scenes] = await Promise.all([
      api.listBoundaries({ video: id }),
      api.listScenes(id),
    ]);
    setter({
      totalBoundaries: boundaries.length,
      reviewedBoundaries: boundaries.filter((b) => b.review_status !== "pending").length,
      totalScenes: scenes.length,
      verifiedScenes: scenes.filter((s) => s.verified).length,
    });
  };

  useEffect(() => {
    loadStats(idA, setStatsA);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idA]);

  useEffect(() => {
    loadStats(idB, setStatsB);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idB]);

  const durationDiffPercent =
    videoA?.duration_seconds && videoB?.duration_seconds
      ? (Math.abs(videoA.duration_seconds - videoB.duration_seconds) /
          Math.max(videoA.duration_seconds, videoB.duration_seconds)) *
        100
      : null;

  const keep = async (keepVideo, duplicateVideo) => {
    const proceed = window.confirm(
      `Mark "${duplicateVideo.path}" as a duplicate of "${keepVideo.path}"? ` +
        "Its encoded clips will be deleted from disk to reclaim space -- the video and its full " +
        "review history stay in the database, and this can be undone from the library page."
    );
    if (!proceed) return;
    setBusy(true);
    try {
      await api.markDuplicate(duplicateVideo.id, keepVideo.id);
      setIdA(null);
      setIdB(null);
      refreshVideos();
    } catch (err) {
      window.alert(err.message);
    } finally {
      setBusy(false);
    }
  };

  const filteredA = videos.filter((v) => v.path.toLowerCase().includes(filterA.toLowerCase()));
  const filteredB = videos.filter((v) => v.path.toLowerCase().includes(filterB.toLowerCase()));

  return (
    <div>
      <BackButton />
      <h1>Mark Duplicates</h1>
      <p className="boundary-log-hint">
        Pick two videos that are the same footage -- e.g. an old capture and a re-digitized
        re-capture of the same tape -- compare them, then keep the better one.
      </p>

      <div className="duplicate-columns">
        <VideoPicker
          label="Video A"
          filter={filterA}
          setFilter={setFilterA}
          options={filteredA}
          selectedId={idA}
          onSelect={setIdA}
        />
        <VideoPicker
          label="Video B"
          filter={filterB}
          setFilter={setFilterB}
          options={filteredB}
          selectedId={idB}
          onSelect={setIdB}
        />
      </div>

      {videoA && videoB && (
        <>
          {durationDiffPercent != null && (
            <p className={durationDiffPercent > DURATION_WARNING_PERCENT ? "video-stuck" : "boundary-log-hint"}>
              Duration difference: {durationDiffPercent.toFixed(1)}%
              {durationDiffPercent > DURATION_WARNING_PERCENT
                ? " -- that's a noticeable gap. Double-check these are really the same tape before marking a duplicate."
                : " -- close enough to plausibly be the same tape."}
            </p>
          )}
          <div className="duplicate-columns">
            <DuplicateCard video={videoA} stats={statsA} onKeep={() => keep(videoA, videoB)} disabled={busy} />
            <DuplicateCard video={videoB} stats={statsB} onKeep={() => keep(videoB, videoA)} disabled={busy} />
          </div>
        </>
      )}
    </div>
  );
}

function VideoPicker({ label, filter, setFilter, options, selectedId, onSelect }) {
  return (
    <div className="duplicate-picker-column">
      <label className="boundary-log-label">{label}</label>
      <input
        type="text"
        placeholder="Filter by path..."
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <select
        size={10}
        value={selectedId ?? ""}
        onChange={(e) => onSelect(e.target.value ? Number(e.target.value) : null)}
      >
        <option value="">-- choose a video --</option>
        {options.map((v) => (
          <option key={v.id} value={v.id}>
            {v.path}
          </option>
        ))}
      </select>
    </div>
  );
}

function DuplicateCard({ video, stats, onKeep, disabled }) {
  return (
    <div className="duplicate-card">
      <img className="video-thumb" src={api.videoThumbnailUrl(video.id)} alt="" style={{ width: "100%" }} />
      <p>{video.path}</p>
      <p className="boundary-log-hint">
        {video.duration_seconds != null ? formatDuration(video.duration_seconds) : "Duration unknown"}
      </p>
      {stats && (
        <p className="boundary-log-hint">
          Boundaries reviewed: {stats.reviewedBoundaries}/{stats.totalBoundaries} · Scenes verified:{" "}
          {stats.verifiedScenes}/{stats.totalScenes}
        </p>
      )}
      <button onClick={onKeep} disabled={disabled}>
        Keep this one
      </button>
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
