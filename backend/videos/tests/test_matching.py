# Covers carry_forward_reviews: the logic that lets a re-run of detection
# with different params reuse prior review verdicts instead of forcing a
# full re-review, as long as the new candidate is within tolerance of an
# old, already-reviewed one.
import pytest
from django.utils import timezone

from videos.models import DetectionRun, SceneBoundary, Video
from videos.services.matching import carry_forward_reviews

pytestmark = pytest.mark.django_db


def _run(video):
    return DetectionRun.objects.create(video=video, params={})


def test_carries_forward_approved_boundary_within_tolerance():
    video = Video.objects.create(path="/videos/tape1.mp4")
    old_run = _run(video)
    old_boundary = SceneBoundary.objects.create(
        video=video,
        run=old_run,
        timestamp_seconds=100.0,
        review_status=SceneBoundary.ReviewStatus.APPROVED,
        reviewed_at=timezone.now(),
    )

    new_run = _run(video)
    new_boundary = SceneBoundary.objects.create(
        video=video, run=new_run, timestamp_seconds=100.4  # within 1s tolerance
    )

    carry_forward_reviews(video, [new_boundary])
    new_boundary.refresh_from_db()

    assert new_boundary.review_status == SceneBoundary.ReviewStatus.APPROVED
    assert new_boundary.matched_from_id == old_boundary.id


def test_does_not_carry_forward_outside_tolerance():
    video = Video.objects.create(path="/videos/tape2.mp4")
    old_run = _run(video)
    SceneBoundary.objects.create(
        video=video,
        run=old_run,
        timestamp_seconds=100.0,
        review_status=SceneBoundary.ReviewStatus.REJECTED,
        reviewed_at=timezone.now(),
    )

    new_run = _run(video)
    new_boundary = SceneBoundary.objects.create(
        video=video, run=new_run, timestamp_seconds=105.0  # well outside tolerance
    )

    carry_forward_reviews(video, [new_boundary])
    new_boundary.refresh_from_db()

    assert new_boundary.review_status == SceneBoundary.ReviewStatus.PENDING
    assert new_boundary.matched_from_id is None


def test_picks_closest_match_when_multiple_candidates():
    video = Video.objects.create(path="/videos/tape3.mp4")
    old_run = _run(video)
    SceneBoundary.objects.create(
        video=video,
        run=old_run,
        timestamp_seconds=99.0,
        review_status=SceneBoundary.ReviewStatus.REJECTED,
        reviewed_at=timezone.now(),
    )
    near = SceneBoundary.objects.create(
        video=video,
        run=old_run,
        timestamp_seconds=100.2,
        review_status=SceneBoundary.ReviewStatus.APPROVED,
        reviewed_at=timezone.now(),
    )

    new_run = _run(video)
    new_boundary = SceneBoundary.objects.create(video=video, run=new_run, timestamp_seconds=100.0)

    carry_forward_reviews(video, [new_boundary])
    new_boundary.refresh_from_db()

    assert new_boundary.matched_from_id == near.id
    assert new_boundary.review_status == SceneBoundary.ReviewStatus.APPROVED


def test_pending_boundaries_are_not_used_as_match_sources():
    video = Video.objects.create(path="/videos/tape4.mp4")
    old_run = _run(video)
    SceneBoundary.objects.create(video=video, run=old_run, timestamp_seconds=100.0)  # still pending

    new_run = _run(video)
    new_boundary = SceneBoundary.objects.create(video=video, run=new_run, timestamp_seconds=100.1)

    carry_forward_reviews(video, [new_boundary])
    new_boundary.refresh_from_db()

    assert new_boundary.review_status == SceneBoundary.ReviewStatus.PENDING
