"""Generates short preview clips (+/- PREVIEW_PAD_SECONDS) around a proposed
scene boundary so the review UI can show what's actually at the cut.

Re-encodes (rather than stream-copying) the preview window: `-c copy` seeks
snap to the nearest keyframe, which on GOP-heavy VHS-capture encodes can be
several seconds off from the requested boundary -- unacceptable for a review
tool whose whole point is showing the exact transition. The clips are short
(10s) so the re-encode cost is small.
"""
import hashlib
import os
import subprocess

from django.conf import settings

PREVIEW_PAD_SECONDS = 5


def _preview_clip_path(video, boundary) -> str:
    key = f"{video.id}:{boundary.id}:{boundary.timestamp_seconds:.3f}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:20]
    return os.path.join(settings.PREVIEW_CLIPS_DIR, f"{digest}.mp4")


def ensure_preview_clip(video, boundary) -> str:
    """Returns the filesystem path to the boundary's preview clip, generating
    it on first request and reusing it thereafter."""
    out_path = _preview_clip_path(video, boundary)
    if os.path.exists(out_path):
        return out_path

    os.makedirs(settings.PREVIEW_CLIPS_DIR, exist_ok=True)
    start = max(0.0, boundary.timestamp_seconds - PREVIEW_PAD_SECONDS)
    duration = PREVIEW_PAD_SECONDS * 2

    tmp_path = out_path + ".tmp.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", video.path,
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac",
            tmp_path,
        ],
        check=True,
        capture_output=True,
    )
    os.rename(tmp_path, out_path)
    return out_path
