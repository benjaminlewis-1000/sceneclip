# Covers rebuild_scenes: turning approved boundaries into Scene rows, and
# the two invariants that matter for the review workflow -- enrichment
# fields on an unchanged segment survive a rebuild, and an already-exported
# scene is never silently deleted even if its boundaries later change.
import pytest

from videos.models import DetectionRun, SceneBoundary, Video
from videos.services.scenes import rebuild_scenes

pytestmark = pytest.mark.django_db


def _run(video):
    return DetectionRun.objects.create(video=video, params={})


def test_rebuild_scenes_creates_segments_between_approved_boundaries():
    video = Video.objects.create(path="/videos/tape.mp4", duration_seconds=120.0)
    run = _run(video)
    b1 = SceneBoundary.objects.create(
        video=video, run=run, timestamp_seconds=30.0, review_status=SceneBoundary.ReviewStatus.APPROVED
    )
    SceneBoundary.objects.create(
        video=video, run=run, timestamp_seconds=60.0, review_status=SceneBoundary.ReviewStatus.REJECTED
    )
    b3 = SceneBoundary.objects.create(
        video=video, run=run, timestamp_seconds=90.0, review_status=SceneBoundary.ReviewStatus.APPROVED
    )

    rebuild_scenes(video)

    scenes = list(video.scenes.order_by("start_seconds"))
    assert [(s.start_seconds, s.end_seconds) for s in scenes] == [
        (0.0, 30.0),
        (30.0, 90.0),
        (90.0, 120.0),
    ]
    assert scenes[0].end_boundary_id == b1.id
    assert scenes[1].start_boundary_id == b1.id
    assert scenes[1].end_boundary_id == b3.id


def test_rebuild_scenes_preserves_enrichment_on_unchanged_segment():
    video = Video.objects.create(path="/videos/tape.mp4", duration_seconds=60.0)
    run = _run(video)
    SceneBoundary.objects.create(
        video=video, run=run, timestamp_seconds=30.0, review_status=SceneBoundary.ReviewStatus.APPROVED
    )

    rebuild_scenes(video)
    scene = video.scenes.get(start_seconds=0.0)
    scene.description = "Birthday party"
    scene.save(update_fields=["description"])

    rebuild_scenes(video)  # no boundary changes -- should be a no-op on this row

    scene.refresh_from_db()
    assert scene.description == "Birthday party"


def test_rebuild_scenes_does_not_delete_exported_scene():
    video = Video.objects.create(path="/videos/tape.mp4", duration_seconds=60.0)
    run = _run(video)
    boundary = SceneBoundary.objects.create(
        video=video, run=run, timestamp_seconds=30.0, review_status=SceneBoundary.ReviewStatus.APPROVED
    )
    rebuild_scenes(video)
    scene = video.scenes.get(start_seconds=0.0)
    scene.exported = True
    scene.save(update_fields=["exported"])

    boundary.review_status = SceneBoundary.ReviewStatus.REJECTED
    boundary.save(update_fields=["review_status"])
    rebuild_scenes(video)

    assert video.scenes.filter(id=scene.id).exists()


def test_rebuild_scenes_no_op_without_duration_or_approved_boundaries():
    video = Video.objects.create(path="/videos/tape.mp4")  # no duration probed yet
    rebuild_scenes(video)
    assert video.scenes.count() == 0


def test_rebuild_scenes_seeds_new_scene_from_boundary_after_description():
    video = Video.objects.create(path="/videos/tape.mp4", duration_seconds=60.0)
    run = _run(video)
    SceneBoundary.objects.create(
        video=video,
        run=run,
        timestamp_seconds=30.0,
        review_status=SceneBoundary.ReviewStatus.APPROVED,
        after_description="Birthday cake",
        after_date="1994-06-01",
    )

    rebuild_scenes(video)

    second_scene = video.scenes.get(start_seconds=30.0)
    assert second_scene.description == "Birthday cake"
    assert str(second_scene.scene_date) == "1994-06-01"


def test_rebuild_scenes_falls_back_to_end_boundary_before_description():
    video = Video.objects.create(path="/videos/tape.mp4", duration_seconds=60.0)
    run = _run(video)
    b1 = SceneBoundary.objects.create(
        video=video, run=run, timestamp_seconds=30.0, review_status=SceneBoundary.ReviewStatus.APPROVED
    )
    SceneBoundary.objects.create(
        video=video,
        run=run,
        timestamp_seconds=45.0,
        review_status=SceneBoundary.ReviewStatus.APPROVED,
        before_description="Opening presents",
    )

    rebuild_scenes(video)

    middle_scene = video.scenes.get(start_seconds=30.0, end_seconds=45.0)
    assert middle_scene.description == "Opening presents"


def test_rebuild_scenes_backfills_blank_existing_scene_from_boundary():
    # Covers the retroactive case: a scene created before before/after_*
    # fields existed (or before they were ever filled in) should pick up
    # the description once the boundary gets one, as long as the scene
    # itself was never manually edited.
    video = Video.objects.create(path="/videos/tape.mp4", duration_seconds=60.0)
    run = _run(video)
    boundary = SceneBoundary.objects.create(
        video=video, run=run, timestamp_seconds=30.0, review_status=SceneBoundary.ReviewStatus.APPROVED
    )
    rebuild_scenes(video)
    assert video.scenes.get(start_seconds=0.0).description == ""

    boundary.before_description = "Backyard BBQ"
    boundary.save(update_fields=["before_description"])
    rebuild_scenes(video)

    assert video.scenes.get(start_seconds=0.0).description == "Backyard BBQ"
