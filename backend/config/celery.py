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
def _sweep_on_startup(sender=None, **kwargs):
    # Hit for real: restarting a worker container to deploy a code change
    # silently orphaned an in-progress DetectionRun -- it stayed "running"
    # forever with no worker actually processing it. Since there's now two
    # worker containers (see docker-compose.yml: `worker` for detection,
    # `worker_encode` for encoding), this signal fires once per container
    # on every restart -- only let the "detect@" one (hostname set via
    # --hostname in docker-compose.yml) actually run the sweep, or both
    # containers coming up together would race to reset/re-queue the same
    # orphaned rows at once.
    hostname = getattr(sender, "hostname", "") or ""
    if not hostname.startswith("detect@"):
        return

    from videos.tasks import sweep_orphaned_work

    swept = sweep_orphaned_work(only_unconditional=True)
    if swept["runs"] or swept["scenes"] or swept["pending_queued"]:
        print(
            f"[startup sweep] recovered {swept['runs']} run(s), {swept['scenes']} scene encode(s), "
            f"auto-queued {swept['pending_queued']} pending video(s)"
        )
