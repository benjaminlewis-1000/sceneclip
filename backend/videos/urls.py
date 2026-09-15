# DRF router wires up standard CRUD + the custom @action routes for each
# viewset; detection-params is added by hand since it's a singleton (no pk)
# and doesn't fit the router's list/detail pattern.
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DetectionParamsView,
    NotificationViewSet,
    SceneBoundaryViewSet,
    SceneViewSet,
    VideoViewSet,
)

router = DefaultRouter()
router.register("videos", VideoViewSet)
router.register("boundaries", SceneBoundaryViewSet)
router.register("scenes", SceneViewSet)
router.register("notifications", NotificationViewSet)

urlpatterns = [
    path("detection-params/", DetectionParamsView.as_view(), name="detection-params"),
] + router.urls
