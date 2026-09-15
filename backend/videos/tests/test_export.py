# Covers export_one_scene: the shared per-scene encode primitive used both
# by export_scenes() (a bulk catch-all, no longer surfaced as its own UI
# button) and the automatic per-scene encode triggered when a scene closes.
import json
import os
import subprocess

import pytest
from django.test import override_settings

from videos.models import Scene, Video
from videos.services.export import export_one_scene, export_scenes

pytestmark = pytest.mark.django_db


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
    out_dir = tmp_path / "output"

    video = Video.objects.create(path=str(source), duration_seconds=4.0)
    scene = Scene.objects.create(
        video=video, start_seconds=0.0, end_seconds=2.0,
        description="Birthday cake", scene_date="1994-06-01",
    )
    scene.refresh_from_db()  # coerces scene_date from a raw string to a real date

    with override_settings(OUTPUT_ROOT=str(out_dir)):
        out_path = export_one_scene(scene)

    scene.refresh_from_db()
    assert scene.exported is True
    assert scene.exported_path == out_path
    assert scene.export_progress_percent is None
    # Dated filename, not just a bare ordinal -- easier to identify on disk
    # without opening the file.
    assert os.path.basename(out_path) == "1994-06-01_scene_001.mp4"

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-of", "json", "-show_entries",
            "format_tags=title,comment,date,description",
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
    # Traceability back to the source tape and where in it this came from --
    # folded into `description` since ffmpeg's mov/mp4 muxer silently drops
    # any metadata key it doesn't recognize as standard.
    assert "source.mp4" in tags["description"]
    assert "0.000s" in tags["description"]
    assert "2.000s" in tags["description"]


def test_export_scenes_skips_already_exported(tmp_path):
    source = tmp_path / "source.mp4"
    _make_synthetic_video(source)
    out_dir = tmp_path / "output"

    video = Video.objects.create(path=str(source), duration_seconds=4.0)
    Scene.objects.create(
        video=video, start_seconds=0.0, end_seconds=2.0, scene_date="1994-01-01",
        exported=True, exported_path="/already/done.mp4",
    )
    pending = Scene.objects.create(
        video=video, start_seconds=2.0, end_seconds=4.0, scene_date="1994-01-01",
    )

    with override_settings(OUTPUT_ROOT=str(out_dir)):
        exported_paths = export_scenes(video)

    assert len(exported_paths) == 1
    pending.refresh_from_db()
    assert pending.exported is True
