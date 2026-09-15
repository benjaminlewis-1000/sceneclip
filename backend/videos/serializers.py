# One ModelSerializer per model, with read_only_fields locking down anything
# that should only ever be set by server-side logic (detection results,
# timestamps, computed status) rather than accepted from the client.
from rest_framework import serializers

from .models import DetectionParams, DetectionRun, Notification, Scene, SceneBoundary, Video


class DetectionParamsSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetectionParams
        fields = ["detector", "threshold", "min_scene_len_seconds", "updated_at"]


class VideoSerializer(serializers.ModelSerializer):
    # Progress of the most recent still-running DetectionRun, if any --
    # lets the library list show a progress bar without a second request
    # per video.
    detection_progress_percent = serializers.SerializerMethodField()
    # Distinguishes "queued behind other work, hasn't started" from
    # "actively running" -- both look like 0% progress otherwise, which
    # reads as stuck when a video is just waiting its turn behind a big
    # backlog (worker concurrency is 2; a "Process all" or the orphan-sweep
    # auto-retry can queue dozens at once).
    detection_run_status = serializers.SerializerMethodField()
    # Drive the frontend's button disabling: "Review this video" needs
    # something to review, "Export clips" needs at least one approved cut
    # to actually produce a chapter.
    has_boundaries = serializers.SerializerMethodField()
    has_approved_boundaries = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = [
            "id", "path", "duration_seconds", "status", "marked_done",
            "export_progress_percent", "detection_progress_percent",
            "detection_run_status", "has_boundaries", "has_approved_boundaries",
            "detection_params_override", "created_at", "updated_at",
        ]
        read_only_fields = [
            "duration_seconds", "status", "export_progress_percent",
            "created_at", "updated_at",
        ]

    def _latest_run(self, obj):
        if obj.status != Video.Status.DETECTING:
            return None
        if not hasattr(obj, "_latest_run_cache"):
            obj._latest_run_cache = obj.runs.order_by("-created_at").first()
        return obj._latest_run_cache

    def get_detection_progress_percent(self, obj):
        latest_run = self._latest_run(obj)
        return latest_run.progress_percent if latest_run else None

    def get_detection_run_status(self, obj):
        latest_run = self._latest_run(obj)
        return latest_run.status if latest_run else None

    def get_has_boundaries(self, obj):
        # VideoViewSet.get_queryset() annotates this with a single EXISTS
        # subquery for the whole list; fall back to a per-object query for
        # anything serialized outside that queryset (e.g. the response to
        # a fresh POST).
        annotated = getattr(obj, "has_boundaries", None)
        return annotated if annotated is not None else obj.boundaries.exists()

    def get_has_approved_boundaries(self, obj):
        annotated = getattr(obj, "has_approved_boundaries", None)
        if annotated is not None:
            return annotated
        return obj.boundaries.filter(review_status=SceneBoundary.ReviewStatus.APPROVED).exists()


class DetectionRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = DetectionRun
        fields = [
            "id", "video", "params", "status", "progress_percent",
            "error_message", "created_at", "finished_at",
        ]
        read_only_fields = ["status", "progress_percent", "error_message", "created_at", "finished_at"]


class SceneBoundarySerializer(serializers.ModelSerializer):
    video_path = serializers.CharField(source="video.path", read_only=True)

    class Meta:
        model = SceneBoundary
        fields = [
            "id", "video", "video_path", "run", "timestamp_seconds",
            "review_status", "reviewed_at", "matched_from", "created_at",
            "before_description", "before_date", "after_description", "after_date",
        ]
        # review_status/reviewed_at stay read-only here: the dedicated
        # `review` action is the only path that should set them, since it
        # also triggers rebuild_scenes() as a side effect -- a plain PATCH
        # setting review_status directly would silently skip that.
        read_only_fields = [
            "video", "run", "timestamp_seconds", "matched_from", "created_at",
            "review_status", "reviewed_at",
        ]


class SceneSerializer(serializers.ModelSerializer):
    video_path = serializers.CharField(source="video.path", read_only=True)
    # Mirrors VideoSerializer.detection_run_status: distinguishes "queued
    # behind other work" from "actually encoding right now," both of which
    # otherwise look identical (export_progress_percent == 0).
    encode_status = serializers.SerializerMethodField()

    class Meta:
        model = Scene
        fields = [
            "id", "video", "video_path", "start_boundary", "end_boundary", "start_seconds",
            "end_seconds", "description", "scene_date", "exported",
            "exported_path", "export_progress_percent", "encode_status", "verified",
            "created_at", "updated_at",
        ]
        # verified only changes through the dedicated `verify` action (which
        # also enforces the date requirement); exported*/progress are
        # task-owned.
        read_only_fields = [
            "exported", "exported_path", "export_progress_percent", "verified",
            "created_at", "updated_at",
        ]

    def get_encode_status(self, obj):
        if obj.export_progress_percent is None:
            return None
        return "encoding" if obj.encode_started_at else "queued"


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "video", "kind", "message", "read", "created_at"]
        read_only_fields = ["video", "kind", "message", "created_at"]
