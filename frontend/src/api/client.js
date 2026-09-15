// Thin fetch wrapper: attaches the Django CSRF cookie (required since we're
// authenticated via session cookies, not a bearer token) and centralizes
// error handling for every API call below.
function getCookie(name) {
  const match = document.cookie.match(new RegExp(`(^| )${name}=([^;]+)`));
  return match ? match[2] : null;
}

async function request(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCookie("csrftoken") || "",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

// One function per backend endpoint -- pages import `api` rather than
// calling fetch() directly.
export const api = {
  listVideos: () => request("/api/videos/"),
  getVideo: (id) => request(`/api/videos/${id}/`),
  createVideo: (path) =>
    request("/api/videos/", { method: "POST", body: JSON.stringify({ path }) }),
  detectVideo: (id, params, saveAsOverride) =>
    request(`/api/videos/${id}/detect/`, {
      method: "POST",
      body: JSON.stringify({ params, save_as_override: saveAsOverride }),
    }),
  exportVideo: (id) => request(`/api/videos/${id}/export/`, { method: "POST" }),

  listBoundaries: (params = {}) =>
    request(`/api/boundaries/?${new URLSearchParams(params)}`),
  nextQueueBoundary: () => request("/api/boundaries/queue/"),
  reviewBoundary: (id, verdict) =>
    request(`/api/boundaries/${id}/review/`, {
      method: "POST",
      body: JSON.stringify({ verdict }),
    }),
  adjustBoundary: (id, timestampSeconds) =>
    request(`/api/boundaries/${id}/adjust/`, {
      method: "POST",
      body: JSON.stringify({ timestamp_seconds: timestampSeconds }),
    }),

  listScenes: (videoId) => request(`/api/scenes/?video=${videoId}`),
  updateScene: (id, data) =>
    request(`/api/scenes/${id}/`, { method: "PATCH", body: JSON.stringify(data) }),

  listNotifications: (since) =>
    request(`/api/notifications/${since ? `?since=${encodeURIComponent(since)}` : ""}`),
  markNotificationRead: (id) =>
    request(`/api/notifications/${id}/mark_read/`, { method: "POST" }),

  getDetectionParams: () => request("/api/detection-params/"),
  updateDetectionParams: (data) =>
    request("/api/detection-params/", { method: "PUT", body: JSON.stringify(data) }),
};
