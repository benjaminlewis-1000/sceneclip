"""PySceneDetect wrapper tuned for noisy VHS-rip source video.

AdaptiveDetector is the default rather than ContentDetector: it compares each
frame's content-detector score against a rolling window of neighboring
frames instead of a single fixed threshold, which makes it considerably more
resilient to the slow lighting drift, tracking noise, and head-switching
artifacts common in analog VHS captures. A minimum scene length (in frames,
derived from the source's own frame rate) rejects momentary tracking glitches
that would otherwise register as a false-positive cut.
"""
from typing import Callable, Optional

from scenedetect import FrameTimecode, SceneManager, open_video
from scenedetect.detectors import AdaptiveDetector, ContentDetector

DEFAULT_PARAMS = {
    "detector": "adaptive",
    "threshold": 3.0,
    "min_scene_len_seconds": 2.0,
}

# PySceneDetect's own `callback` param only fires when a cut is *found*, not
# per-frame -- useless for a smooth progress bar on a video with long,
# static scenes. Instead we drive detect_scenes() in fixed-size time chunks
# ourselves (it supports this: each call resumes from the video's current
# position) and report progress after each chunk completes.
PROGRESS_CHUNK_SECONDS = 15.0


def run_detection(
    video_path: str, params: dict, progress_callback: Optional[Callable[[int], None]] = None
) -> list[float]:
    """Run PySceneDetect over `video_path` and return candidate cut-point
    timestamps (in seconds), excluding the very start and end of the video.
    """
    detector_name = params.get("detector", DEFAULT_PARAMS["detector"])
    threshold = params.get("threshold", DEFAULT_PARAMS["threshold"])
    min_scene_len_seconds = params.get(
        "min_scene_len_seconds", DEFAULT_PARAMS["min_scene_len_seconds"]
    )

    video = open_video(video_path)
    min_scene_len_frames = max(1, int(min_scene_len_seconds * video.frame_rate))

    if detector_name == "content":
        detector = ContentDetector(threshold=threshold, min_scene_len=min_scene_len_frames)
    else:
        detector = AdaptiveDetector(
            adaptive_threshold=threshold, min_scene_len=min_scene_len_frames
        )

    scene_manager = SceneManager()
    scene_manager.add_detector(detector)

    total_seconds = video.duration.get_seconds() if video.duration else None
    if total_seconds and progress_callback:
        processed_seconds = 0.0
        while processed_seconds < total_seconds:
            step = min(PROGRESS_CHUNK_SECONDS, total_seconds - processed_seconds)
            scene_manager.detect_scenes(video=video, duration=FrameTimecode(step, video.frame_rate))
            processed_seconds += step
            progress_callback(min(99, int(processed_seconds / total_seconds * 100)))
    else:
        scene_manager.detect_scenes(video=video)

    if progress_callback:
        progress_callback(100)

    scene_list = scene_manager.get_scene_list()

    # scene_list covers the whole video as consecutive (start, end) pairs;
    # the boundary between scene N and N+1 is scene_list[N][1] ==
    # scene_list[N+1][0]. Only the internal cut points are candidates.
    return [start.get_seconds() for start, _end in scene_list[1:]]
