// Thin fetch wrapper: attaches the Django CSRF cookie (required since we're
// authenticated via session cookies, not a bearer token) and centralizes
// error handling for every API call below.
function getCookie(name) {
  const match = document.cookie.match(new RegExp(`(^| )${name}=([^;]+)`));
  return match ? match[2] : null;
}

// DRF's IsAuthenticated returns 403 (not 401 -- SessionAuthentication sets
// no WWW-Authenticate challenge) for a request with no session at all, and
// this app has no per-object permissions, so a 403 here only ever means
// "not logged in yet." Rather than every page silently rendering empty
// lists with no explanation, bounce straight to Authelia -- with
// SOCIALACCOUNT_LOGIN_ON_GET this skips allauth's intermediate confirm
// page and goes directly to the SSO redirect.
const LOGIN_URL = "/accounts/oidc/authelia/login/";

function redirectToLogin() {
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `${LOGIN_URL}?process=login&next=${next}`;
}

async function request(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      // Cookie name matches settings.py's CSRF_COOKIE_NAME -- kept unique
      // (not Django's default "csrftoken") to avoid colliding with other
      // apps on this domain; see the comment there for what that collision
      // actually broke.
      "X-CSRFToken": getCookie("sceneclip_csrftoken") || "",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (res.status === 403) {
    const text = await res.text().catch(() => "");
    // Django's CSRF middleware also returns 403, and would otherwise look
    // identical to "not logged in" here -- redirecting to login on THAT is
    // wrong (you're already authenticated) and, worse, self-perpetuating:
    // Authelia silently re-approves an existing SSO session and bounces
    // you right back, hitting the same broken request again. Only treat
    // this as "need to log in" when the body doesn't mention CSRF.
    if (!/csrf/i.test(text)) {
      redirectToLogin();
      return new Promise(() => {}); // navigating away -- never resolve
    }
    throw new Error(`403 ${res.statusText}: ${text}`);
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return null;
  // DRF's JSONRenderer sends an empty body (not the literal string "null")
  // for `Response(None)` -- res.json() throws a SyntaxError on empty text,
  // which broke both the boundary and verify queues' "nothing left"
  // case: the throw happened inside loadNext() before setScene/setBoundary
  // ever ran, so the page stayed stuck showing "Loading..." forever
  // instead of ever reaching the actual empty-queue message.
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

// One function per backend endpoint -- pages import `api` rather than
// calling fetch() directly.
export const api = {
  listVideos: () => request("/api/videos/"),
  getVideo: (id) => request(`/api/videos/${id}/`),
  createVideo: (path) =>
    request("/api/videos/", { method: "POST", body: JSON.stringify({ path }) }),
  syncVideos: () => request("/api/videos/sync/", { method: "POST" }),
  browseVideos: (path = "") =>
    request(`/api/videos/browse/?path=${encodeURIComponent(path)}`),
  setVideoDone: (id, done) =>
    request(`/api/videos/${id}/`, { method: "PATCH", body: JSON.stringify({ marked_done: done }) }),
  markDuplicate: (duplicateId, keepId) =>
    request(`/api/videos/${duplicateId}/mark_duplicate/`, {
      method: "POST",
      body: JSON.stringify({ keep: keepId }),
    }),
  unmarkDuplicate: (id) => request(`/api/videos/${id}/unmark_duplicate/`, { method: "POST" }),
  detectVideo: (id, params, saveAsOverride) =>
    request(`/api/videos/${id}/detect/`, {
      method: "POST",
      body: JSON.stringify({ params, save_as_override: saveAsOverride }),
    }),
  exportVideo: (id) => request(`/api/videos/${id}/export/`, { method: "POST" }),
  videoThumbnailUrl: (id) => `/api/videos/${id}/thumbnail/`,

  listBoundaries: (params = {}) =>
    request(`/api/boundaries/?${new URLSearchParams(params)}`),
  nextQueueBoundary: (videoId) =>
    request(`/api/boundaries/queue/${videoId ? `?video=${videoId}` : ""}`),
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
  updateBoundary: (id, data) =>
    request(`/api/boundaries/${id}/`, { method: "PATCH", body: JSON.stringify(data) }),
  undoBoundary: (id) => request(`/api/boundaries/${id}/undo/`, { method: "POST" }),

  listScenes: (videoId) => request(`/api/scenes/?video=${videoId}`),
  updateScene: (id, data) =>
    request(`/api/scenes/${id}/`, { method: "PATCH", body: JSON.stringify(data) }),
  encodeScene: (id) => request(`/api/scenes/${id}/encode/`, { method: "POST" }),
  sceneClipUrl: (id) => `/api/scenes/${id}/clip/`,
  verifyScene: (id) => request(`/api/scenes/${id}/verify/`, { method: "POST" }),
  verifySummary: () => request("/api/scenes/verify_summary/"),
  nextVerifyQueueScene: (videoId, excludeIds) => {
    const params = new URLSearchParams();
    if (videoId) params.set("video", videoId);
    if (excludeIds && excludeIds.length) params.set("exclude", excludeIds.join(","));
    const qs = params.toString();
    return request(`/api/scenes/verify_queue/${qs ? `?${qs}` : ""}`);
  },

  listNotifications: (since) =>
    request(`/api/notifications/${since ? `?since=${encodeURIComponent(since)}` : ""}`),
  markNotificationRead: (id) =>
    request(`/api/notifications/${id}/mark_read/`, { method: "POST" }),
  deleteNotification: (id) => request(`/api/notifications/${id}/`, { method: "DELETE" }),
  clearAllNotifications: () => request("/api/notifications/clear_all/", { method: "POST" }),

  getDetectionParams: () => request("/api/detection-params/"),
  updateDetectionParams: (data) =>
    request("/api/detection-params/", { method: "PUT", body: JSON.stringify(data) }),

  clearDatabase: () =>
    request("/api/clear-database/", { method: "POST", body: JSON.stringify({ confirm: "CLEAR" }) }),

  taskQueue: () => request("/api/queue/"),
};
