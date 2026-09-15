# Covers sweep_orphaned_work: recovers DetectionRuns/Scenes left claiming
# "running"/"mid-encode" by a worker process that's gone. Hit this for real
# in production -- restarting the worker container to deploy a code change
# orphaned an in-progress DetectionRun that then stayed "detecting" forever.
from unittest import mock

import pytest
from django.utils import timezone

from videos.models import DetectionRun, SceneBoundary, Scene, Video
from videos.tasks import sweep_orphaned_work

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

    with mock.patch("videos.tasks.export_scene_task.delay") as mock_delay:
        swept = sweep_orphaned_work(only_unconditional=True)

    assert swept["scenes"] == 1
    scene.refresh_from_db()
    assert scene.export_progress_percent == 0  # requeued by trigger_auto_encode
    mock_delay.assert_called_once_with(scene.id)
