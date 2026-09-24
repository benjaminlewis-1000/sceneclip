# DRF viewsets for the whole API surface: videos, their detection runs and
# proposed boundaries, the derived scenes, and polled notifications.
from django.db.models import Count, Exists, OuterRef
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DetectionParams, DetectionRun, Notification, Scene, SceneBoundary, Video
from .serializers import (
    DetectionParamsSerializer,
    DetectionRunSerializer,
    NotificationSerializer,
    SceneBoundarySerializer,
    SceneSerializer,
    VideoSerializer,
)
from .services.browse import InvalidBrowsePath, list_directory
from .services.clips import ensure_preview_clip
from .services.duplicates import mark_duplicate, unmark_duplicate
from .services.export import finalize_scene
from .services.library import sync_library
from .services.params import resolve_detection_params
from .services.range_response import serve_file_with_range
from .services.scenes import SceneStillEncoding, rebuild_scenes, undo_boundary_review
from .services.thumbnail import ensure_thumbnail
from .tasks import (
    export_scene_task,
    export_video_task,
    generate_video_metadata_task,
    run_detection_task,
    trigger_auto_encode,
)


class DetectionParamsView(APIView):
    """Singleton endpoint (no pk in the URL) for the library-wide default
    detection knobs."""

    def get(self, request):
        obj, _ = DetectionParams.objects.get_or_create(pk=1)
        return Response(DetectionParamsSerializer(obj).data)

    def put(self, request):
        obj, _ = DetectionParams.objects.get_or_create(pk=1)
        serializer = DetectionParamsSerializer(obj, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


CLEAR_DATABASE_CONFIRM_PHRASE = "CLEAR"


class ClearDatabaseView(APIView):
    """Wipes every Video (and, via CASCADE, every DetectionRun/
    SceneBoundary/Scene/Notification) -- source files on disk are untouched,
    this only resets the library back to "nothing scanned yet". Requires the
    exact confirm phrase in the request body as a second guard beyond the
    frontend's own confirmation UI, since this is destructive and
    irreversible: a stray automated retry of a bare POST won't trigger it.
    """

    def post(self, request):
        if request.data.get("confirm") != CLEAR_DATABASE_CONFIRM_PHRASE:
            return Response(
                {"error": f"Must include confirm: \"{CLEAR_DATABASE_CONFIRM_PHRASE}\" to proceed."},
                status=400,
            )
        deleted_count, _ = Video.objects.all().delete()
        return Response({"deleted": deleted_count})


class TaskQueueView(APIView):
    """Read-only snapshot of everything currently detecting or encoding
    library-wide, running work first then queued -- backs the Settings
    page's "Task queue" section so it's possible to see at a glance that a
    big backlog is actually moving, not just watch one video's card at a
    time."""

    def get(self, request):
        runs = list(
            DetectionRun.objects.filter(
                status__in=[DetectionRun.Status.QUEUED, DetectionRun.Status.RUNNING]
            ).select_related("video").order_by("created_at")
        )
        runs.sort(key=lambda r: 0 if r.status == DetectionRun.Status.RUNNING else 1)
        detection = [
            {
                "run_id": r.id,
                "video_id": r.video_id,
                "video_path": r.video.path,
                "status": r.status,
                "progress_percent": r.progress_percent,
                "created_at": r.created_at,
            }
            for r in runs
        ]

        scenes = list(
            Scene.objects.filter(export_progress_percent__isnull=False)
            .select_related("video").order_by("created_at")
        )
        scenes.sort(key=lambda s: 0 if s.encode_started_at else 1)
        encoding = [
            {
                "scene_id": s.id,
                "video_id": s.video_id,
                "video_path": s.video.path,
                "start_seconds": s.start_seconds,
                "end_seconds": s.end_seconds,
                "status": "encoding" if s.encode_started_at else "queued",
                "progress_percent": s.export_progress_percent,
            }
            for s in scenes
        ]

        return Response({"detection": detection, "encoding": encoding})


class VideoViewSet(viewsets.ModelViewSet):
    """Standard CRUD for Video rows, plus three async-job actions: kick off
    a detection run, kick off a final export, and stream the raw source file
    (for the "watch full scene" playback the frontend does via #t=start,end
    media-fragment URLs)."""

    queryset = Video.objects.all()
    serializer_class = VideoSerializer

    def get_queryset(self):
        # Annotates has_boundaries/has_approved_boundaries with a single
        # EXISTS subquery each rather than the serializer calling
        # obj.boundaries.exists() per row -- on a library-sized list that
        # was 2 extra queries per video (148 for 74 videos), noticeably
        # slowing down exactly the request the library page waits on right
        # after a scan.
        boundary_qs = SceneBoundary.objects.filter(video=OuterRef("pk"))
        approved_qs = boundary_qs.filter(review_status=SceneBoundary.ReviewStatus.APPROVED)
        return Video.objects.annotate(
            has_boundaries=Exists(boundary_qs),
            has_approved_boundaries=Exists(approved_qs),
        )

    def perform_create(self, serializer):
        # Manually-added videos (via the directory browser or a typed path)
        # get the same instant-list/backfilled-metadata treatment as a
        # synced one.
        video = serializer.save()
        generate_video_metadata_task.delay(video.id)

    @action(detail=False, methods=["post"])
    def sync(self, request):
        # Walks VIDEO_ROOT for video files not already known and adds them --
        # this is what makes the library page "just show up populated"
        # rather than requiring every file to be added by hand. Returns as
        # soon as the rows exist; duration/thumbnail are backfilled by a
        # Celery task per video so this request (and the page render that
        # follows it) isn't blocked on dozens of ffmpeg calls.
        created = sync_library()
        for video in created:
            generate_video_metadata_task.delay(video.id)
        return Response(VideoSerializer(created, many=True).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def browse(self, request):
        # Backs the "pick a different file" UI. Scoped to VIDEO_ROOT --
        # the container has no visibility into the rest of the host
        # filesystem, so browsing outside it isn't possible without adding
        # another bind mount to docker-compose.yml first.
        try:
            return Response(list_directory(request.query_params.get("path", "")))
        except InvalidBrowsePath as exc:
            return Response({"error": str(exc)}, status=400)

    @action(detail=True, methods=["get"])
    def thumbnail(self, request, pk=None):
        video = self.get_object()
        path = ensure_thumbnail(video)
        return serve_file_with_range(request, path, content_type="image/jpeg")

    @action(detail=True, methods=["post"])
    def detect(self, request, pk=None):
        # Params come from the request body if given, else the video's own
        # saved override, else the library-wide default -- in that order.
        video = self.get_object()
        params = request.data.get("params") or resolve_detection_params(video)

        if request.data.get("save_as_override"):
            video.detection_params_override = params
            video.save(update_fields=["detection_params_override"])

        run = DetectionRun.objects.create(video=video, params=params)
        # Flips to DETECTING here, at queue time -- not inside the task once
        # a worker slot actually picks it up. With worker concurrency capped
        # (2 by default), queuing a 3rd/4th video used to leave its
        # Video.status looking like "pending" until a slot freed up, so the
        # UI showed no sign anything had happened and its Reprocess button
        # stayed enabled, inviting a duplicate click.
        video.status = Video.Status.DETECTING
        video.save(update_fields=["status"])
        run_detection_task.delay(run.id)
        return Response(DetectionRunSerializer(run).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"])
    def export(self, request, pk=None):
        # Fire-and-forget: the Celery worker does the ffmpeg work and drops a
        # Notification row when it's done, which the frontend picks up by
        # polling /api/notifications/.
        video = self.get_object()
        export_video_task.delay(video.id)
        return Response({"status": "queued"}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"])
    def stream(self, request, pk=None):
        # Byte-range streaming of the raw source file so <video> can seek.
        video = self.get_object()
        return serve_file_with_range(request, video.path)

    @action(detail=True, methods=["post"])
    def mark_duplicate(self, request, pk=None):
        # `self` (the video this action is called on) is the losing copy --
        # its clips get deleted; `keep` (given in the body) is untouched.
        duplicate = self.get_object()
        keep_id = request.data.get("keep")
        try:
            keep = Video.objects.get(id=keep_id)
        except (Video.DoesNotExist, ValueError, TypeError):
            return Response({"error": "keep must be the id of another video."}, status=400)
        if keep.id == duplicate.id:
            return Response({"error": "A video can't be marked a duplicate of itself."}, status=400)
        try:
            mark_duplicate(keep, duplicate)
        except SceneStillEncoding as exc:
            return Response({"error": str(exc)}, status=400)
        return Response(VideoSerializer(duplicate).data)

    @action(detail=True, methods=["post"])
    def unmark_duplicate(self, request, pk=None):
        video = self.get_object()
        if not video.duplicate_of_id:
            return Response({"error": "Not marked as a duplicate."}, status=400)
        unmark_duplicate(video)
        return Response(VideoSerializer(video).data)


class SceneBoundaryViewSet(
    mixins.UpdateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet
):
    """Boundaries are only ever created by a detection run (see tasks.py),
    never directly through this API, and their verdict only changes through
    the dedicated `review` action (see the serializer for why) -- so no
    create/destroy here. PATCH is enabled only for the before/after_*
    enrichment fields (everything else is read-only on the serializer), plus
    custom actions: `queue` (next thing to review), `review` (record a
    verdict), `adjust` (nudge the timestamp), and `clip` (the preview clip)."""

    queryset = SceneBoundary.objects.select_related("video")
    serializer_class = SceneBoundarySerializer

    def get_queryset(self):
        # Supports ?video=<id> and ?status=pending|approved|rejected filtering,
        # used by both the review queue and the tile-grid backup view.
        qs = super().get_queryset()
        video_id = self.request.query_params.get("video")
        review_status = self.request.query_params.get("status")
        if video_id:
            qs = qs.filter(video_id=video_id)
        if review_status:
            qs = qs.filter(review_status=review_status)
        return qs

    @action(detail=False, methods=["get"])
    def queue(self, request):
        """Next pending boundary across the whole library, video-then-time ordered,
        for the auto-advancing review queue view."""
        boundary = (
            self.get_queryset()
            .filter(review_status=SceneBoundary.ReviewStatus.PENDING)
            .order_by("video_id", "timestamp_seconds")
            .first()
        )
        return Response(SceneBoundarySerializer(boundary).data if boundary else None)

    @action(detail=False, methods=["get"])
    def review_summary(self, request):
        """One row per video that still has pending boundaries -- backs the
        Review Queue's "videos to review" list, mirroring SceneViewSet.
        verify_summary."""
        rows = (
            self.get_queryset()
            .filter(review_status=SceneBoundary.ReviewStatus.PENDING)
            .values("video_id", "video__path", "video__recorded_date")
            .annotate(count=Count("id"))
            .order_by("video__path")
        )
        return Response(
            [
                {
                    "video_id": r["video_id"],
                    "video_path": r["video__path"],
                    "recorded_date": r["video__recorded_date"],
                    "count": r["count"],
                }
                for r in rows
            ]
        )

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        # Records the human verdict (the hotkey/tile-click target), then
        # immediately recomputes the video's Scene rows since an approved
        # boundary changes where chapter cuts fall.
        boundary = self.get_object()
        verdict = request.data.get("verdict")
        valid = {SceneBoundary.ReviewStatus.APPROVED, SceneBoundary.ReviewStatus.REJECTED}
        if verdict not in valid:
            return Response({"error": "verdict must be 'approved' or 'rejected'"}, status=400)

        # A date is required before advancing at all -- it ends up baked
        # into the encoded clip's metadata, so letting a scene go undated
        # here just means re-encoding later once someone notices. Approve
        # closes two genuinely different scenes (before and after), so both
        # need a date; reject means there was no real cut -- before/after
        # describe the same continuous scene -- so only before_date matters
        # (frontend already falls back to it when after is left blank).
        if verdict == SceneBoundary.ReviewStatus.APPROVED:
            missing = not boundary.before_date or not boundary.after_date
        else:
            missing = not boundary.before_date
        if missing:
            return Response(
                {"error": "A date is required before this boundary can be reviewed."}, status=400
            )

        boundary.review_status = verdict
        boundary.reviewed_at = timezone.now()
        boundary.matched_from = None
        boundary.save(update_fields=["review_status", "reviewed_at", "matched_from"])
        rebuild_scenes(boundary.video)
        trigger_auto_encode(boundary.video)
        return Response(SceneBoundarySerializer(boundary).data)

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        # Lets the reviewer nudge a candidate's exact timestamp within its
        # preview clip (frame-scrubbed on the frontend) before approving it
        # -- PySceneDetect's candidate is a good starting point, not always
        # frame-accurate on noisy VHS source. Re-centers the cached preview
        # clip and any derived Scene edges on the new timestamp.
        boundary = self.get_object()
        try:
            new_timestamp = float(request.data.get("timestamp_seconds"))
        except (TypeError, ValueError):
            return Response({"error": "timestamp_seconds must be a number"}, status=400)

        boundary.timestamp_seconds = new_timestamp
        boundary.save(update_fields=["timestamp_seconds"])
        rebuild_scenes(boundary.video)
        return Response(SceneBoundarySerializer(boundary).data)

    @action(detail=True, methods=["get"])
    def clip(self, request, pk=None):
        # Generates the +/-5s preview clip on first request (cached on disk
        # after that) and streams it with range support.
        boundary = self.get_object()
        path = ensure_preview_clip(boundary.video, boundary)
        return serve_file_with_range(request, path)

    @action(detail=True, methods=["post"])
    def undo(self, request, pk=None):
        # Reverts an approved/rejected boundary back to pending -- see
        # services/scenes.py:undo_boundary_review for what this does to any
        # scene that boundary borders.
        boundary = self.get_object()
        if boundary.review_status == SceneBoundary.ReviewStatus.PENDING:
            return Response({"error": "Already pending."}, status=400)
        undo_boundary_review(boundary)
        boundary.refresh_from_db()
        return Response(SceneBoundarySerializer(boundary).data)


class SceneViewSet(viewsets.ModelViewSet):
    """Scenes are auto-derived by rebuild_scenes() from approved boundaries,
    but this is a full ModelViewSet because the enrichment fields
    (description, scene_date) are edited directly by the user through
    PATCH, not through a detection run. Most scenes encode automatically as
    soon as they close (see tasks.trigger_auto_encode); `encode` is the
    manual trigger for the one that doesn't -- the trailing, still-open
    scene with no end_boundary yet."""

    queryset = Scene.objects.select_related("video")
    serializer_class = SceneSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        video_id = self.request.query_params.get("video")
        if video_id:
            qs = qs.filter(video_id=video_id)
        return qs

    @action(detail=True, methods=["post"])
    def encode(self, request, pk=None):
        scene = self.get_object()
        if not scene.scene_date:
            return Response({"error": "A date is required before this scene can be encoded."}, status=400)
        if scene.exported:
            return Response({"error": "Already encoded."}, status=400)
        if scene.export_progress_percent is not None:
            return Response({"error": "Already encoding."}, status=400)

        scene.export_progress_percent = 0
        scene.save(update_fields=["export_progress_percent"])
        result = export_scene_task.delay(scene.id)
        Scene.objects.filter(id=scene.id).update(encode_task_id=result.id)
        return Response(SceneSerializer(scene).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"])
    def clip(self, request, pk=None):
        # The actual encoded output, not the raw (often browser-incompatible
        # -- see CLAUDE.md) source -- this is what "Watch" and the Verify
        # queue play.
        scene = self.get_object()
        if not scene.exported or not scene.exported_path:
            return Response({"error": "Not encoded yet."}, status=404)
        return serve_file_with_range(request, scene.exported_path)

    @action(detail=False, methods=["get"])
    def verify_summary(self, request):
        """One row per source video that still has unverified encoded
        scenes -- backs the Verify page's "videos to check" list, so you
        can jump straight to a specific video instead of only ever seeing
        the single global next-up scene."""
        rows = (
            Scene.objects.filter(exported=True, verified=False)
            .values("video_id", "video__path", "video__recorded_date")
            .annotate(count=Count("id"))
            .order_by("video__path")
        )
        return Response(
            [
                {
                    "video_id": r["video_id"],
                    "video_path": r["video__path"],
                    "recorded_date": r["video__recorded_date"],
                    "count": r["count"],
                }
                for r in rows
            ]
        )

    @action(detail=False, methods=["get"])
    def verify_queue(self, request):
        """Next encoded-but-unverified scene, for the auto-advancing Verify
        queue view. Optional ?video= scopes to one video. Optional
        ?exclude=<comma-separated ids> skips scenes the reviewer has
        already hit "Skip" on this session -- a session-only skip, not a
        persisted verdict, so they're still reachable normally later (a
        page reload clears it)."""
        qs = self.get_queryset().filter(exported=True, verified=False)
        exclude_param = request.query_params.get("exclude")
        if exclude_param:
            exclude_ids = [int(x) for x in exclude_param.split(",") if x.strip().isdigit()]
            qs = qs.exclude(id__in=exclude_ids)
        scene = qs.order_by("video_id", "start_seconds").first()
        return Response(SceneSerializer(scene).data if scene else None)

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        # The one workflow behind both entry points: the Verify Queue page
        # and the per-scene "Verify" button on VideoDetail both hit this
        # same action -- moves the clip out of TEMP_SCENE_CLIPS_DIR into
        # OUTPUT_ROOT (see services/export.py:finalize_scene) and marks it
        # verified, so OUTPUT_ROOT only ever holds clips a human actually
        # confirmed are right.
        scene = self.get_object()
        if not scene.exported:
            return Response({"error": "Not encoded yet."}, status=400)
        if scene.verified:
            return Response({"error": "Already verified."}, status=400)
        if not scene.scene_date:
            return Response({"error": "A date is required before this scene can be verified."}, status=400)
        finalize_scene(scene)
        return Response(SceneSerializer(scene).data)


class NotificationViewSet(mixins.DestroyModelMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only + mark_read, plus clearing (individually via DELETE, or all
    at once) -- the frontend polls GET /api/notifications/ on an interval
    rather than this pushing anything (see App.jsx)."""

    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer

    def get_queryset(self):
        # ?since=<ISO timestamp> lets a poller ask for only what's new.
        qs = super().get_queryset()
        since = self.request.query_params.get("since")
        if since:
            qs = qs.filter(created_at__gt=since)
        return qs

    @action(detail=True, methods=["post"])
    def mark_read(self, request, pk=None):
        notif = self.get_object()
        notif.read = True
        notif.save(update_fields=["read"])
        return Response(NotificationSerializer(notif).data)

    @action(detail=False, methods=["post"])
    def clear_all(self, request):
        deleted_count, _ = Notification.objects.all().delete()
        return Response({"deleted": deleted_count})
