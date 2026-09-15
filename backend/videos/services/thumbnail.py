"""One still frame per video for the library list, grabbed with ffmpeg and
cached to disk the same way preview clips are.
"""
import hashlib
import os
import subprocess

from django.conf import settings

# A few seconds in wasn't far enough -- VHS capture-card rips routinely
# have a black or garbage stretch well past 0:00 while the deck's tracking
# settles, sometimes several seconds long. One minute in clears that
# reliably, but shouldn't eat a big chunk of a short clip, so fall back to
# 10% in for anything where a minute would be a large fraction of the
# runtime (e.g. 60s into a 5-minute video is 20% in, further than needed).
THUMBNAIL_OFFSET_SECONDS = 60.0
THUMBNAIL_OFFSET_FRACTION = 0.1


def _thumbnail_path(video) -> str:
    digest = hashlib.sha1(f"{video.id}:{video.path}".encode()).hexdigest()[:20]
    return os.path.join(settings.PREVIEW_CLIPS_DIR, f"thumb_{digest}.jpg")


def ensure_thumbnail(video) -> str:
    out_path = _thumbnail_path(video)
    if os.path.exists(out_path):
        return out_path

    os.makedirs(settings.PREVIEW_CLIPS_DIR, exist_ok=True)
    duration = video.duration_seconds or 0
    offset = min(THUMBNAIL_OFFSET_SECONDS, duration * THUMBNAIL_OFFSET_FRACTION)

    tmp_path = out_path + ".tmp.jpg"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(offset),
            "-i", video.path,
            "-frames:v", "1",
            "-q:v", "4",
            tmp_path,
        ],
        check=True,
        capture_output=True,
    )
    os.rename(tmp_path, out_path)
    return out_path
