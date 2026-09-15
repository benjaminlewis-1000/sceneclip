# Test-only settings: supplies safe defaults for the env vars settings.py
# requires (so tests don't need a real .env), and swaps Postgres for an
# in-memory sqlite DB and Celery for synchronous eager execution so tests
# don't need Redis/Postgres/a worker running.
import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("AUTHELIA_SECRET", "test-secret")

from .settings import *  # noqa: E402,F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
PREVIEW_CLIPS_DIR = "/tmp/sceneclip_test_previews"
# Without this, a test that approves a boundary triggers a real (eager)
# export_scene_task, which falls through to the real OUTPUT_ROOT bind mount
# and creates a stray directory there -- hit this for real: approving a
# boundary in test_api.py created a "tape3" folder in the actual production
# output share.
OUTPUT_ROOT = "/tmp/sceneclip_test_output"
