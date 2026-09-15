// Auto-advancing queue over encoded-but-unverified scenes -- the final
// checkpoint after boundary review and encoding: watch the actual encoded
// clip, confirm (or fix) its description/date, mark it verified, move on.
// Mirrors ReviewQueue's structure.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import BackButton from "../components/BackButton.jsx";

const SPEEDS = [1, 1.25, 1.5, 1.75, 2, 2.5, 3];

export default function VerifyQueue() {
  const [scene, setScene] = useState(undefined); // undefined = loading, null = empty queue
  const [speed, setSpeed] = useState(1);
  const [description, setDescription] = useState("");
  const [date, setDate] = useState("");
  const videoRef = useRef(null);

  const loadNext = useCallback(async () => {
    setScene(undefined);
    const next = await api.nextVerifyQueueScene();
    setScene(next);
    setDescription(next?.description || "");
    setDate(next?.scene_date || "");
  }, []);

  useEffect(() => {
    loadNext();
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
  };

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
        <h1>Nothing to verify</h1>
        <p>
          Every encoded scene has been verified, or nothing's been encoded yet. Scenes encode
          automatically once dated during <Link to="/review">boundary review</Link>.
        </p>
      </div>
    );
  }

  return (
    <div className="review-queue">
      <BackButton />
      <h1>Verify Queue</h1>
      <p className="boundary-meta">
        {scene.video_path} @ {formatTime(scene.start_seconds)}-{formatTime(scene.end_seconds)}
      </p>
      <video
        ref={videoRef}
        key={scene.id}
        src={api.sceneClipUrl(scene.id)}
        autoPlay
        controls
        style={{ maxWidth: "720px", width: "100%" }}
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
        <button className="approve" onClick={markVerified}>
          Looks good -- Verify
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
