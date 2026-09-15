# Covers generate_video_metadata_task: the background backfill that lets
# sync/create return instantly instead of generating thumbnails inline.
import subprocess

import pytest

from videos.models import Video
from videos.tasks import generate_video_metadata_task

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
