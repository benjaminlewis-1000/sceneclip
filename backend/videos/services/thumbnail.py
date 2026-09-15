"""One still frame per video for the library list, grabbed with ffmpeg and
cached to disk the same way preview clips are.
"""
import hashlib
import os
import subprocess

from django.conf import settings

# Not right at 0:00 -- capture-card VHS rips are prone to a black/garbage
# first frame or two while the deck's tracking settles.
THUMBNAIL_OFFSET_SECONDS = 3.0


def _thumbnail_path(video) -> str:
    digest = hashlib.sha1(f"{video.id}:{video.path}".encode()).hexdigest()[:20]
    return os.path.join(settings.PREVIEW_CLIPS_DIR, f"thumb_{digest}.jpg")


def ensure_thumbnail(video) -> str:
    out_path = _thumbnail_path(video)
    if os.path.exists(out_path):
        return out_path

    os.makedirs(settings.PREVIEW_CLIPS_DIR, exist_ok=True)
    offset = min(THUMBNAIL_OFFSET_SECONDS, (video.duration_seconds or 0) / 2)

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
