# Celery tasks: the two long-running jobs (scene detection, final export)
# that the dashboard kicks off and then polls Notification rows to find out
# about, rather than waiting on the request; plus a lightweight background
# metadata backfill so newly-discovered videos don't block the request that
# lists them.
import datetime
import subprocess

from celery import shared_task
from django.utils import timezone

from .models import DetectionRun, Notification, Scene, SceneBoundary, Video
from .services.detection import run_detection
from .services.matching import carry_forward_reviews
from .services.probe import probe_duration_seconds
from .services.scenes import rebuild_scenes, scenes_ready_to_encode
from .services.thumbnail import ensure_thumbnail


# How long a run/encode can sit with no completion before the periodic
# sweep (not the startup one, which is unconditional) treats it as
# abandoned rather than just slow. Generous on purpose -- a long VHS tape
# still has to be decoded frame-by-frame, and a false-positive here means
# interrupting something that was actually about to finish.
STALE_DETECTION_RUN_AGE = datetime.timedelta(hours=3)
STALE_SCENE_ENCODE_AGE = datetime.timedelta(hours=1)


def sweep_orphaned_work(only_unconditional: bool = False) -> dict:
    """Recovers DetectionRuns/Scenes left claiming "running"/"mid-encode"
    by a worker process that's gone -- either because this worker just
    started (worker_ready signal, see config/celery.py: with a single
    worker container, anything still marked running at that instant is
    unconditionally orphaned, since the process that was running it can't
    still exist) or, periodically, because a task has been running
    implausibly long for a worker that's still alive (hung ffmpeg/
    PySceneDetect subprocess, OOM-killed child, etc.).

    `only_unconditional=True` (used at startup) skips the age check and
    sweeps every in-progress row regardless of how recently it started --
    right after a restart, "recently started" is exactly the orphaned
    case, not a sign it's still legitimately running.
    """
    now = timezone.now()
    swept = {"runs": 0, "scenes": 0}

    runs = DetectionRun.objects.filter(status=DetectionRun.Status.RUNNING).select_related("video")
    if not only_unconditional:
        runs = runs.filter(created_at__lt=now - STALE_DETECTION_RUN_AGE)
    for run in runs:
        run.status = DetectionRun.Status.FAILED
        run.error_message = "Orphaned: no worker was actually processing this run anymore."
        run.finished_at = now
        run.save(update_fields=["status", "error_message", "finished_at"])

        video = run.video
        had_boundaries = video.boundaries.exists()

        if had_boundaries:
            # Already has usable content from an earlier successful run --
            # this orphaned attempt was just a redundant re-process, not
            # essential missing work. Leave it actionable, don't auto-retry.
            video.status = Video.Status.REVIEWING
            video.save(update_fields=["status"])
            Notification.objects.create(
                video=video,
                kind=Notification.Kind.DETECTION_FAILED,
                message=f"Scene detection for {video.path} was interrupted (worker restarted or hung).",
            )
        else:
            # Nothing survived -- this video still genuinely needs
            # processing, so re-queue it with the same params immediately
            # rather than leaving it sitting at "pending" for someone to
            # notice and re-click Reprocess (or wait for the next manual
            # "Process all").
            new_run = DetectionRun.objects.create(video=video, params=run.params)
            video.status = Video.Status.DETECTING
            video.save(update_fields=["status"])
            run_detection_task.delay(new_run.id)
            Notification.objects.create(
                video=video,
                kind=Notification.Kind.DETECTION_FAILED,
                message=f"Scene detection for {video.path} was interrupted (worker restarted or hung) -- automatically retrying.",
            )
        swept["runs"] += 1

    scenes = Scene.objects.filter(export_progress_percent__isnull=False).select_related("video")
    if not only_unconditional:
        scenes = scenes.filter(updated_at__lt=now - STALE_SCENE_ENCODE_AGE)
    for scene in scenes:
        scene.export_progress_percent = None
        scene.encode_started_at = None
        scene.save(update_fields=["export_progress_percent", "encode_started_at"])

        Notification.objects.create(
            video=scene.video,
            kind=Notification.Kind.EXPORT_FAILED,
            message=f"Encoding a scene in {scene.video.path} was interrupted (worker restarted or hung) -- will retry automatically.",
        )
        # Safe to just re-queue immediately: still closed+dated+not
        # exported, so it's still eligible.
        trigger_auto_encode(scene.video)
        swept["scenes"] += 1

    return swept


@shared_task
def sweep_orphaned_work_task() -> dict:
    """Periodic (Celery beat, see config/celery.py) safety net -- catches a
    hung task on a worker that's still alive, as opposed to the
    unconditional startup sweep (also in config/celery.py, via the
    worker_ready signal) which handles the worker-got-restarted case."""
    return sweep_orphaned_work(only_unconditional=False)


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
    # export_progress_percent was already set to 0 when this was queued
    # (trigger_auto_encode / the manual `encode` action) -- encode_started_at
    # is what actually flips here, now that a worker has picked it up.
    scene.export_progress_percent = 0
    scene.encode_started_at = timezone.now()
    scene.save(update_fields=["export_progress_percent", "encode_started_at"])

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
        Scene.objects.filter(id=scene.id).update(export_progress_percent=None, encode_started_at=None)
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
