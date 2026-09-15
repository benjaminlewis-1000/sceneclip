"""Final chapter export: cuts each approved+enriched Scene out of its source
video into settings.OUTPUT_ROOT, writing the enrichment fields in as
container metadata.

export_one_scene() is the shared primitive -- used both by export_scenes()
(the bulk "Export approved scenes" button, a catch-all for anything that
slipped through) and by the automatic per-scene encode triggered the moment
a scene closes (see services/scenes.py), which is the primary path now.
"""
import os
import subprocess
import time
from typing import Callable, Optional

# How often (wall-clock seconds) to write a progress update back to the DB
# while an ffmpeg cut is running -- ffmpeg's -progress output emits far more
# often than that, and there's no value updating every line.
PROGRESS_WRITE_INTERVAL_SECONDS = 1.0


def _safe_name(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in base)


def _run_ffmpeg_with_progress(cmd: list[str], scene_duration: float, on_progress) -> None:
    """Runs an ffmpeg command with `-progress pipe:1` already appended to
    `cmd`, calling on_progress(fraction_0_to_1) as it reports how far into
    the clip it's gotten."""
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    last_write = 0.0
    try:
        for line in process.stdout:
            if not line.startswith("out_time_ms="):
                continue
            try:
                # Despite the name, ffmpeg's -progress reports this field in
                # microseconds, not milliseconds -- a long-standing quirk.
                out_time_seconds = int(line.strip().split("=", 1)[1]) / 1_000_000
            except (ValueError, IndexError):
                continue
            now = time.monotonic()
            if on_progress and now - last_write >= PROGRESS_WRITE_INTERVAL_SECONDS:
                fraction = min(1.0, out_time_seconds / scene_duration) if scene_duration else 1.0
                on_progress(fraction)
                last_write = now
    finally:
        process.wait()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, cmd)


def _output_dir_for(video) -> str:
    from django.conf import settings

    out_dir = os.path.join(settings.OUTPUT_ROOT, _safe_name(video.path))
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _scene_output_filename(scene) -> str:
    # Ordinal position among this video's scenes in chronological order --
    # stable in practice since approving/rejecting an existing boundary
    # doesn't move any scene's start_seconds around.
    ordered_ids = list(scene.video.scenes.order_by("start_seconds").values_list("id", flat=True))
    idx = ordered_ids.index(scene.id) + 1
    return f"scene_{idx:03d}.mp4"


def export_one_scene(scene, on_progress: Optional[Callable[[float], None]] = None) -> str:
    """Cuts one Scene out of its source video, writes description/date in
    as container metadata, and marks it exported. `on_progress` receives a
    0.0-1.0 fraction through this scene's own encode."""
    video = scene.video
    out_dir = _output_dir_for(video)
    out_path = os.path.join(out_dir, _scene_output_filename(scene))
    duration = scene.end_seconds - scene.start_seconds

    metadata_args = []
    if scene.description:
        metadata_args += ["-metadata", f"title={scene.description}"]
    if scene.scene_date:
        metadata_args += ["-metadata", f"date={scene.scene_date.isoformat()}"]

    tmp_path = out_path + ".tmp.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(scene.start_seconds),
        "-i", video.path,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac",
        *metadata_args,
        "-progress", "pipe:1", "-nostats",
        tmp_path,
    ]

    _run_ffmpeg_with_progress(cmd, duration, on_progress)
    os.rename(tmp_path, out_path)

    scene.exported = True
    scene.exported_path = out_path
    scene.export_progress_percent = None
    scene.save(update_fields=["exported", "exported_path", "export_progress_percent"])
    return out_path


def export_scenes(video, progress_callback: Optional[Callable[[int], None]] = None) -> list[str]:
    """Bulk catch-all: encodes every not-yet-exported scene for a video.
    Most scenes never reach this -- they auto-encode individually as soon
    as they close (see services/scenes.py) -- this is for whatever's left
    (e.g. the trailing open scene, once finalized, if it wasn't manually
    encoded on its own)."""
    exported_paths = []
    scenes = list(video.scenes.filter(exported=False).order_by("start_seconds"))
    total_duration = sum(s.end_seconds - s.start_seconds for s in scenes) or 1.0
    completed_duration = 0.0

    for scene in scenes:
        duration = scene.end_seconds - scene.start_seconds
        scene_completed_so_far = completed_duration

        def on_progress(fraction, _completed=scene_completed_so_far, _duration=duration):
            if progress_callback:
                overall = (_completed + fraction * _duration) / total_duration
                progress_callback(min(99, int(overall * 100)))

        out_path = export_one_scene(scene, on_progress=on_progress if progress_callback else None)
        exported_paths.append(out_path)
        completed_duration += duration

    if progress_callback:
        progress_callback(100)

    return exported_paths
