"""Resolves which detection params a video should run with: an explicit
override wins, otherwise the library-wide default (DetectionParams, a
singleton row, created with model defaults on first use). Shared by the
`detect` view action and the auto-queue sweep (tasks.py) so both pick the
same params a manual click would.
"""
from ..models import DetectionParams


def resolve_detection_params(video) -> dict:
    if video.detection_params_override:
        return video.detection_params_override
    obj, _ = DetectionParams.objects.get_or_create(pk=1)
    return {
        "detector": obj.detector,
        "threshold": obj.threshold,
        "min_scene_len_seconds": obj.min_scene_len_seconds,
    }
