# Top-level URL map: Django admin, allauth's login/callback views (Authelia
# OIDC flow), and the DRF API under /api/. The built React SPA is served by
# the nginx frontend container in production, not by Django -- see
# frontend/nginx.conf.
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("api/", include("videos.urls")),
]
