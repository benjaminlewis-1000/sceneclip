# Covers services/duplicates.py: marking one of two videos (a re-digitized
# re-capture of the same tape, say) as a duplicate deletes its encoded
# clips from disk but keeps the Video/Scene/SceneBoundary rows, and
# unmark_duplicate reverses it and re-triggers encoding.
from unittest import mock

import pytest

from videos.models import Scene, Video
from videos.services.duplicates import mark_duplicate, unmark_duplicate
from videos.services.scenes import SceneStillEncoding

pytestmark = pytest.mark.django_db


def test_mark_duplicate_deletes_clip_files_and_flags_video(tmp_path):
    keep = Video.objects.create(path="/videos/keep.mp4", duration_seconds=60.0)
    duplicate = Video.objects.create(path="/videos/dup.mp4", duration_seconds=60.0)

    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"fake mp4 data")
    scene = Scene.objects.create(
        video=duplicate, start_seconds=0.0, end_seconds=30.0,
        exported=True, exported_path=str(clip_path), verified=True,
    )

    mark_duplicate(keep, duplicate)

    duplicate.refresh_from_db()
    scene.refresh_from_db()
    assert duplicate.duplicate_of_id == keep.id
    assert duplicate.marked_done is True
    assert scene.exported is False
    assert scene.exported_path == ""
    assert scene.verified is False
    assert not clip_path.exists()


def test_mark_duplicate_rejects_self():
    video = Video.objects.create(path="/videos/tape.mp4")
    with pytest.raises(ValueError):
        mark_duplicate(video, video)


def test_mark_duplicate_blocks_while_a_scene_is_encoding():
    keep = Video.objects.create(path="/videos/keep.mp4", duration_seconds=60.0)
    duplicate = Video.objects.create(path="/videos/dup.mp4", duration_seconds=60.0)
    scene = Scene.objects.create(
        video=duplicate, start_seconds=0.0, end_seconds=30.0, export_progress_percent=40,
    )

    with pytest.raises(SceneStillEncoding):
        mark_duplicate(keep, duplicate)

    duplicate.refresh_from_db()
    assert duplicate.duplicate_of_id is None
    scene.refresh_from_db()
    assert scene.export_progress_percent == 40  # untouched


def test_unmark_duplicate_reverses_flag_and_requeues_encoding():
    keep = Video.objects.create(path="/videos/keep.mp4", duration_seconds=60.0)
    duplicate = Video.objects.create(
        path="/videos/dup.mp4", duration_seconds=60.0, duplicate_of=keep, marked_done=True,
    )
    # Closed + dated + not exported -- exactly what a scene looks like
    # right after mark_duplicate deleted its clip.
    from videos.models import DetectionRun, SceneBoundary
    run = DetectionRun.objects.create(video=duplicate, params={})
    end_boundary = SceneBoundary.objects.create(video=duplicate, run=run, timestamp_seconds=30.0)
    Scene.objects.create(
        video=duplicate, end_boundary=end_boundary, start_seconds=0.0, end_seconds=30.0,
        scene_date="1994-01-01",
    )

    with mock.patch("videos.tasks.export_scene_task.delay") as mock_delay:
        mock_delay.return_value.id = "fake-task-id"
        unmark_duplicate(duplicate)

    duplicate.refresh_from_db()
    assert duplicate.duplicate_of_id is None
    assert duplicate.marked_done is False
    mock_delay.assert_called_once()
