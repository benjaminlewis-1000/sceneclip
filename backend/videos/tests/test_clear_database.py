# Covers the settings-page "clear database" action: requires the exact
# confirm phrase (a second guard beyond the frontend's own confirmation UI
# for a destructive, irreversible action) and actually wipes Video rows.
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from videos.models import Video

pytestmark = pytest.mark.django_db


def test_clear_database_requires_exact_confirm_phrase():
    user = get_user_model().objects.create_user(username="clear1", password="x")
    client = APIClient()
    client.force_authenticate(user=user)
    Video.objects.create(path="/videos/tape1.mp4")

    response = client.post("/api/clear-database/", {"confirm": "wrong"}, format="json")
    assert response.status_code == 400
    assert Video.objects.count() == 1


def test_clear_database_wipes_all_videos_with_correct_phrase():
    user = get_user_model().objects.create_user(username="clear2", password="x")
    client = APIClient()
    client.force_authenticate(user=user)
    Video.objects.create(path="/videos/tape1.mp4")
    Video.objects.create(path="/videos/tape2.mp4")

    response = client.post("/api/clear-database/", {"confirm": "CLEAR"}, format="json")
    assert response.status_code == 200
    assert response.data["deleted"] >= 2
    assert Video.objects.count() == 0


def test_clear_database_requires_authentication():
    client = APIClient()
    response = client.post("/api/clear-database/", {"confirm": "CLEAR"}, format="json")
    assert response.status_code == 403
