# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

SceneClip: a self-hosted dashboard for finding and reviewing scene breaks in long, noisy VHS-rip videos with PySceneDetect, then exporting reviewed chapters as short clips with metadata. Single-user tool, deployed via Docker behind Authelia.

## Commands

Local dev (no Docker):
- Backend: `cd backend && python manage.py runserver` (needs local Postgres/Redis and a `.env` with the vars in `.env.example`)
- Frontend: `cd frontend && npm install && npm run dev` (proxies `/api`, `/accounts`, `/admin` to `localhost:8000`, see `vite.config.js`)
- Migrations: `cd backend && python manage.py makemigrations videos && python manage.py migrate`
- Celery worker: `cd backend && celery -A config worker -l INFO`
- Tests: `cd backend && pip install -r requirements-dev.txt && pytest` (uses `config.settings_test` — in-memory sqlite, eager Celery, no real `.env` needed). Add tests alongside new backend logic as the app grows, particularly around `services/matching.py` and `services/scenes.py`, which carry the trickiest invariants.

Docker (production-shaped):
- `docker compose up --build` — brings up `web`, `worker`, `db`, `redis`, `frontend`
- `docker compose exec web python manage.py <cmd>` for one-off management commands

## Architecture

**Data flow:** `Video` → `DetectionRun` (one PySceneDetect invocation with a params snapshot) → `SceneBoundary` rows (candidate cuts, human-reviewed approved/rejected) → `Scene` rows (derived automatically from approved boundaries by `backend/videos/services/scenes.py:rebuild_scenes`, enriched with description/date, eventually exported). `Notification` rows are what the frontend's polling-based notifications tab reads.

**Boundary review persists across re-detection.** Re-running detection with different params creates a new `DetectionRun` and fresh `SceneBoundary` rows rather than overwriting anything. `backend/videos/services/matching.py:carry_forward_reviews` matches new candidates to previously-reviewed boundaries on the same video within a 1-second tolerance and copies the verdict forward, so only genuinely new/shifted candidates need re-review.

**Async jobs run in Celery, not the request cycle.** Detection (`run_detection_task`) and export (`export_video_task`) are both Celery tasks in `backend/videos/tasks.py`, kicked off by `POST /api/videos/{id}/detect/` and `/export/`. The frontend has no live push channel — `App.jsx` polls `GET /api/notifications/` on an interval; there's no SSE/websocket layer by design (single user, jobs take minutes not seconds).

**VHS resilience lives in `backend/videos/services/detection.py`.** Uses PySceneDetect's `AdaptiveDetector` by default rather than `ContentDetector` — it compares each frame against a rolling window of neighbors instead of a fixed threshold, which tolerates the lighting drift and tracking noise typical of analog captures better. Detection knobs are a library-wide default (`DetectionParams`, singleton row) with an optional per-video JSON override on `Video.detection_params_override`.

**Preview clips are generated, not stored per-boundary in the DB.** `backend/videos/services/clips.py:ensure_preview_clip` cuts a ±5s ffmpeg re-encode (not a stream-copy — `-c copy` seeks snap to the nearest keyframe, which on GOP-heavy VHS encodes can be seconds off from the actual cut) on first request and caches it to `PREVIEW_CLIPS_DIR`, keyed by a hash of video+boundary.

**Video playback uses a hand-rolled Range-request view**, not Django's `FileResponse` (which doesn't support HTTP Range/seeking) — see `backend/videos/services/range_response.py`, used by both `VideoViewSet.stream` (full source) and `SceneBoundaryViewSet.clip` (preview clips).

**A boundary's timestamp can be nudged after detection.** `SceneBoundaryViewSet.adjust` (frontend: the frame-step buttons + "Set boundary here" in `ReviewQueue.jsx`) lets the reviewer frame-scrub within the ±5s preview clip and relocate the exact cut point before approving, since PySceneDetect's candidate timestamp isn't always frame-accurate on noisy source. This re-centers the cached preview clip and re-derives Scene edges.

**Auth mirrors the `django_picasa` sibling project**, not a forward-auth reverse-proxy pattern: `django-allauth` + `allauth.socialaccount.providers.openid_connect` bridges to Authelia as an OIDC identity provider (see `AUTHENTICATION_BACKENDS`/`SOCIALACCOUNT_PROVIDERS` in `backend/config/settings.py`). DRF's `DEFAULT_PERMISSION_CLASSES` is `IsAuthenticated` globally in settings, not opt-in per view, so a new endpoint is closed by default. In production the `frontend` nginx container is the only thing Cloudflare Tunnel points at; it reverse-proxies `/api/`, `/admin/`, `/accounts/`, `/static/` back to the `web` container (see `frontend/nginx.conf`) so the browser only ever sees one origin.

**Docker/host layout:** source videos are a read-only bind mount at `VIDEO_ROOT` (host: `/mnt/data/samba_share/Video/Lewis_family_videos`), exports go to `OUTPUT_ROOT` (host: `.../Lewis_video_clips`), and Postgres data/media/preview-clip-cache/logs live under `APPDATA_ROOT` (host: `/mnt/fast_storage/appdata/sceneclip`), following the convention of sibling projects under `/mnt/fast_storage/appdata/`. All containers join the external `traefik_proxy` docker network solely so Cloudflare Tunnel can resolve `frontend` by container name — there's no Traefik/nginx-proxy auth layer in this stack.
