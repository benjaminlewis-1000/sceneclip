"""Keeps the Video table in sync with what's actually sitting under
VIDEO_ROOT, so the dashboard's default view is "every tape I have," not an
empty list waiting for manual entry.
"""
import os

from django.conf import settings

from ..models import Video

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts", ".m2ts", ".wmv", ".mpg", ".mpeg",
}


def sync_library() -> list[Video]:
    existing_paths = set(Video.objects.values_list("path", flat=True))
    created = []
    temp_clips_name = os.path.basename(settings.TEMP_SCENE_CLIPS_DIR)

    for root, dirs, files in os.walk(settings.VIDEO_ROOT):
        # Scene encodes land here before a human verifies them (see
        # config/settings.py:TEMP_SCENE_CLIPS_DIR) -- never scan it as
        # source material, or every auto-encoded clip would show up as its
        # own "video" needing scene detection.
        if root == settings.VIDEO_ROOT:
            dirs[:] = [d for d in dirs if d != temp_clips_name]
        for name in files:
            if os.path.splitext(name)[1].lower() not in VIDEO_EXTENSIONS:
                continue
            path = os.path.join(root, name)
            if path in existing_paths:
                continue
            created.append(Video(path=path))

    if created:
        Video.objects.bulk_create(created)
    return created
