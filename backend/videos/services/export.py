"""Chapter export, in two stages:

1. export_one_scene() cuts a Scene out of its source video into
   settings.TEMP_SCENE_CLIPS_DIR (not the final output folder -- nobody's
   watched it yet). Used both by export_scenes() (the bulk "Export approved
   scenes" catch-all) and by the automatic per-scene encode triggered the
   moment a scene closes (see services/scenes.py), which is the primary
   path now.
2. finalize_scene() moves an already-encoded, human-verified clip from
   there into settings.OUTPUT_ROOT -- called by the `verify` action, so
   OUTPUT_ROOT only ever holds clips someone's actually confirmed are
   right.
"""
import datetime
import os
import shutil
import subprocess
import time
from typing import Callable, Optional
from zoneinfo import ZoneInfo

# How often (wall-clock seconds) to write a progress update back to the DB
# while an ffmpeg cut is running -- ffmpeg's -progress output emits far more
# often than that, and there's no value updating every line.
PROGRESS_WRITE_INTERVAL_SECONDS = 1.0

_EASTERN = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")


def creation_time_for_date(scene_date: datetime.date) -> str:
    """A scene_date is a date, not a moment -- pins it to noon Eastern
    (not midnight) before converting to the UTC ffmpeg/mov's `creation_time`
    metadata actually wants, so the date it round-trips to in any other
    timezone a downstream tool happens to render it in stays comfortably
    away from a day boundary. zoneinfo resolves the correct EST/EDT offset
    for the specific date (including pre-2007 historical DST rules, which
    matter here -- these are VHS tapes from the 90s)."""
    noon_eastern = datetime.datetime.combine(scene_date, datetime.time(12, 0), tzinfo=_EASTERN)
    return noon_eastern.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _temp_dir_for(video) -> str:
    from django.conf import settings

    out_dir = os.path.join(settings.TEMP_SCENE_CLIPS_DIR, _safe_name(video.path))
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _scene_output_filename(scene) -> str:
    # Ordinal position among this video's scenes in chronological order --
    # stable in practice since approving/rejecting an existing boundary
    # doesn't move any scene's start_seconds around.
    ordered_ids = list(scene.video.scenes.order_by("start_seconds").values_list("id", flat=True))
    idx = ordered_ids.index(scene.id) + 1
    date_prefix = scene.scene_date.isoformat() if scene.scene_date else "undated"
    return f"{date_prefix}_scene_{idx:03d}.mp4"


def export_one_scene(scene, on_progress: Optional[Callable[[float], None]] = None) -> str:
    """Cuts one Scene out of its source video, writes description/date in
    as container metadata, and marks it exported. `on_progress` receives a
    0.0-1.0 fraction through this scene's own encode."""
    video = scene.video
    out_dir = _temp_dir_for(video)
    out_path = os.path.join(out_dir, _scene_output_filename(scene))
    duration = scene.end_seconds - scene.start_seconds

    metadata_args = []
    if scene.description:
        # Both tags: `title` is what most players/OS file browsers show as
        # the displayed name (works best short), while `comment` is where
        # tools that support a longer free-text description (Finder,
        # Windows Explorer, Plex/Jellyfin) actually look for one. Writing
        # both means the same description shows up either way, regardless
        # of which convention the tool viewing it later expects.
        metadata_args += ["-metadata", f"title={scene.description}"]
        metadata_args += ["-metadata", f"comment={scene.description}"]
    if scene.scene_date:
        metadata_args += ["-metadata", f"date={scene.scene_date.isoformat()}"]
        # The real, structured timestamp -- ffmpeg's mov/mp4 muxer writes
        # this straight into the mvhd/tkhd/mdhd atoms' creation/modification
        # time fields (not a free-text tag), which is what tools like
        # exiftool report as CreateDate and what any file manager's "date
        # taken"/"media created" column actually reads, rather than a guess
        # based on file mtime.
        metadata_args += ["-metadata", f"creation_time={creation_time_for_date(scene.scene_date)}"]
    # Traceability back to the source tape and where in it this came from.
    # ffmpeg's mov/mp4 muxer silently drops any metadata key it doesn't
    # recognize as standard (verified empirically -- a custom "source_file"
    # key never made it into the output at all), so this can't be its own
    # field the way it could in a format like Matroska. `description` is
    # one of the handful MP4 actually preserves and isn't used for anything
    # else here (title/comment already carry the scene's own description).
    metadata_args += [
        "-metadata",
        f"description=Source: {os.path.basename(video.path)} "
        f"[{scene.start_seconds:.3f}s - {scene.end_seconds:.3f}s]",
    ]

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
    scene.encode_started_at = None
    scene.save(update_fields=["exported", "exported_path", "export_progress_percent", "encode_started_at"])
    return out_path


def finalize_scene(scene) -> str:
    """Moves an encoded scene's clip from TEMP_SCENE_CLIPS_DIR into
    OUTPUT_ROOT and marks it verified -- called by the `verify` action once
    a human has actually watched it and confirmed it's right. shutil.move
    rather than os.rename since the temp dir (under VIDEO_ROOT) and
    OUTPUT_ROOT are typically separate mounts/filesystems, and os.rename
    can't cross that boundary."""
    out_dir = _output_dir_for(scene.video)
    final_path = os.path.join(out_dir, os.path.basename(scene.exported_path))
    shutil.move(scene.exported_path, final_path)

    scene.exported_path = final_path
    scene.verified = True
    scene.save(update_fields=["exported_path", "verified"])
    return final_path


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
