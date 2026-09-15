# Covers export_one_scene: the shared per-scene encode primitive used both
# by the bulk "Export approved scenes" button and the automatic per-scene
# encode triggered when a scene closes.
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

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags=title,date", "-of", "json", out_path],
        capture_output=True, text=True, check=True,
    )
    assert "Birthday cake" in probe.stdout
    assert "1994-06-01" in probe.stdout


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
