# Django settings for the SceneClip project. Required env vars (no
# defaults, will raise on missing) are listed in .env.example. Auth mirrors
# django_picasa's Authelia-as-OIDC-provider pattern.
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# Django's own default (DEBUG=False, no ADMINS configured) is to mail
# nobody and print nothing -- a request-handling exception is completely
# invisible in `docker logs`. Route it to the console instead, which
# `docker logs` already captures; allauth logs its own social-login
# failures (token exchange errors, etc.) through "allauth.*" separately
# from Django's own "django.request" 500 logging, so both are wired here.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "allauth": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "rest_framework",
    "corsheaders",
    "videos",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",
]

SITE_ID = 1

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Postgres, pointed at the `db` container by default (see docker-compose.yml).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TZ", "America/New_York")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# App-specific paths. /videos and /output are host bind mounts (read-only
# source, read-write export target respectively -- see docker-compose.yml).
# ---------------------------------------------------------------------------
VIDEO_ROOT = os.environ.get("VIDEO_ROOT_CONTAINER", "/videos")
OUTPUT_ROOT = os.environ.get("OUTPUT_ROOT_CONTAINER", "/output")
# Scene encodes land here first, not in OUTPUT_ROOT -- a fresh auto-encode
# hasn't been watched by a human yet, and OUTPUT_ROOT is meant to hold only
# clips someone's actually confirmed are right. Living under VIDEO_ROOT
# (rather than its own top-level mount) keeps it on the same filesystem as
# the source tapes without needing another docker-compose volume; it's
# excluded from library scanning/browsing (see services/library.py,
# services/browse.py) so it never shows up as something to detect scenes
# in. The `verify` action (services/export.py:finalize_scene) moves a clip
# from here into OUTPUT_ROOT once a human confirms it.
TEMP_SCENE_CLIPS_DIR = os.path.join(VIDEO_ROOT, "_temp_scene_clips")
PREVIEW_CLIPS_DIR = os.environ.get("PREVIEW_CLIPS_DIR", "/previews")

# ---------------------------------------------------------------------------
# DRF -- IsAuthenticated is the DEFAULT permission class (not opt-in per
# view), so a view added without thinking about auth is closed by default
# rather than silently public.
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
# Default uses the container_name (sceneclip_redis), not the bare compose
# service name -- see the comment in .env.example on the collision this
# caused with other projects' "redis" containers on the shared network.
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://sceneclip_redis:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_TRACK_STARTED = True
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
# Without TCP keepalive, a Redis connection that goes stale while idle (a
# dropped connection nobody explicitly closed) leaves the worker blocked on
# a read that will never return -- no error, no crash, just a queue that
# silently stops draining. Hit this for real: 70 queued tasks sat untouched
# for 12+ minutes after the first few ran, and only a worker restart
# unstuck it. socket_keepalive plus a bounded timeout means a truly-dead
# connection gets noticed and torn down instead of hanging forever.
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "socket_keepalive": True,
    "socket_timeout": 30,
    "socket_connect_timeout": 30,
}
# Detection and encoding get their own dedicated worker (see
# docker-compose.yml: `worker` consumes "detection"+"celery", `worker_encode`
# consumes "encoding") so a big detection backlog can't starve scene
# encoding, or vice versa -- everything else not listed here (metadata
# backfill, the orphaned-work sweep) rides the default "celery" queue.
CELERY_TASK_ROUTES = {
    "videos.tasks.run_detection_task": {"queue": "detection"},
    "videos.tasks.export_scene_task": {"queue": "encoding"},
    "videos.tasks.export_video_task": {"queue": "encoding"},
    # Thumbnail/duration backfill for newly-added videos -- cheap and fast,
    # but was sharing the detection worker's 2 slots with run_detection_task
    # (both on the default "celery" queue there), so a big detection
    # backlog could leave a fresh video showing a placeholder thumbnail for
    # a long time even though generating it takes a couple seconds. The
    # encoding worker is the less contended of the two in practice (fewer,
    # shorter-lived jobs than a multi-hour detection backlog).
    "videos.tasks.generate_video_metadata_task": {"queue": "encoding"},
}

# ---------------------------------------------------------------------------
# Auth: Authelia as an OIDC provider via django-allauth, same pattern as
# django_picasa. The reverse-tunnel/nginx layer does no auth of its own here
# -- Django's own login (backed by Authelia) is the sole gate, so every view
# must stay behind IsAuthenticated above.
# ---------------------------------------------------------------------------
SOCIALACCOUNT_PROVIDERS = {
    "openid_connect": {
        "APPS": [
            {
                "provider_id": "authelia",
                "name": "Authelia SSO",
                "client_id": "sceneclip",
                "secret": os.environ["AUTHELIA_SECRET"],
                "settings": {
                    "server_url": "https://auth.exploretheworld.tech/.well-known/openid-configuration",
                    # Authelia's discovery doc advertises client_secret_basic
                    # as a globally supported method, so allauth's adapter
                    # defaults to it -- but this client was registered in
                    # Authelia's config with token_endpoint_auth_method:
                    # 'client_secret_post' only, and Authelia rejects the
                    # mismatch with invalid_client. Must match that exactly.
                    "token_auth_method": "client_secret_post",
                },
            }
        ]
    }
}

SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_ADAPTER = "videos.adapters.LoggingSocialAccountAdapter"

LOGIN_REDIRECT_URL = f"https://{os.environ.get('APP_DOMAIN', 'localhost')}/"
LOGOUT_REDIRECT_URL = f"https://{os.environ.get('APP_DOMAIN', 'localhost')}/"
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"

# Unique cookie names, not Django's defaults ("csrftoken"/"sessionid").
# Root-caused a real bug: django_picasa, a sibling app on this same parent
# domain, sets CSRF_COOKIE_DOMAIN/SESSION_COOKIE_DOMAIN to the wildcard
# '.exploretheworld.tech', making its cookies visible on every subdomain
# including this one. With the same default cookie names, a browser logged
# into both apps ends up with two different "csrftoken" cookies scoped to
# this origin (picasa's wildcard one, this app's host-only one) -- the
# browser sends both, and the JS reading document.cookie vs. Django parsing
# the Cookie header can resolve the duplicate to different values, so every
# POST/PATCH/DELETE CSRF-failed with a token that looked stable but was
# actually just consistently wrong. Verified via direct log capture of the
# cookie vs. header values on a real request. This app never sets a
# wildcard cookie domain (no *_COOKIE_DOMAIN override above), so a unique
# name is enough to make the collision structurally impossible regardless
# of what other apps on the domain do.
CSRF_COOKIE_NAME = "sceneclip_csrftoken"
SESSION_COOKIE_NAME = "sceneclip_sessionid"

# The nginx frontend container terminates the browser connection and proxies
# to this service over plain HTTP inside the docker network, forwarding
# X-Forwarded-Proto -- trust that header for Django's own scheme detection
# (secure cookies, redirect scheme, allauth's callback URL building) rather
# than assuming every request arrived in the clear.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

CSRF_TRUSTED_ORIGINS = [
    f"https://{os.environ.get('APP_DOMAIN', 'localhost')}",
]
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = [
    origin
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin
]
