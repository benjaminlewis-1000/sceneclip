// Library landing page: add a video by its container-side path and jump
// into its detail page.
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";

export default function VideoList() {
  const [videos, setVideos] = useState([]);
  const [newPath, setNewPath] = useState("");

  const refresh = () => api.listVideos().then(setVideos);

  useEffect(() => {
    refresh();
  }, []);

  const addVideo = async (e) => {
    e.preventDefault();
    if (!newPath.trim()) return;
    await api.createVideo(newPath.trim());
    setNewPath("");
    refresh();
  };

  return (
    <div>
      <h1>Videos</h1>
      <form onSubmit={addVideo} className="add-video-form">
        <input
          value={newPath}
          onChange={(e) => setNewPath(e.target.value)}
          placeholder="/videos/some_tape.mp4"
        />
        <button type="submit">Add</button>
      </form>
      <table className="video-table">
        <thead>
          <tr>
            <th>Path</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {videos.map((v) => (
            <tr key={v.id}>
              <td>
                <Link to={`/videos/${v.id}`}>{v.path}</Link>
              </td>
              <td>{v.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
