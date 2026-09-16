# The core data model:
#   Video -> DetectionRun -> SceneBoundary (candidate cuts, human-reviewed)
#   Video -> Scene (the chapters derived from approved boundaries, enriched
#            with description/date, eventually exported)
#   Video/-> Notification (what the polled notifications tab reads)
from django.db import models


class Video(models.Model):
    """One source file under VIDEO_ROOT. Tracks its own review/export
    progress via `status`."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DETECTING = "detecting", "Detecting"
        REVIEWING = "reviewing", "Reviewing"
        EXPORTED = "exported", "Exported"

    path = models.CharField(max_length=1024, unique=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # Per-video override of detection knobs; falls back to the singleton
    # DetectionParams row when null.
    detection_params_override = models.JSONField(null=True, blank=True)
    # A human "I'm done with this one" flag -- purely a visual/organizational
    # marker on the library page, independent of and reversible regardless of
    # `status` (re-processing or re-exporting a marked-done video is fine).
    marked_done = models.BooleanField(default=False)
    # Set when this video has been identified as a re-digitized/duplicate
    # capture of another Video already in the library (see
    # services/duplicates.py) -- the losing copy's encoded clips get
    # deleted from disk to reclaim space, but the Video/DetectionRun/
    # SceneBoundary/Scene rows themselves are kept, not deleted, so the
    # decision and its history stay visible and reversible.
    duplicate_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates"
    )
    # 0-100, set by export_video_task as it works through each scene's
    # ffmpeg cut; null when no export is in flight.
    export_progress_percent = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.path


class DetectionParams(models.Model):
    """Singleton (pk=1) row holding the library-wide default detection knobs."""

    detector = models.CharField(max_length=50, default="adaptive")
    threshold = models.FloatField(default=3.0)
    min_scene_len_seconds = models.FloatField(default=2.0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Global default detection params"


class DetectionRun(models.Model):
    """One invocation of PySceneDetect against a Video with a specific set of
    params. Kept as its own row (rather than overwriting boundaries in
    place) so re-running with different knobs doesn't lose the history that
    carry_forward_reviews() needs to match old verdicts to new candidates."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    video = models.ForeignKey(Video, related_name="runs", on_delete=models.CASCADE)
    params = models.JSONField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    # 0-100, updated from PySceneDetect's per-frame callback as it works
    # through the video (see services/detection.py).
    progress_percent = models.IntegerField(default=0)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Run {self.id} for video {self.video_id}"


class SceneBoundary(models.Model):
    """A single candidate cut point at a timestamp, produced by a
    DetectionRun and reviewed (approved/rejected) by a human."""

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    video = models.ForeignKey(Video, related_name="boundaries", on_delete=models.CASCADE)
    run = models.ForeignKey(DetectionRun, related_name="boundaries", on_delete=models.CASCADE)
    timestamp_seconds = models.FloatField()
    review_status = models.CharField(
        max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    # Set when this boundary's verdict was carried forward from a prior run's
    # matching boundary (see videos/services/matching.py) rather than reviewed
    # directly.
    matched_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="carried_forward_to"
    )
    # Free-text log of what's happening in the footage immediately before
    # and after this specific transition, entered during review. Deliberately
    # attached to the boundary rather than to Scene (which only exists for
    # segments between *approved* cuts, and gets rebuilt/deleted as review
    # progresses) -- a boundary always has a well-defined before/after
    # regardless of its own verdict. The frontend carries a boundary's
    # after_* values forward as the next boundary's before_* on advance,
    # since chronologically they describe the same stretch of footage.
    before_description = models.TextField(blank=True, default="")
    before_date = models.DateField(null=True, blank=True)
    after_description = models.TextField(blank=True, default="")
    after_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["video_id", "timestamp_seconds"]
        indexes = [
            models.Index(fields=["video", "review_status"]),
        ]

    def __str__(self):
        return f"video={self.video_id} @ {self.timestamp_seconds:.2f}s ({self.review_status})"


class Scene(models.Model):
    """A chapter between two approved boundaries (or the video's start/end).
    Derived automatically by videos/services/scenes.py:rebuild_scenes(), but
    its enrichment fields are edited directly by the user."""

    video = models.ForeignKey(Video, related_name="scenes", on_delete=models.CASCADE)
    start_boundary = models.ForeignKey(
        SceneBoundary, null=True, blank=True, related_name="scenes_starting", on_delete=models.SET_NULL
    )
    end_boundary = models.ForeignKey(
        SceneBoundary, null=True, blank=True, related_name="scenes_ending", on_delete=models.SET_NULL
    )
    start_seconds = models.FloatField()
    end_seconds = models.FloatField()
    description = models.TextField(blank=True, default="")
    scene_date = models.DateField(null=True, blank=True)
    exported = models.BooleanField(default=False)
    exported_path = models.CharField(max_length=1024, blank=True, default="")
    # Set while a per-scene encode is in flight; None otherwise. Distinct
    # from `exported` so a scene mid-encode isn't re-triggered a second
    # time by the next boundary review (see services/scenes.py's auto-
    # encode trigger).
    export_progress_percent = models.IntegerField(null=True, blank=True)
    # Set the moment export_scene_task actually starts running (as opposed
    # to export_progress_percent, which is set to 0 as soon as the task is
    # merely queued) -- distinguishes "queued behind other work" from
    # "actively encoding" the same way DetectionRun.status does for
    # detection, without needing to poll Celery itself.
    encode_started_at = models.DateTimeField(null=True, blank=True)
    # A human "I watched the final encoded clip and it's right" checkpoint,
    # separate from boundary review -- set via the Verify queue.
    verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["video_id", "start_seconds"]

    def __str__(self):
        return f"video={self.video_id} [{self.start_seconds:.1f}-{self.end_seconds:.1f}]"


class Notification(models.Model):
    """A "job finished" record for the frontend's polled notifications tab.
    Created by the Celery tasks in tasks.py when a run or export ends."""

    class Kind(models.TextChoices):
        DETECTION_DONE = "detection_done", "Detection done"
        DETECTION_FAILED = "detection_failed", "Detection failed"
        EXPORT_DONE = "export_done", "Export done"
        EXPORT_FAILED = "export_failed", "Export failed"

    video = models.ForeignKey(Video, related_name="notifications", on_delete=models.CASCADE)
    kind = models.CharField(max_length=30, choices=Kind.choices)
    message = models.CharField(max_length=512)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.kind}] {self.message}"
