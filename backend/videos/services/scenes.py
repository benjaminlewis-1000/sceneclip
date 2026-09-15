"""Derives Scene rows (the chapters that eventually get exported) from a
video's approved boundaries. Rejected/pending boundaries don't split
anything -- only approved cut points become scene edges.
"""
from django.db import transaction

from ..models import Scene, SceneBoundary


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
            if existing.start_seconds != start_t or existing.end_seconds != end_t:
                existing.start_seconds = start_t
                existing.end_seconds = end_t
                existing.save(update_fields=["start_seconds", "end_seconds"])
            keep_ids.append(existing.id)
        else:
            new_scene = Scene.objects.create(
                video=video,
                start_boundary=start_b,
                end_boundary=end_b,
                start_seconds=start_t,
                end_seconds=end_t,
            )
            keep_ids.append(new_scene.id)

    # Never drop a scene that's already been exported, even if its boundary
    # pair no longer matches the current approved set -- that export already
    # happened against the old cut points and re-deriving would silently
    # orphan the file on disk.
    video.scenes.exclude(id__in=keep_ids).filter(exported=False).delete()
