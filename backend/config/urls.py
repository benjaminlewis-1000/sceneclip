# Top-level URL map: Django admin, allauth's login/callback views (Authelia
# OIDC flow), and the DRF API under /api/. The built React SPA is served by
# the nginx frontend container in production, not by Django -- see
# frontend/nginx.conf.
import os

from django.contrib import admin
from django.contrib.auth import logout as auth_logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import include, path
from django.views.decorators.csrf import ensure_csrf_cookie


@ensure_csrf_cookie
def csrf_bootstrap(request):
    # Django only ever *sets* the csrftoken cookie as a side effect of
    # get_token() running during a request -- which happens automatically
    # for Django's own template-rendered forms, but never for a JSON SPA
    # that only ever does fetch() calls. Without this, the browser has no
    # CSRF cookie to echo back on any POST/PATCH/DELETE, and every one of
    # those requests 403s regardless of being logged in. main.jsx hits this
    # once before rendering the app.
    return JsonResponse({"detail": "csrf cookie set"})


def logout_view(request):
    # Clears this app's own Django session, then also hits Authelia's
    # logout endpoint (?rd= bounces back here once done) so the SSO session
    # itself ends, not just this app's local one -- by request, this
    # deliberately also logs the user out of every other app sharing this
    # Authelia instance.
    auth_logout(request)
    app_domain = os.environ.get("APP_DOMAIN", "localhost")
    return redirect(f"https://auth.exploretheworld.tech/logout?rd=https://{app_domain}/")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("api/csrf/", csrf_bootstrap, name="csrf-bootstrap"),
    path("api/logout/", logout_view, name="logout"),
    path("api/", include("videos.urls")),
]
