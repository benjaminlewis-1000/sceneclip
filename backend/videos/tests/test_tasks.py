# Covers generate_video_metadata_task: the background backfill that lets
# sync/create return instantly instead of generating thumbnails inline.
import subprocess
from unittest import mock

import pytest

from videos.models import DetectionRun, Scene, SceneBoundary, Video
from videos.tasks import (
    export_scene_task,
    generate_video_metadata_task,
    run_detection_task,
    trigger_auto_encode,
)

pytestmark = pytest.mark.django_db


def test_generate_video_metadata_task_populates_duration(tmp_path):
    video_path = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=64x64:rate=10",
            str(video_path),
        ],
        check=True,
        capture_output=True,
    )
    video = Video.objects.create(path=str(video_path))

    generate_video_metadata_task(video.id)

    video.refresh_from_db()
    assert video.duration_seconds is not None
    assert video.duration_seconds == pytest.approx(2.0, abs=0.5)


def test_generate_video_metadata_task_is_a_noop_on_missing_file():
    video = Video.objects.create(path="/videos/does-not-exist.mp4")

    generate_video_metadata_task(video.id)  # should not raise

    video.refresh_from_db()
    assert video.duration_seconds is None


def test_run_detection_task_failure_reverts_pending_video_to_pending():
    # A video with no boundaries yet that fails detection (bad path,
    # unreadable file, etc.) must not get stuck showing "detecting"
    # forever -- confirmed this really happened in production when a stale
    # worker process's model class didn't match the DB schema.
    video = Video.objects.create(path="/videos/does-not-exist.mp4")
    run = DetectionRun.objects.create(video=video, params={"detector": "adaptive"})

    with pytest.raises(Exception):
        run_detection_task(run.id)

    video.refresh_from_db()
    run.refresh_from_db()
    assert video.status == Video.Status.PENDING
    assert run.status == DetectionRun.Status.FAILED


def test_run_detection_task_failure_reverts_to_reviewing_when_boundaries_exist():
    video = Video.objects.create(path="/videos/does-not-exist.mp4", duration_seconds=60.0)
    old_run = DetectionRun.objects.create(video=video, params={})
    SceneBoundary.objects.create(video=video, run=old_run, timestamp_seconds=10.0)

    new_run = DetectionRun.objects.create(video=video, params={"detector": "adaptive"})
    with pytest.raises(Exception):
        run_detection_task(new_run.id)

    video.refresh_from_db()
    assert video.status == Video.Status.REVIEWING


def test_run_detection_task_skips_a_redelivered_duplicate_of_a_running_run():
    # Hit for real: a broker/connection hiccup caused Celery to redeliver
    # an already-executing task, starting a second concurrent run against
    # the same DetectionRun -- if the duplicate hadn't bailed out, it
    # would have clobbered status back to RUNNING (or worse, raced to
    # bulk_create duplicate SceneBoundary rows).
    video = Video.objects.create(path="/videos/tape.mp4")
    run = DetectionRun.objects.create(video=video, params={}, status=DetectionRun.Status.RUNNING)

    run_detection_task(run.id)  # should return immediately, not raise

    run.refresh_from_db()
    assert run.status == DetectionRun.Status.RUNNING  # untouched by the duplicate


def test_run_detection_task_skips_a_redelivered_duplicate_of_a_done_run():
    video = Video.objects.create(path="/videos/tape.mp4", duration_seconds=60.0)
    run = DetectionRun.objects.create(
        video=video, params={}, status=DetectionRun.Status.DONE, progress_percent=100,
    )
    video.status = Video.Status.REVIEWING
    video.save(update_fields=["status"])

    run_detection_task(run.id)

    run.refresh_from_db()
    video.refresh_from_db()
    assert run.status == DetectionRun.Status.DONE
    assert video.status == Video.Status.REVIEWING  # not clobbered back to DETECTING


def test_export_scene_task_skips_a_redelivered_duplicate_of_an_exported_scene():
    video = Video.objects.create(path="/videos/tape.mp4", duration_seconds=60.0)
    scene = Scene.objects.create(
        video=video, start_seconds=0.0, end_seconds=30.0, scene_date="1994-01-01",
        exported=True, exported_path="/output/already-done.mp4",
    )

    with mock.patch("videos.services.export.export_one_scene") as mock_export:
        export_scene_task(scene.id)

    mock_export.assert_not_called()
    scene.refresh_from_db()
    assert scene.exported_path == "/output/already-done.mp4"  # untouched


def test_trigger_auto_encode_queues_closed_dated_scenes_only():
    video = Video.objects.create(path="/videos/tape.mp4", duration_seconds=90.0)
    run = DetectionRun.objects.create(video=video, params={})
    b1 = SceneBoundary.objects.create(video=video, run=run, timestamp_seconds=30.0)
    b2 = SceneBoundary.objects.create(video=video, run=run, timestamp_seconds=60.0)

    closed_dated = Scene.objects.create(
        video=video, start_boundary=b1, end_boundary=b2, start_seconds=30.0, end_seconds=60.0,
        scene_date="1994-01-01",
    )
    trailing_open = Scene.objects.create(
        video=video, start_boundary=b2, end_boundary=None, start_seconds=60.0, end_seconds=90.0,
        scene_date="1994-01-01",
    )  # no end_boundary -- must not auto-encode even though it has a date
    already_exported = Scene.objects.create(
        video=video, start_boundary=None, end_boundary=b1, start_seconds=0.0, end_seconds=30.0,
        scene_date="1994-01-01", exported=True, exported_path="/output/x.mp4",
    )

    with mock.patch("videos.tasks.export_scene_task.delay") as mock_delay:
        trigger_auto_encode(video)

    mock_delay.assert_called_once_with(closed_dated.id)
    closed_dated.refresh_from_db()
    assert closed_dated.export_progress_percent == 0
    trailing_open.refresh_from_db()
    assert trailing_open.export_progress_percent is None
    already_exported.refresh_from_db()
    assert already_exported.export_progress_percent is None
