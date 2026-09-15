# Plain default admin registrations -- useful as a debugging console for a
# single-user tool, no custom ModelAdmin needed yet.
from django.contrib import admin

from .models import DetectionParams, DetectionRun, Notification, Scene, SceneBoundary, Video

admin.site.register(Video)
admin.site.register(DetectionParams)
admin.site.register(DetectionRun)
admin.site.register(SceneBoundary)
admin.site.register(Scene)
admin.site.register(Notification)
