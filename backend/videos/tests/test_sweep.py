# Covers sweep_orphaned_work: recovers DetectionRuns/Scenes left claiming
# "running"/"mid-encode" by a worker process that's gone, and (via
# queue_pending_videos) auto-queues any video still sitting at PENDING. Hit
# the first case for real in production -- restarting the worker container
# to deploy a code change orphaned an in-progress DetectionRun that then
# stayed "detecting" forever. Hit the second for real too -- a batch of
# videos whose only run failed (a real error, not an orphaned worker) just
# sat at "pending" forever with nothing to auto-retry them.
#
# Every video created here defaults to status="pending" (the model
# default), so every sweep_orphaned_work() call below risks queue_pending_
# videos() picking it up and firing a *real*, unmocked run_detection_task
# under CELERY_TASK_ALWAYS_EAGER -- which would try to actually run
# PySceneDetect against a nonexistent file and blow up the test. Every call
# in this file mocks videos.tasks.run_detection_task.delay for exactly that
# reason, even in tests that aren't about detection runs at all.
from unittest import mock

import pytest
from django.utils import timezone

from videos.models import DetectionRun, SceneBoundary, Scene, Video
from videos.tasks import MAX_AUTO_DETECTION_RETRIES, queue_pending_videos, sweep_orphaned_work

pytestmark = pytest.mark.django_db


def test_unconditional_sweep_auto_retries_a_video_with_no_surviving_boundaries():
    # No boundaries ever came out of this run, so the video genuinely still
    # needs processing -- the sweep should re-queue it itself rather than
    # leaving it at "pending" for a human to notice and re-click Reprocess.
    video = Video.objects.create(path="/videos/tape.mp4")
    run = DetectionRun.objects.create(
        video=video, params={"detector": "adaptive"}, status=DetectionRun.Status.RUNNING
    )

    with mock.patch("videos.tasks.run_detection_task.delay") as mock_delay:
        swept = sweep_orphaned_work(only_unconditional=True)

    assert swept["runs"] == 1
    run.refresh_from_db()
    video.refresh_from_db()
    assert run.status == DetectionRun.Status.FAILED
    assert video.status == Video.Status.DETECTING  # re-queued, not left at pending
    # Status flips to DETECTING inside the orphan-recovery loop itself, before
    # queue_pending_videos() runs at the end of the same sweep -- so this is
    # the *only* re-queue, not a double-dispatch from both code paths.
    mock_delay.assert_called_once()
    new_run = DetectionRun.objects.exclude(id=run.id).get(video=video)
    assert new_run.params == {"detector": "adaptive"}  # same params carried over
    mock_delay.assert_called_once_with(new_run.id)


def test_unconditional_sweep_does_not_retry_video_with_existing_boundaries():
    video = Video.objects.create(path="/videos/tape.mp4", duration_seconds=60.0)
    old_run = DetectionRun.objects.create(video=video, params={})
    SceneBoundary.objects.create(video=video, run=old_run, timestamp_seconds=10.0)
    orphaned_run = DetectionRun.objects.create(video=video, params={}, status=DetectionRun.Status.RUNNING)

    with mock.patch("videos.tasks.run_detection_task.delay") as mock_delay:
        sweep_orphaned_work(only_unconditional=True)

    video.refresh_from_db()
    assert video.status == Video.Status.REVIEWING
    mock_delay.assert_not_called()
    orphaned_run.refresh_from_db()
    assert orphaned_run.status == DetectionRun.Status.FAILED


def test_periodic_sweep_only_touches_stale_runs():
    video = Video.objects.create(path="/videos/tape.mp4")
    fresh_run = DetectionRun.objects.create(video=video, params={}, status=DetectionRun.Status.RUNNING)

    with mock.patch("videos.tasks.run_detection_task.delay"):
        swept = sweep_orphaned_work(only_unconditional=False)

    assert swept["runs"] == 0
    fresh_run.refresh_from_db()
    assert fresh_run.status == DetectionRun.Status.RUNNING  # untouched -- not stale yet


def test_periodic_sweep_recovers_actually_stale_run():
    video = Video.objects.create(path="/videos/tape.mp4")
    run = DetectionRun.objects.create(video=video, params={}, status=DetectionRun.Status.RUNNING)
    DetectionRun.objects.filter(id=run.id).update(created_at=timezone.now() - timezone.timedelta(hours=4))

    with mock.patch("videos.tasks.run_detection_task.delay"):
        swept = sweep_orphaned_work(only_unconditional=False)

    assert swept["runs"] == 1
    run.refresh_from_db()
    assert run.status == DetectionRun.Status.FAILED


def test_sweep_resets_and_requeues_orphaned_scene_encode():
    # A *closed* scene -- has an end_boundary -- so it's eligible for
    # trigger_auto_encode's re-queue. An orphaned encode of the trailing
    # open scene (no end_boundary, only reachable via the manual "Encode
    # this scene" button) just resets to "not encoding"; re-triggering it
    # is manual by design, same as starting it was.
    video = Video.objects.create(path="/videos/tape.mp4", duration_seconds=60.0)
    run = DetectionRun.objects.create(video=video, params={})
    end_boundary = SceneBoundary.objects.create(video=video, run=run, timestamp_seconds=30.0)
    scene = Scene.objects.create(
        video=video, end_boundary=end_boundary, start_seconds=0.0, end_seconds=30.0,
        scene_date="1994-01-01", export_progress_percent=42,
    )

    with mock.patch("videos.tasks.export_scene_task.delay") as mock_delay, \
            mock.patch("videos.tasks.run_detection_task.delay"):
        mock_delay.return_value.id = "fake-task-id"
        swept = sweep_orphaned_work(only_unconditional=True)

    assert swept["scenes"] == 1
    scene.refresh_from_db()
    assert scene.export_progress_percent == 0  # requeued by trigger_auto_encode
    mock_delay.assert_called_once_with(scene.id)


def test_queue_pending_videos_queues_a_never_attempted_video():
    video = Video.objects.create(path="/videos/tape.mp4")  # status defaults to pending, 0 runs

    with mock.patch("videos.tasks.run_detection_task.delay") as mock_delay:
        queued = queue_pending_videos()

    assert queued == 1
    video.refresh_from_db()
    assert video.status == Video.Status.DETECTING
    new_run = DetectionRun.objects.get(video=video)
    mock_delay.assert_called_once_with(new_run.id)


def test_queue_pending_videos_retries_a_video_with_failed_runs_under_the_cap():
    video = Video.objects.create(path="/videos/tape.mp4")
    for _ in range(MAX_AUTO_DETECTION_RETRIES - 1):
        DetectionRun.objects.create(video=video, params={}, status=DetectionRun.Status.FAILED)

    with mock.patch("videos.tasks.run_detection_task.delay") as mock_delay:
        queued = queue_pending_videos()

    assert queued == 1
    mock_delay.assert_called_once()


def test_queue_pending_videos_stops_once_cap_is_reached():
    # A genuinely broken file (corrupt/unsupported source, say) would
    # otherwise get re-queued and re-fail forever, once per sweep cycle --
    # this is what makes it stop and sit pending for a human instead.
    video = Video.objects.create(path="/videos/tape.mp4")
    for _ in range(MAX_AUTO_DETECTION_RETRIES):
        DetectionRun.objects.create(video=video, params={}, status=DetectionRun.Status.FAILED)

    with mock.patch("videos.tasks.run_detection_task.delay") as mock_delay:
        queued = queue_pending_videos()

    assert queued == 0
    mock_delay.assert_not_called()
    video.refresh_from_db()
    assert video.status == Video.Status.PENDING  # left alone, not re-queued


def test_queue_pending_videos_ignores_non_pending_videos():
    video = Video.objects.create(path="/videos/tape.mp4", status=Video.Status.REVIEWING)

    with mock.patch("videos.tasks.run_detection_task.delay") as mock_delay:
        queued = queue_pending_videos()

    assert queued == 0
    mock_delay.assert_not_called()
