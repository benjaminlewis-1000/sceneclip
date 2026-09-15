# Django settings for the SceneClip project. Required env vars (no
# defaults, will raise on missing) are listed in .env.example. Auth mirrors
# django_picasa's Authelia-as-OIDC-provider pattern.
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

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
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_TRACK_STARTED = True
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

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
                    "server_url": "https://auth.exploretheworld.tech/.well-known/openid-configuration"
                },
            }
        ]
    }
}

SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_LOGIN_ON_GET = True

LOGIN_REDIRECT_URL = f"https://{os.environ.get('APP_DOMAIN', 'localhost')}/"
LOGOUT_REDIRECT_URL = f"https://{os.environ.get('APP_DOMAIN', 'localhost')}/"
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"

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
