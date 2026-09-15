# DRF viewsets for the whole API surface: videos, their detection runs and
# proposed boundaries, the derived scenes, and polled notifications.
from django.db.models import Exists, OuterRef
from django.utils import timezone
from rest_framework import status, viewsets
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
from .services.library import sync_library
from .services.range_response import serve_file_with_range
from .services.scenes import rebuild_scenes
from .services.thumbnail import ensure_thumbnail
from .tasks import export_video_task, generate_video_metadata_task, run_detection_task


def _global_default_params() -> dict:
    """Reads the singleton DetectionParams row, creating it with model
    defaults on first use, and returns it as a plain dict of detector knobs.
    """
    obj, _ = DetectionParams.objects.get_or_create(pk=1)
    return {
        "detector": obj.detector,
        "threshold": obj.threshold,
        "min_scene_len_seconds": obj.min_scene_len_seconds,
    }


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
        params = request.data.get("params") or video.detection_params_override or _global_default_params()

        if request.data.get("save_as_override"):
            video.detection_params_override = params
            video.save(update_fields=["detection_params_override"])

        run = DetectionRun.objects.create(video=video, params=params)
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


class SceneBoundaryViewSet(viewsets.ReadOnlyModelViewSet):
    """Boundaries are only ever created by a detection run (see tasks.py),
    never directly through this API -- so this viewset is read-only plus two
    custom actions: `queue` (next thing to review) and `review` (record a
    verdict), and `clip` to fetch the generated preview clip for one."""

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

        boundary.review_status = verdict
        boundary.reviewed_at = timezone.now()
        boundary.matched_from = None
        boundary.save(update_fields=["review_status", "reviewed_at", "matched_from"])
        rebuild_scenes(boundary.video)
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


class SceneViewSet(viewsets.ModelViewSet):
    """Scenes are auto-derived by rebuild_scenes() from approved boundaries,
    but this is a full ModelViewSet because the enrichment fields
    (description, scene_date) are edited directly by the user through
    PATCH, not through a detection run."""

    queryset = Scene.objects.select_related("video")
    serializer_class = SceneSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        video_id = self.request.query_params.get("video")
        if video_id:
            qs = qs.filter(video_id=video_id)
        return qs


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only + mark_read; the frontend polls GET /api/notifications/ on
    an interval rather than this pushing anything (see App.jsx)."""

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
