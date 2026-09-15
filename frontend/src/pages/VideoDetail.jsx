// Per-video page: full-video playback, per-video detection-param overrides
// (falls back to the library-wide defaults), the derived scene list with
// inline enrichment editing, and the export trigger.
import React, { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client.js";
import BackButton from "../components/BackButton.jsx";

const SPEEDS = [1, 1.25, 1.5, 1.75, 2, 2.5, 3];

export default function VideoDetail() {
  const { videoId } = useParams();
  const [video, setVideo] = useState(null);
  const [scenes, setScenes] = useState([]);
  const [params, setParams] = useState({ detector: "adaptive", threshold: 3.0, min_scene_len_seconds: 2.0 });
  const [speed, setSpeed] = useState(1);
  const [playRange, setPlayRange] = useState(null); // {end} while "Watch this scene" is active
  const videoRef = useRef(null);

  const refresh = async () => {
    const v = await api.getVideo(videoId);
    setVideo(v);
    setParams(v.detection_params_override || (await api.getDetectionParams()));
    setScenes(await api.listScenes(videoId));
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoId]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = speed;
  }, [speed, videoId]);

  // Auto-pauses once playback reaches the end of the scene that "Watch
  // this scene" started -- there's no native way to bound <video> playback
  // to a range once it's already loaded (media-fragment #t=start,end only
  // takes effect on initial load), so this is a manual stand-in.
  useEffect(() => {
    const v = videoRef.current;
    if (!v || !playRange) return undefined;
    const onTimeUpdate = () => {
      if (v.currentTime >= playRange.end) v.pause();
    };
    v.addEventListener("timeupdate", onTimeUpdate);
    return () => v.removeEventListener("timeupdate", onTimeUpdate);
  }, [playRange]);

  const watchScene = (scene) => {
    const v = videoRef.current;
    if (!v) return;
    setPlayRange({ end: scene.end_seconds });
    v.currentTime = scene.start_seconds;
    v.play();
    v.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const runDetection = async (saveAsOverride) => {
    await api.detectVideo(videoId, params, saveAsOverride);
    refresh();
  };

  const exportVideo = async () => {
    await api.exportVideo(videoId);
    refresh();
  };

  const updateScene = async (sceneId, field, value) => {
    await api.updateScene(sceneId, { [field]: value });
    refresh();
  };

  if (!video) return <p>Loading...</p>;

  // Even with zero approved boundaries, rebuild_scenes() still produces one
  // "whole video" Scene row once duration is known -- so gate on
  // has_approved_boundaries (a real human verdict), not just "some
  // not-yet-exported scene exists."
  const exportDisabled = !video.has_approved_boundaries || !scenes.some((s) => !s.exported);

  return (
    <div>
      <BackButton />
      <h1>{video.path}</h1>
      <p>Status: {video.status}</p>

      <video
        ref={videoRef}
        src={`/api/videos/${videoId}/stream/`}
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
            <th>Exported</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {scenes.map((s) => (
            <tr key={s.id}>
              <td>{formatTime(s.start_seconds)}</td>
              <td>{formatTime(s.end_seconds)}</td>
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
              <td>{s.exported ? "yes" : "no"}</td>
              <td>
                <button onClick={() => watchScene(s)}>Watch</button>
              </td>
            </tr>
          ))}
          {scenes.length === 0 && (
            <tr>
              <td colSpan="6">
                No scenes yet -- once boundaries are reviewed, approved segments show up here.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <button
        onClick={exportVideo}
        disabled={exportDisabled}
        title={exportDisabled ? "No approved boundaries yet -- review at least one first" : undefined}
      >
        Export approved scenes
      </button>
    </div>
  );
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
