# API-level smoke tests: auth is enforced by default (the whole point of
# DEFAULT_PERMISSION_CLASSES=[IsAuthenticated] in settings.py), and the
# review/adjust endpoints behave as documented.
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from videos.models import DetectionRun, SceneBoundary, Video

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
    boundary = SceneBoundary.objects.create(video=video, run=run, timestamp_seconds=30.0)

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
