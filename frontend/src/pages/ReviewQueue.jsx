// Primary review UX: shows one candidate boundary's preview clip at a time
// and auto-advances to the next pending one after a hotkey decision. This
// is the "queue" mode from the plan; TileGrid.jsx is the grid-view backup.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import BackButton from "../components/BackButton.jsx";

const SPEEDS = [1, 1.25, 1.5, 1.75, 2, 2.5, 3];

// Preview clips span [timestamp - PAD, timestamp + PAD] -- must match
// PREVIEW_PAD_SECONDS in backend/videos/services/clips.py.
const PREVIEW_PAD_SECONDS = 5;
// Approximate NTSC frame duration for the frame-step buttons. Not read from
// the actual source's frame rate (not exposed to the frontend today), so
// this is a nudge amount rather than a guaranteed single-frame step on
// every tape.
const FRAME_STEP_SECONDS = 1 / 30;

export default function ReviewQueue() {
  // ?video=<id> scopes the queue to one video (the "Review this video" link
  // from VideoList); no param reviews across the whole library.
  const [searchParams] = useSearchParams();
  const scopedVideoId = searchParams.get("video");

  const [boundary, setBoundary] = useState(undefined); // undefined = loading, null = empty queue
  const [speed, setSpeed] = useState(1);
  const [clipVersion, setClipVersion] = useState(0); // cache-bust the <video> src after an adjustment
  const videoRef = useRef(null);

  const loadNext = useCallback(async () => {
    setBoundary(undefined);
    setClipVersion(0);
    const next = await api.nextQueueBoundary(scopedVideoId);
    setBoundary(next);
  }, [scopedVideoId]);

  useEffect(() => {
    loadNext();
  }, [loadNext]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = speed;
  }, [speed, boundary]);

  const decide = useCallback(
    async (verdict) => {
      if (!boundary) return;
      await api.reviewBoundary(boundary.id, verdict);
      loadNext();
    },
    [boundary, loadNext]
  );

  const replay = useCallback(() => {
    if (videoRef.current) {
      videoRef.current.currentTime = 0;
      videoRef.current.play();
    }
  }, []);

  // Frame-step the paused clip to line up the cut exactly, then either
  // "Set boundary here" (re-centers the clip on the new position without
  // deciding yet) or approve/reject as normal once it looks right.
  const nudgeFrame = useCallback((direction) => {
    const v = videoRef.current;
    if (!v) return;
    v.pause();
    const maxTime = PREVIEW_PAD_SECONDS * 2;
    v.currentTime = Math.min(maxTime, Math.max(0, v.currentTime + direction * FRAME_STEP_SECONDS));
  }, []);

  const setBoundaryHere = useCallback(async () => {
    const v = videoRef.current;
    if (!v || !boundary) return;
    const absoluteTimestamp = boundary.timestamp_seconds - PREVIEW_PAD_SECONDS + v.currentTime;
    const updated = await api.adjustBoundary(boundary.id, absoluteTimestamp);
    setBoundary(updated);
    setClipVersion((n) => n + 1);
  }, [boundary]);

  // Hotkeys: Y/right-arrow approve, N/left-arrow reject, R/space replay.
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      switch (e.key.toLowerCase()) {
        case "y":
        case "arrowright":
          decide("approved");
          break;
        case "n":
        case "arrowleft":
          decide("rejected");
          break;
        case "r":
        case " ":
          e.preventDefault();
          replay();
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [decide, replay]);

  if (boundary === undefined) {
    return (
      <div>
        <BackButton />
        <p>Loading next boundary...</p>
      </div>
    );
  }

  if (boundary === null) {
    return (
      <div>
        <BackButton />
        <h1>Nothing to review</h1>
        <p>
          {scopedVideoId
            ? "This video hasn't been processed yet, or every candidate boundary has already been reviewed."
            : "Nothing's been detected yet, or everything detected so far has already been reviewed."}{" "}
          Run detection on a video from the <Link to="/">Videos page</Link> to get candidates here.
        </p>
      </div>
    );
  }

  return (
    <div className="review-queue">
      <BackButton />
      <h1>{scopedVideoId ? "Review Queue (this video)" : "Review Queue"}</h1>
      <p className="boundary-meta">
        {boundary.video_path} @ {formatTime(boundary.timestamp_seconds)}
      </p>
      <video
        ref={videoRef}
        key={`${boundary.id}-${clipVersion}`}
        src={`/api/boundaries/${boundary.id}/clip/?v=${clipVersion}`}
        autoPlay
        controls
        style={{ maxWidth: "720px", width: "100%" }}
      />
      <div className="speed-controls">
        {SPEEDS.map((s) => (
          <button
            key={s}
            className={s === speed ? "active" : ""}
            onClick={() => setSpeed(s)}
          >
            {s}x
          </button>
        ))}
      </div>
      <div className="scrub-controls">
        <button onClick={() => nudgeFrame(-1)}>&laquo; frame</button>
        <button onClick={() => nudgeFrame(1)}>frame &raquo;</button>
        <button onClick={setBoundaryHere}>Set boundary here</button>
      </div>
      <div className="review-actions">
        <button className="reject" onClick={() => decide("rejected")}>
          Not a scene change (N)
        </button>
        <button onClick={replay}>Replay (R)</button>
        <button className="approve" onClick={() => decide("approved")}>
          Real scene change (Y)
        </button>
      </div>
    </div>
  );
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
