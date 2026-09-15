"""Derives Scene rows (the chapters that eventually get exported) from a
video's approved boundaries. Rejected/pending boundaries don't split
anything -- only approved cut points become scene edges.
"""
import os

from django.db import models, transaction

from ..models import Scene, SceneBoundary


class SceneStillEncoding(Exception):
    """Raised by undo_boundary_review when a scene bordering the boundary
    is actively mid-encode -- undoing right now would delete/rewrite a file
    a running ffmpeg process still has open."""


def _seed_from_boundaries(start_b, end_b):
    """A scene's content is described by what the reviewer logged as
    happening right after its start cut, or -- if that's blank -- right
    before its end cut (the same footage, from the other boundary's point
    of view). Used only to seed a scene that has no description/date of
    its own yet; never overwrites a real edit."""
    description = ""
    if start_b and start_b.after_description:
        description = start_b.after_description
    elif end_b and end_b.before_description:
        description = end_b.before_description

    scene_date = None
    if start_b and start_b.after_date:
        scene_date = start_b.after_date
    elif end_b and end_b.before_date:
        scene_date = end_b.before_date

    return description, scene_date


@transaction.atomic
def rebuild_scenes(video) -> None:
    approved = list(
        SceneBoundary.objects.filter(
            video=video, review_status=SceneBoundary.ReviewStatus.APPROVED
        ).order_by("timestamp_seconds")
    )

    cut_points = [(None, 0.0)] + [(b, b.timestamp_seconds) for b in approved]
    if video.duration_seconds:
        cut_points.append((None, video.duration_seconds))
    if len(cut_points) < 2:
        return

    existing_by_bounds = {
        (s.start_boundary_id, s.end_boundary_id): s for s in video.scenes.all()
    }

    keep_ids = []
    for (start_b, start_t), (end_b, end_t) in zip(cut_points, cut_points[1:]):
        key = (start_b.id if start_b else None, end_b.id if end_b else None)
        existing = existing_by_bounds.get(key)
        if existing:
            update_fields = []
            if existing.start_seconds != start_t or existing.end_seconds != end_t:
                existing.start_seconds = start_t
                existing.end_seconds = end_t
                update_fields += ["start_seconds", "end_seconds"]
            # Only backfills a scene that's never had its own description/
            # date set -- never overwrites a real edit made on the Scenes
            # table.
            if not existing.description and not existing.scene_date:
                description, scene_date = _seed_from_boundaries(start_b, end_b)
                if description or scene_date:
                    existing.description = description
                    existing.scene_date = scene_date
                    update_fields += ["description", "scene_date"]
            if update_fields:
                existing.save(update_fields=update_fields)
            keep_ids.append(existing.id)
        else:
            description, scene_date = _seed_from_boundaries(start_b, end_b)
            new_scene = Scene.objects.create(
                video=video,
                start_boundary=start_b,
                end_boundary=end_b,
                start_seconds=start_t,
                end_seconds=end_t,
                description=description,
                scene_date=scene_date,
            )
            keep_ids.append(new_scene.id)

    # Never drop a scene that's already been exported, even if its boundary
    # pair no longer matches the current approved set -- that export already
    # happened against the old cut points and re-deriving would silently
    # orphan the file on disk.
    video.scenes.exclude(id__in=keep_ids).filter(exported=False).delete()


def scenes_ready_to_encode(video):
    """Scenes that should auto-encode: closed (has an end_boundary -- the
    trailing open-ended scene is deliberately excluded, since re-encoding
    it every time a new boundary gets approved would mean repeatedly
    re-cutting a growing, potentially very large tail; it gets a manual
    "Encode this scene" trigger instead), not already exported or
    mid-encode, and dated (required before encoding at all -- see
    ReviewQueue's date validation, which is what's supposed to guarantee
    this is already true by the time a scene closes)."""
    return video.scenes.filter(
        end_boundary__isnull=False,
        exported=False,
        export_progress_percent__isnull=True,
        scene_date__isnull=False,
    )


@transaction.atomic
def undo_boundary_review(boundary: SceneBoundary) -> None:
    """Reverts an approved/rejected boundary back to pending. An approved
    boundary is the shared edge of two adjacent scenes (one's end_boundary,
    the next's start_boundary); undoing it means those two scenes are about
    to merge back into one, so any Scene actually touching this boundary
    that's already been encoded has its output file deleted from disk (the
    merged scene will need a fresh encode covering the wider span) and its
    exported state reset -- otherwise rebuild_scenes()'s own "never drop an
    exported scene" guard would leave it stranded, orphaned from the new
    cut points but still marked exported, with its file never cleaned up.

    Raises SceneStillEncoding, changing nothing, if any bordering scene is
    actively mid-encode -- its ffmpeg process may still have the very file
    this would delete open for writing.
    """
    affected = list(
        Scene.objects.filter(video=boundary.video).filter(
            models.Q(start_boundary=boundary) | models.Q(end_boundary=boundary)
        )
    )
    if any(s.export_progress_percent is not None for s in affected):
        raise SceneStillEncoding(
            "A scene bordering this boundary is still encoding -- wait for it to finish before undoing."
        )

    for scene in affected:
        if scene.exported and scene.exported_path:
            try:
                os.remove(scene.exported_path)
            except OSError:
                pass
        scene.exported = False
        scene.exported_path = ""
        scene.verified = False
        scene.export_progress_percent = None
        scene.encode_started_at = None
        scene.save(
            update_fields=[
                "exported", "exported_path", "verified",
                "export_progress_percent", "encode_started_at",
            ]
        )

    boundary.review_status = SceneBoundary.ReviewStatus.PENDING
    boundary.reviewed_at = None
    boundary.matched_from = None
    boundary.save(update_fields=["review_status", "reviewed_at", "matched_from"])

    rebuild_scenes(boundary.video)
