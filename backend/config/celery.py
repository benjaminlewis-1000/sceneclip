# Celery app entrypoint, imported by config/__init__.py so `shared_task`
# decorated functions elsewhere in the project pick up Django's settings
# (CELERY_* keys) automatically.
import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("sceneclip")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Every 15 minutes, catch a hung task on a worker that's still alive (see
# videos/tasks.py:sweep_orphaned_work). Requires the worker to run with -B
# (embedded beat) -- see docker-compose.yml.
app.conf.beat_schedule = {
    "sweep-orphaned-work": {
        "task": "videos.tasks.sweep_orphaned_work_task",
        "schedule": crontab(minute="*/15"),
    },
}


@worker_ready.connect
def _sweep_on_startup(**kwargs):
    # Hit for real: restarting the worker container to deploy a code change
    # silently orphaned an in-progress DetectionRun -- it stayed "running"
    # forever with no worker actually processing it. In this deployment
    # there's only ever one worker container, so anything still claiming
    # "running"/"mid-encode" the instant a fresh worker process comes up is
    # unconditionally orphaned, not just slow.
    from videos.tasks import sweep_orphaned_work

    swept = sweep_orphaned_work(only_unconditional=True)
    if swept["runs"] or swept["scenes"]:
        print(f"[startup sweep] recovered {swept['runs']} run(s), {swept['scenes']} scene encode(s)")
