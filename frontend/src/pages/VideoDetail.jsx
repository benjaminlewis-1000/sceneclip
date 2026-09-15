// Per-video page: full-video playback, per-video detection-param overrides
// (falls back to the library-wide defaults), and the derived scene list
// with inline enrichment editing. Encoding is per-scene: most scenes
// encode automatically as soon as they close (see tasks.trigger_auto_encode
// on the backend); the trailing, still-open scene needs a manual "Encode"
// click here since it keeps growing as more boundaries get approved.
import React, { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client.js";
import BackButton from "../components/BackButton.jsx";

const SPEEDS = [1, 1.25, 1.5, 1.75, 2, 2.5, 3];
const POLL_MS = 3000;

export default function VideoDetail() {
  const { videoId } = useParams();
  const [video, setVideo] = useState(null);
  const [scenes, setScenes] = useState([]);
  const [boundaries, setBoundaries] = useState([]);
  const [params, setParams] = useState({ detector: "adaptive", threshold: 3.0, min_scene_len_seconds: 2.0 });
  const [speed, setSpeed] = useState(1);
  // null = playing the raw source (top player); otherwise the scene whose
  // encoded clip is currently loaded.
  const [watchingSceneId, setWatchingSceneId] = useState(null);
  const videoRef = useRef(null);

  const refresh = async () => {
    const v = await api.getVideo(videoId);
    setVideo(v);
    setParams(v.detection_params_override || (await api.getDetectionParams()));
    setScenes(await api.listScenes(videoId));
    setBoundaries(await api.listBoundaries({ video: videoId }));
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoId]);

  useEffect(() => {
    // Scene encoding happens server-side (auto-triggered or manual) --
    // poll while anything's mid-encode so the table's progress/Watch
    // gating updates without a manual refresh.
    const hasEncodingInFlight = scenes.some((s) => s.export_progress_percent != null);
    if (!hasEncodingInFlight) return undefined;
    const interval = setInterval(refresh, POLL_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenes]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = speed;
  }, [speed, videoId, watchingSceneId]);

  const watchScene = (scene) => {
    if (!scene.exported) return;
    setWatchingSceneId(scene.id);
  };

  const watchFullSource = () => setWatchingSceneId(null);

  const runDetection = async (saveAsOverride) => {
    await api.detectVideo(videoId, params, saveAsOverride);
    refresh();
  };

  const updateScene = async (sceneId, field, value) => {
    await api.updateScene(sceneId, { [field]: value });
    refresh();
  };

  const encodeScene = async (scene) => {
    const pending = await api.listBoundaries({ video: videoId, status: "pending" });
    if (pending.length > 0) {
      const n = pending.length;
      const proceed = window.confirm(
        `This video still has ${n} unreviewed scene boundar${n === 1 ? "y" : "ies"}. ` +
          "Encoding the final scene now may lock it in before it's actually complete " +
          "(a later approval could split it further). Encode anyway?"
      );
      if (!proceed) return;
    }
    await api.encodeScene(scene.id);
    refresh();
  };

  const verifyScene = async (scene) => {
    await api.verifyScene(scene.id);
    refresh();
  };

  const undoBoundary = async (boundaryId) => {
    if (!boundaryId) return;
    const proceed = window.confirm(
      "Undo this boundary? Any already-encoded clip bordering it will be deleted and re-merged " +
        "into a single scene for re-review."
    );
    if (!proceed) return;
    try {
      await api.undoBoundary(boundaryId);
      refresh();
    } catch (err) {
      window.alert(err.message);
    }
  };

  if (!video) return <p>Loading...</p>;

  const reviewedBoundaries = boundaries
    .filter((b) => b.review_status !== "pending")
    .sort((a, b) => a.timestamp_seconds - b.timestamp_seconds);

  const playerSrc = watchingSceneId
    ? api.sceneClipUrl(watchingSceneId)
    : `/api/videos/${videoId}/stream/`;

  return (
    <div>
      <BackButton />
      <h1>{video.path}</h1>
      <p>Status: {video.status}</p>

      <video
        ref={videoRef}
        key={playerSrc}
        src={playerSrc}
        controls
        autoPlay={watchingSceneId != null}
        style={{ maxWidth: "720px", width: "100%" }}
      />
      {watchingSceneId != null && (
        <p className="boundary-log-hint">
          Playing the encoded clip for this scene.{" "}
          <button onClick={watchFullSource}>Back to full source</button>
        </p>
      )}
      <div className="speed-controls">
        {SPEEDS.map((s) => (
          <button key={s} className={s === speed ? "active" : ""} onClick={() => setSpeed(s)}>
            {s}x
          </button>
        ))}
      </div>

      <h2>Detection parameters (this video)</h2>
      <div className="params-form">
        <label>
          Detector
          <select
            value={params.detector}
            onChange={(e) => setParams({ ...params, detector: e.target.value })}
          >
            <option value="adaptive">Adaptive (recommended for VHS)</option>
            <option value="content">Content</option>
          </select>
        </label>
        <label>
          Threshold
          <input
            type="number"
            step="0.1"
            value={params.threshold}
            onChange={(e) => setParams({ ...params, threshold: parseFloat(e.target.value) })}
          />
        </label>
        <label>
          Min scene length (seconds)
          <input
            type="number"
            step="0.5"
            value={params.min_scene_len_seconds}
            onChange={(e) =>
              setParams({ ...params, min_scene_len_seconds: parseFloat(e.target.value) })
            }
          />
        </label>
        <div className="params-actions">
          <button onClick={() => runDetection(false)}>Run detection</button>
          <button onClick={() => runDetection(true)}>Run &amp; save as this video's default</button>
        </div>
      </div>

      <h2>Scenes</h2>
      <table className="scene-table">
        <thead>
          <tr>
            <th>Start</th>
            <th>End</th>
            <th>Description</th>
            <th>Date</th>
            <th>Encoded</th>
            <th>Verified</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {scenes.map((s) => {
            const isOpenScene = !s.end_boundary;
            const encoding = s.export_progress_percent != null;
            return (
              <tr key={s.id}>
                <td>{formatTime(s.start_seconds)}</td>
                <td>{isOpenScene ? "(open)" : formatTime(s.end_seconds)}</td>
                <td>
                  <input
                    defaultValue={s.description}
                    onBlur={(e) => updateScene(s.id, "description", e.target.value)}
                  />
                </td>
                <td>
                  <input
                    type="date"
                    defaultValue={s.scene_date || ""}
                    onBlur={(e) => updateScene(s.id, "scene_date", e.target.value || null)}
                  />
                </td>
                <td>
                  {encoding
                    ? s.encode_status === "encoding"
                      ? `${s.export_progress_percent}%...`
                      : "Queued"
                    : s.exported
                    ? "yes"
                    : "no"}
                </td>
                <td>{s.verified ? "✓" : ""}</td>
                <td className="scene-table-actions">
                  <button onClick={() => watchScene(s)} disabled={!s.exported}>
                    Watch
                  </button>
                  <button
                    onClick={() => undoBoundary(s.start_boundary)}
                    disabled={!s.start_boundary || encoding}
                    title={!s.start_boundary ? "This scene starts at the beginning of the video" : undefined}
                  >
                    Undo start
                  </button>
                  <button
                    onClick={() => undoBoundary(s.end_boundary)}
                    disabled={!s.end_boundary || encoding}
                    title={!s.end_boundary ? "This scene is still open (no end cut yet)" : undefined}
                  >
                    Undo end
                  </button>
                  <button
                    onClick={() => verifyScene(s)}
                    disabled={!s.exported || s.verified}
                    title={!s.exported ? "Not encoded yet" : s.verified ? "Already verified" : undefined}
                  >
                    Verify
                  </button>
                  {isOpenScene && !s.exported && (
                    <button
                      onClick={() => encodeScene(s)}
                      disabled={encoding || !s.scene_date}
                      title={!s.scene_date ? "A date is required before encoding" : undefined}
                    >
                      {encoding ? "Encoding..." : "Encode this scene"}
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
          {scenes.length === 0 && (
            <tr>
              <td colSpan="7">
                No scenes yet -- once boundaries are reviewed, approved segments show up here.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      <p className="boundary-log-hint">
        Closed scenes encode automatically once dated during review. Unverified encoded scenes
        show up in the <Link to="/verify">Verify queue</Link>.
      </p>

      <h2>Reviewed boundaries</h2>
      <table className="scene-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Verdict</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {reviewedBoundaries.map((b) => (
            <tr key={b.id}>
              <td>{formatTime(b.timestamp_seconds)}</td>
              <td>{b.review_status}</td>
              <td>
                <button onClick={() => undoBoundary(b.id)}>Undo</button>
              </td>
            </tr>
          ))}
          {reviewedBoundaries.length === 0 && (
            <tr>
              <td colSpan="3">No boundaries reviewed yet.</td>
            </tr>
          )}
        </tbody>
      </table>
      <p className="boundary-log-hint">
        Undoing an approved boundary merges its two neighboring scenes back into one for
        re-review -- if either was already encoded, that clip is deleted from disk.
      </p>
    </div>
  );
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
