# API-level smoke tests: auth is enforced by default (the whole point of
# DEFAULT_PERMISSION_CLASSES=[IsAuthenticated] in settings.py), and the
# review/adjust endpoints behave as documented.
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from videos.models import DetectionRun, Notification, Scene, SceneBoundary, Video

pytestmark = pytest.mark.django_db


def test_videos_endpoint_requires_authentication():
    client = APIClient()
    response = client.get("/api/videos/")
    assert response.status_code in (401, 403)


def test_authenticated_user_can_create_and_list_video():
    user = get_user_model().objects.create_user(username="benjamin", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    create_response = client.post("/api/videos/", {"path": "/videos/tape1.mp4"}, format="json")
    assert create_response.status_code == 201

    list_response = client.get("/api/videos/")
    assert list_response.status_code == 200
    assert len(list_response.data) == 1


def test_review_boundary_rejects_invalid_verdict():
    user = get_user_model().objects.create_user(username="benjamin2", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape2.mp4")
    run = DetectionRun.objects.create(video=video, params={})
    boundary = SceneBoundary.objects.create(video=video, run=run, timestamp_seconds=10.0)

    response = client.post(f"/api/boundaries/{boundary.id}/review/", {"verdict": "maybe"}, format="json")
    assert response.status_code == 400


def test_review_boundary_approve_creates_derived_scenes():
    user = get_user_model().objects.create_user(username="benjamin3", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape3.mp4", duration_seconds=60.0)
    run = DetectionRun.objects.create(video=video, params={})
    boundary = SceneBoundary.objects.create(
        video=video,
        run=run,
        timestamp_seconds=30.0,
        before_date="1994-01-01",
        after_date="1994-01-02",
    )

    response = client.post(f"/api/boundaries/{boundary.id}/review/", {"verdict": "approved"}, format="json")
    assert response.status_code == 200
    assert video.scenes.count() == 2


def test_adjust_boundary_moves_timestamp_and_rebuilds_scenes():
    user = get_user_model().objects.create_user(username="benjamin4", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape4.mp4", duration_seconds=60.0)
    run = DetectionRun.objects.create(video=video, params={})
    boundary = SceneBoundary.objects.create(
        video=video, run=run, timestamp_seconds=30.0, review_status=SceneBoundary.ReviewStatus.APPROVED
    )

    response = client.post(
        f"/api/boundaries/{boundary.id}/adjust/", {"timestamp_seconds": 31.5}, format="json"
    )
    assert response.status_code == 200
    boundary.refresh_from_db()
    assert boundary.timestamp_seconds == 31.5
    assert list(video.scenes.order_by("start_seconds").values_list("start_seconds", "end_seconds")) == [
        (0.0, 31.5),
        (31.5, 60.0),
    ]


def test_boundary_queue_returns_oldest_pending_across_library():
    user = get_user_model().objects.create_user(username="benjamin5", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape5.mp4")
    run = DetectionRun.objects.create(video=video, params={})
    first = SceneBoundary.objects.create(video=video, run=run, timestamp_seconds=10.0)
    SceneBoundary.objects.create(video=video, run=run, timestamp_seconds=20.0)

    response = client.get("/api/boundaries/queue/")
    assert response.status_code == 200
    assert response.data["id"] == first.id


def test_video_serializer_flags_boundary_and_approval_state():
    # Drives the frontend's disabling of "Review this video" (needs
    # has_boundaries) and "Export clips" (needs has_approved_boundaries).
    user = get_user_model().objects.create_user(username="benjamin6", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape6.mp4")
    response = client.get(f"/api/videos/{video.id}/")
    assert response.data["has_boundaries"] is False
    assert response.data["has_approved_boundaries"] is False

    run = DetectionRun.objects.create(video=video, params={})
    boundary = SceneBoundary.objects.create(video=video, run=run, timestamp_seconds=10.0)
    response = client.get(f"/api/videos/{video.id}/")
    assert response.data["has_boundaries"] is True
    assert response.data["has_approved_boundaries"] is False

    boundary.review_status = SceneBoundary.ReviewStatus.APPROVED
    boundary.save(update_fields=["review_status"])
    response = client.get(f"/api/videos/{video.id}/")
    assert response.data["has_approved_boundaries"] is True


def test_detect_marks_video_detecting_immediately_at_queue_time():
    # Video.status must flip the moment a run is queued, not only once a
    # Celery worker slot actually starts it -- otherwise, with worker
    # concurrency capped, a video queued behind others in the backlog looks
    # untouched ("pending", Reprocess still enabled) even though a run
    # genuinely exists for it. Patches out the task itself: under
    # CELERY_TASK_ALWAYS_EAGER it would otherwise run synchronously inline
    # (and immediately fail against a nonexistent path, reverting the
    # status again via run_detection_task's own failure handling) -- this
    # test is only about the view's own synchronous status-setting, not
    # what the task does afterward.
    user = get_user_model().objects.create_user(username="benjamin7", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape7.mp4")
    with mock.patch("videos.views.run_detection_task.delay"):
        response = client.post(f"/api/videos/{video.id}/detect/", {}, format="json")
    assert response.status_code == 202

    video.refresh_from_db()
    assert video.status == Video.Status.DETECTING


def test_notifications_clear_all():
    user = get_user_model().objects.create_user(username="benjamin8", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape8.mp4")
    Notification.objects.create(video=video, kind=Notification.Kind.DETECTION_DONE, message="a")
    Notification.objects.create(video=video, kind=Notification.Kind.DETECTION_DONE, message="b")

    response = client.post("/api/notifications/clear_all/")
    assert response.status_code == 200
    assert response.data["deleted"] == 2
    assert Notification.objects.count() == 0


def test_boundary_patch_updates_before_after_fields():
    user = get_user_model().objects.create_user(username="benjamin9", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape9.mp4")
    run = DetectionRun.objects.create(video=video, params={})
    boundary = SceneBoundary.objects.create(video=video, run=run, timestamp_seconds=10.0)

    response = client.patch(
        f"/api/boundaries/{boundary.id}/",
        {"after_description": "Birthday cake", "after_date": "1994-06-01"},
        format="json",
    )
    assert response.status_code == 200
    boundary.refresh_from_db()
    assert boundary.after_description == "Birthday cake"
    assert str(boundary.after_date) == "1994-06-01"


def test_boundary_patch_cannot_set_review_status_directly():
    # review_status must only change through the dedicated `review` action
    # (which also triggers rebuild_scenes()) -- a plain PATCH silently
    # ignores it rather than applying it.
    user = get_user_model().objects.create_user(username="benjamin10", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape10.mp4")
    run = DetectionRun.objects.create(video=video, params={})
    boundary = SceneBoundary.objects.create(video=video, run=run, timestamp_seconds=10.0)

    client.patch(
        f"/api/boundaries/{boundary.id}/",
        {"review_status": "approved"},
        format="json",
    )
    boundary.refresh_from_db()
    assert boundary.review_status == SceneBoundary.ReviewStatus.PENDING


def test_review_approve_requires_both_before_and_after_date():
    user = get_user_model().objects.create_user(username="benjamin11", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape11.mp4", duration_seconds=60.0)
    run = DetectionRun.objects.create(video=video, params={})
    boundary = SceneBoundary.objects.create(
        video=video, run=run, timestamp_seconds=30.0, before_date="1994-01-01"
    )  # after_date still missing

    response = client.post(f"/api/boundaries/{boundary.id}/review/", {"verdict": "approved"}, format="json")
    assert response.status_code == 400
    boundary.refresh_from_db()
    assert boundary.review_status == SceneBoundary.ReviewStatus.PENDING


def test_review_reject_only_requires_before_date():
    # Reject means no real cut -- before/after describe the same continuous
    # scene -- so after_date isn't required to proceed.
    user = get_user_model().objects.create_user(username="benjamin12", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape12.mp4", duration_seconds=60.0)
    run = DetectionRun.objects.create(video=video, params={})
    boundary = SceneBoundary.objects.create(
        video=video, run=run, timestamp_seconds=30.0, before_date="1994-01-01"
    )

    response = client.post(f"/api/boundaries/{boundary.id}/review/", {"verdict": "rejected"}, format="json")
    assert response.status_code == 200


def test_scene_encode_requires_date():
    user = get_user_model().objects.create_user(username="benjamin13", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape13.mp4", duration_seconds=60.0)
    scene = Scene.objects.create(video=video, start_seconds=0.0, end_seconds=30.0)

    response = client.post(f"/api/scenes/{scene.id}/encode/")
    assert response.status_code == 400


def test_scene_clip_404s_when_not_encoded():
    user = get_user_model().objects.create_user(username="benjamin14", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape14.mp4", duration_seconds=60.0)
    scene = Scene.objects.create(video=video, start_seconds=0.0, end_seconds=30.0)

    response = client.get(f"/api/scenes/{scene.id}/clip/")
    assert response.status_code == 404


def test_scene_verify_requires_encoded_and_dated():
    user = get_user_model().objects.create_user(username="benjamin15", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape15.mp4", duration_seconds=60.0)
    scene = Scene.objects.create(video=video, start_seconds=0.0, end_seconds=30.0)

    response = client.post(f"/api/scenes/{scene.id}/verify/")
    assert response.status_code == 400  # not exported yet

    scene.exported = True
    scene.save(update_fields=["exported"])
    response = client.post(f"/api/scenes/{scene.id}/verify/")
    assert response.status_code == 400  # exported but still no date

    scene.scene_date = "1994-01-01"
    scene.save(update_fields=["scene_date"])
    response = client.post(f"/api/scenes/{scene.id}/verify/")
    assert response.status_code == 200
    scene.refresh_from_db()
    assert scene.verified is True


def test_scene_verify_queue_returns_next_unverified_exported_scene():
    user = get_user_model().objects.create_user(username="benjamin16", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape16.mp4", duration_seconds=60.0)
    Scene.objects.create(video=video, start_seconds=0.0, end_seconds=30.0)  # not exported -- excluded
    ready = Scene.objects.create(
        video=video, start_seconds=30.0, end_seconds=60.0, exported=True, scene_date="1994-01-01"
    )

    response = client.get("/api/scenes/verify_queue/")
    assert response.status_code == 200
    assert response.data["id"] == ready.id


def test_undo_boundary_endpoint_reverts_and_rebuilds():
    user = get_user_model().objects.create_user(username="benjamin17", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape17.mp4", duration_seconds=60.0)
    run = DetectionRun.objects.create(video=video, params={})
    boundary = SceneBoundary.objects.create(
        video=video, run=run, timestamp_seconds=30.0, review_status=SceneBoundary.ReviewStatus.APPROVED
    )
    from videos.services.scenes import rebuild_scenes
    rebuild_scenes(video)
    assert video.scenes.count() == 2

    response = client.post(f"/api/boundaries/{boundary.id}/undo/")
    assert response.status_code == 200
    boundary.refresh_from_db()
    assert boundary.review_status == SceneBoundary.ReviewStatus.PENDING
    assert video.scenes.count() == 1


def test_undo_boundary_endpoint_rejects_already_pending():
    user = get_user_model().objects.create_user(username="benjamin18", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape18.mp4")
    run = DetectionRun.objects.create(video=video, params={})
    boundary = SceneBoundary.objects.create(video=video, run=run, timestamp_seconds=10.0)

    response = client.post(f"/api/boundaries/{boundary.id}/undo/")
    assert response.status_code == 400


def test_undo_boundary_endpoint_blocks_while_encoding():
    user = get_user_model().objects.create_user(username="benjamin19", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape19.mp4", duration_seconds=60.0)
    run = DetectionRun.objects.create(video=video, params={})
    boundary = SceneBoundary.objects.create(
        video=video, run=run, timestamp_seconds=30.0, review_status=SceneBoundary.ReviewStatus.APPROVED
    )
    from videos.services.scenes import rebuild_scenes
    rebuild_scenes(video)
    scene = video.scenes.get(start_seconds=0.0)
    scene.export_progress_percent = 10
    scene.save(update_fields=["export_progress_percent"])

    response = client.post(f"/api/boundaries/{boundary.id}/undo/")
    assert response.status_code == 400
    boundary.refresh_from_db()
    assert boundary.review_status == SceneBoundary.ReviewStatus.APPROVED


def test_task_queue_endpoint_lists_running_and_queued_work():
    user = get_user_model().objects.create_user(username="benjamin20", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    video = Video.objects.create(path="/videos/tape20.mp4", duration_seconds=60.0)
    DetectionRun.objects.create(video=video, params={}, status=DetectionRun.Status.RUNNING, progress_percent=40)
    DetectionRun.objects.create(video=video, params={}, status=DetectionRun.Status.QUEUED)
    Scene.objects.create(
        video=video, start_seconds=0.0, end_seconds=10.0,
        export_progress_percent=55, encode_started_at="2026-01-01T00:00:00Z",
    )
    Scene.objects.create(video=video, start_seconds=10.0, end_seconds=20.0, export_progress_percent=0)

    response = client.get("/api/queue/")
    assert response.status_code == 200
    assert [r["status"] for r in response.data["detection"]] == ["running", "queued"]
    assert [s["status"] for s in response.data["encoding"]] == ["encoding", "queued"]
