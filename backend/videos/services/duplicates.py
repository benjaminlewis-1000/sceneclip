"""Marking one of two videos as a duplicate capture of the other: the
losing copy's encoded clips get deleted from disk (reclaiming space) and
it's flagged marked_done so it drops out of the library's active views,
but the Video row and its full boundary/scene history are kept, not
deleted -- unmark_duplicate() reverses it and re-triggers encoding for
whatever scenes lost their file.
"""
import os

from django.db import transaction

from ..models import Video
from .scenes import SceneStillEncoding


@transaction.atomic
def mark_duplicate(keep: Video, duplicate: Video) -> None:
    """`duplicate` is the losing copy -- `keep` is untouched. Raises
    SceneStillEncoding, changing nothing, if any of duplicate's scenes are
    actively mid-encode."""
    if keep.id == duplicate.id:
        raise ValueError("A video can't be marked a duplicate of itself.")

    scenes = list(duplicate.scenes.all())
    if any(s.export_progress_percent is not None for s in scenes):
        raise SceneStillEncoding(
            "A scene in this video is still encoding -- wait for it to finish before marking a duplicate."
        )

    for scene in scenes:
        if scene.exported and scene.exported_path:
            try:
                os.remove(scene.exported_path)
            except OSError:
                pass
        if scene.exported or scene.exported_path or scene.verified:
            scene.exported = False
            scene.exported_path = ""
            scene.verified = False
            scene.save(update_fields=["exported", "exported_path", "verified"])

    duplicate.duplicate_of = keep
    duplicate.marked_done = True
    duplicate.save(update_fields=["duplicate_of", "marked_done"])


def unmark_duplicate(video: Video) -> None:
    """Reverses mark_duplicate: clears the flag and re-triggers encoding
    for every scene that's eligible again now that it has no file (closed,
    dated, not already exported -- see services/scenes.py:
    scenes_ready_to_encode). The trailing open scene, as always, needs its
    manual "Encode this scene" trigger."""
    from .. import tasks  # deferred -- tasks.py imports this module's sibling scenes.py

    video.duplicate_of = None
    video.marked_done = False
    video.save(update_fields=["duplicate_of", "marked_done"])
    tasks.trigger_auto_encode(video)
