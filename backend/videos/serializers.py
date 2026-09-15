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

    class Meta:
        model = Video
        fields = [
            "id", "path", "duration_seconds", "status", "marked_done",
            "export_progress_percent", "detection_progress_percent",
            "detection_params_override", "created_at", "updated_at",
        ]
        read_only_fields = [
            "duration_seconds", "status", "export_progress_percent",
            "created_at", "updated_at",
        ]

    def get_detection_progress_percent(self, obj):
        if obj.status != Video.Status.DETECTING:
            return None
        latest_run = obj.runs.order_by("-created_at").first()
        return latest_run.progress_percent if latest_run else None


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
        ]
        read_only_fields = ["video", "run", "timestamp_seconds", "matched_from", "created_at"]


class SceneSerializer(serializers.ModelSerializer):
    class Meta:
        model = Scene
        fields = [
            "id", "video", "start_boundary", "end_boundary", "start_seconds",
            "end_seconds", "description", "scene_date", "exported",
            "exported_path", "created_at", "updated_at",
        ]
        read_only_fields = ["exported", "exported_path", "created_at", "updated_at"]


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "video", "kind", "message", "read", "created_at"]
        read_only_fields = ["video", "kind", "message", "created_at"]
