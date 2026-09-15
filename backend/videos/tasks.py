# Celery tasks: the two long-running jobs (scene detection, final export)
# that the dashboard kicks off and then polls Notification rows to find out
# about, rather than waiting on the request.
from celery import shared_task
from django.utils import timezone

from .models import DetectionRun, Notification, SceneBoundary, Video
from .services.detection import run_detection
from .services.matching import carry_forward_reviews
from .services.probe import probe_duration_seconds
from .services.scenes import rebuild_scenes


@shared_task(bind=True)
def run_detection_task(self, run_id: int):
    """Runs PySceneDetect for one DetectionRun, saves the candidate
    boundaries, carries forward any prior review verdicts that still match,
    and rebuilds the video's Scene rows so already-approved cuts are
    reflected immediately."""
    run = DetectionRun.objects.select_related("video").get(id=run_id)
    video = run.video

    run.status = DetectionRun.Status.RUNNING
    run.save(update_fields=["status"])
    video.status = Video.Status.DETECTING
    video.save(update_fields=["status"])

    def on_progress(percent: int) -> None:
        DetectionRun.objects.filter(id=run.id).update(progress_percent=percent)

    try:
        # Needed as the end-of-video cut point when deriving Scene rows;
        # only probed once per video.
        if video.duration_seconds is None:
            video.duration_seconds = probe_duration_seconds(video.path)
            video.save(update_fields=["duration_seconds"])

        timestamps = run_detection(video.path, run.params, progress_callback=on_progress)
        boundaries = SceneBoundary.objects.bulk_create(
            [SceneBoundary(video=video, run=run, timestamp_seconds=ts) for ts in timestamps]
        )
        carry_forward_reviews(video, boundaries)
        rebuild_scenes(video)

        run.status = DetectionRun.Status.DONE
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])

        video.status = Video.Status.REVIEWING
        video.save(update_fields=["status"])

        Notification.objects.create(
            video=video,
            kind=Notification.Kind.DETECTION_DONE,
            message=f"Scene detection finished for {video.path}: {len(timestamps)} candidate boundaries.",
        )
    except Exception as exc:
        run.status = DetectionRun.Status.FAILED
        run.error_message = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_message", "finished_at"])

        Notification.objects.create(
            video=video,
            kind=Notification.Kind.DETECTION_FAILED,
            message=f"Scene detection failed for {video.path}: {exc}",
        )
        raise


@shared_task(bind=True)
def export_video_task(self, video_id: int):
    """Cuts every not-yet-exported Scene out of the source video into
    OUTPUT_ROOT with the enrichment fields written in as metadata."""
    from .services.export import export_scenes

    video = Video.objects.get(id=video_id)
    video.export_progress_percent = 0
    video.save(update_fields=["export_progress_percent"])

    def on_progress(percent: int) -> None:
        Video.objects.filter(id=video.id).update(export_progress_percent=percent)

    try:
        exported = export_scenes(video, progress_callback=on_progress)
        video.status = Video.Status.EXPORTED
        video.export_progress_percent = None
        video.save(update_fields=["status", "export_progress_percent"])
        Notification.objects.create(
            video=video,
            kind=Notification.Kind.EXPORT_DONE,
            message=f"Exported {len(exported)} scene(s) for {video.path}.",
        )
    except Exception as exc:
        video.export_progress_percent = None
        video.save(update_fields=["export_progress_percent"])
        Notification.objects.create(
            video=video,
            kind=Notification.Kind.EXPORT_FAILED,
            message=f"Export failed for {video.path}: {exc}",
        )
        raise
