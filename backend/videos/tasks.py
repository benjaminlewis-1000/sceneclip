# Celery tasks: the two long-running jobs (scene detection, final export)
# that the dashboard kicks off and then polls Notification rows to find out
# about, rather than waiting on the request; plus a lightweight background
# metadata backfill so newly-discovered videos don't block the request that
# lists them.
import subprocess

from celery import shared_task
from django.utils import timezone

from .models import DetectionRun, Notification, Scene, SceneBoundary, Video
from .services.detection import run_detection
from .services.matching import carry_forward_reviews
from .services.probe import probe_duration_seconds
from .services.scenes import rebuild_scenes, scenes_ready_to_encode
from .services.thumbnail import ensure_thumbnail


def trigger_auto_encode(video) -> None:
    """Queues an encode task for every scene that just became eligible
    (closed, dated, not already exported/encoding) -- called after any
    rebuild_scenes(). Sets export_progress_percent=0 here, synchronously,
    before .delay() -- same reasoning as Video.status flipping at queue
    time in the detect view: without it, a scene queued behind others in
    a busy worker would show no sign anything had happened."""
    for scene in scenes_ready_to_encode(video):
        scene.export_progress_percent = 0
        scene.save(update_fields=["export_progress_percent"])
        export_scene_task.delay(scene.id)


@shared_task
def generate_video_metadata_task(video_id: int):
    """Probes duration and pre-generates the thumbnail for one video. Run
    per-video after sync/create so the list endpoint stays instant -- the
    frontend shows a placeholder card until duration_seconds is populated,
    which is what signals this task has finished.

    Deliberately swallows ffmpeg/ffprobe failures (e.g. an unreadable or
    still-copying file): this is a best-effort backfill, not a job the user
    is waiting on or gets a failure notification for. It just leaves the
    video's card showing a placeholder rather than tanking the whole sync.
    """
    try:
        video = Video.objects.get(id=video_id)
    except Video.DoesNotExist:
        return

    try:
        if video.duration_seconds is None:
            video.duration_seconds = probe_duration_seconds(video.path)
            video.save(update_fields=["duration_seconds"])
        ensure_thumbnail(video)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass


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
        # carry_forward_reviews can auto-approve boundaries that match a
        # prior run's verdict, which can close a scene immediately on a
        # re-run without any explicit review action happening here.
        trigger_auto_encode(video)

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

        # video.status was flipped to DETECTING when this run was queued
        # (see VideoViewSet.detect) and, without this, would stay there
        # forever on failure -- the card would show "Finishing up" with no
        # way out. Revert to whatever's actually true: REVIEWING if earlier
        # boundaries already exist (this failed run just didn't add
        # anything new), otherwise back to PENDING.
        video.status = Video.Status.REVIEWING if video.boundaries.exists() else Video.Status.PENDING
        video.save(update_fields=["status"])

        Notification.objects.create(
            video=video,
            kind=Notification.Kind.DETECTION_FAILED,
            message=f"Scene detection failed for {video.path}: {exc}",
        )
        raise


@shared_task(bind=True)
def export_scene_task(self, scene_id: int):
    """Encodes a single scene -- the primary encode path now, triggered
    automatically as soon as a scene closes (trigger_auto_encode) or
    manually for the trailing open-ended scene via the API."""
    from .services.export import export_one_scene

    scene = Scene.objects.select_related("video").get(id=scene_id)
    video = scene.video
    scene.export_progress_percent = 0
    scene.save(update_fields=["export_progress_percent"])

    def on_progress(fraction: float) -> None:
        Scene.objects.filter(id=scene.id).update(export_progress_percent=min(99, int(fraction * 100)))

    try:
        export_one_scene(scene, on_progress=on_progress)
        Notification.objects.create(
            video=video,
            kind=Notification.Kind.EXPORT_DONE,
            message=f"Encoded scene {scene.start_seconds:.0f}s-{scene.end_seconds:.0f}s for {video.path}.",
        )
    except Exception as exc:
        Scene.objects.filter(id=scene.id).update(export_progress_percent=None)
        Notification.objects.create(
            video=video,
            kind=Notification.Kind.EXPORT_FAILED,
            message=f"Encoding failed for a scene in {video.path}: {exc}",
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
