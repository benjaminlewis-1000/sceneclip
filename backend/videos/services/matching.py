"""Carries reviewer verdicts forward across re-runs of detection.

Tweaking detection params and re-running produces a fresh set of
SceneBoundary rows whose timestamps won't line up exactly with a prior run's.
Rather than making the reviewer re-litigate every boundary, each new boundary
is matched to the closest previously-reviewed boundary on the same video
within MATCH_TOLERANCE_SECONDS, and that verdict is copied forward. Only
genuinely new or shifted candidates are left pending for review.
"""
from .. import models

MATCH_TOLERANCE_SECONDS = 1.0


def carry_forward_reviews(video: "models.Video", new_boundaries: list["models.SceneBoundary"]) -> None:
    new_ids = {b.id for b in new_boundaries}
    prior_reviewed = list(
        models.SceneBoundary.objects.filter(video=video)
        .exclude(review_status=models.SceneBoundary.ReviewStatus.PENDING)
        .exclude(id__in=new_ids)
    )
    if not prior_reviewed:
        return

    for boundary in new_boundaries:
        best = None
        best_delta = MATCH_TOLERANCE_SECONDS
        for prior in prior_reviewed:
            delta = abs(prior.timestamp_seconds - boundary.timestamp_seconds)
            if delta <= best_delta:
                best = prior
                best_delta = delta
        if best is not None:
            boundary.review_status = best.review_status
            boundary.reviewed_at = best.reviewed_at
            boundary.matched_from = best
            boundary.save(update_fields=["review_status", "reviewed_at", "matched_from"])
