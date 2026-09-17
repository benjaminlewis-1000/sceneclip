# Covers run_detection's backend choice. Hit for real: several .mpg
# captures (interlaced MPEG-1/2) silently produced zero candidate
# boundaries -- PySceneDetect's default OpenCV backend's bundled ffmpeg/
# swscale fails to convert interlaced->progressive frames, and that
# failure never surfaces as an exception, so detection "succeeds" having
# read nothing. PyAV (same ffmpeg/libav stack the rest of this app already
# uses) decodes it correctly -- verified manually against the actual
# affected files. This test only guards against the backend choice being
# silently reverted; it doesn't re-verify the underlying OpenCV bug, which
# needs a real interlaced fixture file to reproduce.
from unittest import mock

from videos.services.detection import run_detection


def test_run_detection_opens_video_with_pyav_backend():
    with mock.patch("videos.services.detection.open_video") as mock_open_video, \
            mock.patch("videos.services.detection.SceneManager") as mock_scene_manager_cls:
        mock_open_video.return_value.frame_rate = 30.0
        mock_scene_manager_cls.return_value.get_scene_list.return_value = []

        run_detection("/videos/tape.mpg", {"detector": "adaptive", "threshold": 3.0, "min_scene_len_seconds": 2.0})

        mock_open_video.assert_called_once_with("/videos/tape.mpg", backend="pyav")
