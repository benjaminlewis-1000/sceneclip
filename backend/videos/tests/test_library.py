# Covers sync_library (auto-discovers files under VIDEO_ROOT) and the
# browse endpoint's path-escape guard.
import os

import pytest
from django.test import override_settings

from videos.models import Video
from videos.services.browse import InvalidBrowsePath, list_directory
from videos.services.library import sync_library

pytestmark = pytest.mark.django_db


def test_sync_library_adds_new_video_files(tmp_path):
    (tmp_path / "tape1.mp4").write_bytes(b"")
    (tmp_path / "notes.txt").write_bytes(b"")  # not a video extension
    sub = tmp_path / "1990s"
    sub.mkdir()
    (sub / "tape2.mkv").write_bytes(b"")

    with override_settings(VIDEO_ROOT=str(tmp_path)):
        created = sync_library()

    paths = {v.path for v in created}
    assert paths == {str(tmp_path / "tape1.mp4"), str(sub / "tape2.mkv")}
    assert Video.objects.count() == 2


def test_sync_library_does_not_duplicate_existing_videos(tmp_path):
    (tmp_path / "tape1.mp4").write_bytes(b"")

    with override_settings(VIDEO_ROOT=str(tmp_path)):
        first = sync_library()
        assert len(first) == 1
        second = sync_library()
        assert second == []
    assert Video.objects.count() == 1


def test_browse_lists_dirs_and_video_files(tmp_path):
    (tmp_path / "tape1.mp4").write_bytes(b"")
    (tmp_path / "readme.txt").write_bytes(b"")
    (tmp_path / "1990s").mkdir()

    with override_settings(VIDEO_ROOT=str(tmp_path)):
        result = list_directory("")

    names = {(e["name"], e["type"]) for e in result["entries"]}
    assert ("tape1.mp4", "file") in names
    assert ("1990s", "dir") in names
    assert ("readme.txt", "file") not in names


def test_browse_rejects_path_escaping_video_root(tmp_path):
    with override_settings(VIDEO_ROOT=str(tmp_path)):
        with pytest.raises(InvalidBrowsePath):
            list_directory("../../etc")
