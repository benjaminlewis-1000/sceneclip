// Auto-advancing queue over encoded-but-unverified scenes -- the final
// checkpoint after boundary review and encoding: watch the actual encoded
// clip, confirm (or fix) its description/date, mark it verified, move on.
// Mirrors ReviewQueue's structure, including optional ?video= scoping.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import BackButton from "../components/BackButton.jsx";

const SPEEDS = [1, 1.25, 1.5, 1.75, 2, 2.5, 3, 5, 8, 10];

export default function VerifyQueue() {
  const [searchParams] = useSearchParams();
  const scopedVideoId = searchParams.get("video");

  const [scene, setScene] = useState(undefined); // undefined = loading, null = empty queue
  const [speed, setSpeed] = useState(1);
  const [description, setDescription] = useState("");
  const [date, setDate] = useState("");
  // Session-only -- not persisted, just kept out of the query for the rest
  // of this visit so "Skip" doesn't loop back to the same scene. A reload
  // (or coming back later) clears it and it's reachable normally again.
  const [skippedIds, setSkippedIds] = useState([]);
  // Every source video that still has something to verify, with a count --
  // lets you jump straight to a specific video instead of only ever seeing
  // the single global next-up scene.
  const [summary, setSummary] = useState([]);
  const videoRef = useRef(null);

  const refreshSummary = () => api.verifySummary().then(setSummary);

  const loadNext = useCallback(async () => {
    setScene(undefined);
    const next = await api.nextVerifyQueueScene(scopedVideoId, skippedIds);
    setScene(next);
    setDescription(next?.description || "");
    setDate(next?.scene_date || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopedVideoId, skippedIds]);

  useEffect(() => {
    loadNext();
    refreshSummary();
  }, [loadNext]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = speed;
  }, [speed, scene]);

  const markVerified = async () => {
    if (!scene) return;
    // Flush any edits made here before verifying.
    await api.updateScene(scene.id, { description, scene_date: date || null });
    await api.verifyScene(scene.id);
    loadNext();
    refreshSummary();
  };

  const skip = () => {
    if (!scene) return;
    setSkippedIds((ids) => [...ids, scene.id]);
  };

  const videoList = summary.length > 0 && (
    <div className="boundary-log">
      <h2>Videos with clips to verify</h2>
      <ul>
        {!scopedVideoId ? null : (
          <li>
            <Link to="/verify">-- All videos --</Link>
          </li>
        )}
        {summary.map((row) => (
          <li key={row.video_id}>
            <Link to={`/verify?video=${row.video_id}`}>
              {row.video_path} ({row.count})
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );

  if (scene === undefined) {
    return (
      <div>
        <BackButton />
        <p>Loading next scene...</p>
      </div>
    );
  }

  if (scene === null) {
    return (
      <div>
        <BackButton />
        <h1>{scopedVideoId ? "Verify Queue (this video)" : "Verify Queue"}</h1>
        <p>
          {scopedVideoId
            ? "Nothing left to verify for this video."
            : "Every encoded scene has been verified, or nothing's been encoded yet."}{" "}
          Scenes encode automatically once dated during <Link to="/review">boundary review</Link>.
        </p>
        {videoList}
      </div>
    );
  }

  return (
    <div className="review-queue">
      <BackButton />
      <h1>{scopedVideoId ? "Verify Queue (this video)" : "Verify Queue"}</h1>
      <p className="boundary-meta">
        {scene.video_path} @ {formatTime(scene.start_seconds)}-{formatTime(scene.end_seconds)}
        {" -- "}
        <Link to={`/videos/${scene.video}`}>Go to video</Link>
      </p>
      <video
        ref={videoRef}
        key={scene.id}
        src={api.sceneClipUrl(scene.id)}
        autoPlay
        controls
        // Loading a new src resets playbackRate to 1 in most browsers --
        // re-applying once metadata is actually loaded keeps the selected
        // speed sticking as the queue auto-advances to the next scene.
        onLoadedMetadata={() => {
          if (videoRef.current) videoRef.current.playbackRate = speed;
        }}
        style={{ maxWidth: "720px", width: "100%", display: "block", margin: "0 auto" }}
      />
      <div className="speed-controls">
        {SPEEDS.map((s) => (
          <button key={s} className={s === speed ? "active" : ""} onClick={() => setSpeed(s)}>
            {s}x
          </button>
        ))}
      </div>

      <div className="boundary-log">
        <div className="boundary-log-row">
          <span className="boundary-log-label">Log</span>
          <input
            type="text"
            placeholder="Description..."
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </div>
      </div>

      <div className="review-actions">
        <button onClick={skip}>Skip (this session only)</button>
        <button className="approve" onClick={markVerified}>
          Looks good -- Verify
        </button>
      </div>

      {videoList}
    </div>
  );
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
