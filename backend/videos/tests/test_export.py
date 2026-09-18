# Covers export_one_scene: the shared per-scene encode primitive used both
# by export_scenes() (a bulk catch-all, no longer surfaced as its own UI
# button) and the automatic per-scene encode triggered when a scene closes.
import datetime
import json
import os
import subprocess

import pytest
from django.test import override_settings

from videos.models import Scene, Video
from videos.services.export import creation_time_for_date, export_one_scene, export_scenes, finalize_scene

pytestmark = pytest.mark.django_db


def test_creation_time_for_date_uses_noon_eastern_accounting_for_dst():
    # April 24 1993 falls after that year's (pre-2007-rules) DST start --
    # EDT, UTC-4 -- so noon Eastern is 16:00 UTC.
    assert creation_time_for_date(datetime.date(1993, 4, 24)) == "1993-04-24T16:00:00Z"
    # January 1 1994 is standard time -- EST, UTC-5 -- so noon Eastern is
    # 17:00 UTC, still comfortably clear of the date boundary either way.
    assert creation_time_for_date(datetime.date(1994, 1, 1)) == "1994-01-01T17:00:00Z"


def _make_synthetic_video(path, duration=4):
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=64x64:rate=10",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_export_one_scene_encodes_and_marks_exported(tmp_path):
    source = tmp_path / "source.mp4"
    _make_synthetic_video(source)
    temp_dir = tmp_path / "temp_scene_clips"

    video = Video.objects.create(path=str(source), duration_seconds=4.0)
    scene = Scene.objects.create(
        video=video, start_seconds=0.0, end_seconds=2.0,
        description="Birthday cake", scene_date="1994-06-01",
    )
    scene.refresh_from_db()  # coerces scene_date from a raw string to a real date

    with override_settings(TEMP_SCENE_CLIPS_DIR=str(temp_dir)):
        out_path = export_one_scene(scene)

    scene.refresh_from_db()
    assert scene.exported is True
    assert scene.exported_path == out_path
    assert scene.export_progress_percent is None
    # Lands in the unverified staging dir, not OUTPUT_ROOT -- only
    # finalize_scene() (the `verify` action) moves it there.
    assert str(temp_dir) in out_path
    # Dated filename, not just a bare ordinal -- easier to identify on disk
    # without opening the file.
    assert os.path.basename(out_path) == "1994-06-01_scene_001.mp4"

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-of", "json", "-show_entries",
            "format_tags=title,comment,date,description,creation_time",
            out_path,
        ],
        capture_output=True, text=True, check=True,
    )
    tags = json.loads(probe.stdout)["format"]["tags"]
    # title and comment both carry the description -- different tools read
    # different tags for a scene's descriptive text (see export.py).
    assert tags["title"] == "Birthday cake"
    assert tags["comment"] == "Birthday cake"
    assert tags["date"] == "1994-06-01"
    # Real structured timestamp, not just a free-text date tag -- what
    # exiftool/etc. read as CreateDate. Pinned to noon Eastern (June 1st is
    # EDT, UTC-4) specifically so it can't round-trip across a date
    # boundary in another timezone; ffmpeg normalizes to microsecond
    # precision on the way back out.
    assert tags["creation_time"] == "1994-06-01T16:00:00.000000Z"
    # Traceability back to the source tape and where in it this came from --
    # folded into `description` since ffmpeg's mov/mp4 muxer silently drops
    # any metadata key it doesn't recognize as standard.
    assert "source.mp4" in tags["description"]
    assert "0.000s" in tags["description"]
    assert "2.000s" in tags["description"]


def test_export_scenes_skips_already_exported(tmp_path):
    source = tmp_path / "source.mp4"
    _make_synthetic_video(source)
    temp_dir = tmp_path / "temp_scene_clips"

    video = Video.objects.create(path=str(source), duration_seconds=4.0)
    Scene.objects.create(
        video=video, start_seconds=0.0, end_seconds=2.0, scene_date="1994-01-01",
        exported=True, exported_path="/already/done.mp4",
    )
    pending = Scene.objects.create(
        video=video, start_seconds=2.0, end_seconds=4.0, scene_date="1994-01-01",
    )

    with override_settings(TEMP_SCENE_CLIPS_DIR=str(temp_dir)):
        exported_paths = export_scenes(video)

    assert len(exported_paths) == 1
    pending.refresh_from_db()
    assert pending.exported is True


def test_finalize_scene_moves_clip_to_output_root_and_verifies(tmp_path):
    source = tmp_path / "source.mp4"
    _make_synthetic_video(source)
    temp_dir = tmp_path / "temp_scene_clips"
    out_dir = tmp_path / "output"

    video = Video.objects.create(path=str(source), duration_seconds=4.0)
    scene = Scene.objects.create(
        video=video, start_seconds=0.0, end_seconds=2.0, scene_date="1994-06-01",
    )
    scene.refresh_from_db()

    with override_settings(TEMP_SCENE_CLIPS_DIR=str(temp_dir)):
        export_one_scene(scene)
    scene.refresh_from_db()
    temp_path = scene.exported_path
    assert os.path.exists(temp_path)

    # A correction made after the initial encode but before verify -- the
    # whole point of finalize_scene re-stamping metadata, not just moving
    # the file: the encoded clip's baked-in metadata still says the old
    # values at this point.
    scene.description = "Corrected description"
    scene.scene_date = datetime.date(1994, 7, 4)
    scene.save(update_fields=["description", "scene_date"])

    with override_settings(OUTPUT_ROOT=str(out_dir)):
        final_path = finalize_scene(scene)

    scene.refresh_from_db()
    assert scene.verified is True
    assert scene.exported_path == final_path
    assert not os.path.exists(temp_path)  # moved, not copied
    assert os.path.exists(final_path)
    assert str(out_dir) in final_path
    assert os.path.basename(final_path) == os.path.basename(temp_path)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json", "-show_entries", "format_tags=title,date", final_path],
        capture_output=True, text=True, check=True,
    )
    tags = json.loads(probe.stdout)["format"]["tags"]
    assert tags["title"] == "Corrected description"
    assert tags["date"] == "1994-07-04"
