# Covers generate_video_metadata_task: the background backfill that lets
# sync/create return instantly instead of generating thumbnails inline.
import subprocess

import pytest

from videos.models import DetectionRun, SceneBoundary, Video
from videos.tasks import generate_video_metadata_task, run_detection_task

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
