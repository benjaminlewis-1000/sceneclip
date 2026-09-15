# Celery app entrypoint, imported by config/__init__.py so `shared_task`
# decorated functions elsewhere in the project pick up Django's settings
# (CELERY_* keys) automatically.
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("sceneclip")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
