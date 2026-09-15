"""Final chapter export: cuts each approved+enriched Scene out of its source
video into settings.OUTPUT_ROOT, writing the enrichment fields in as
container metadata.
"""
import os
import subprocess


def _safe_name(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in base)


def export_scenes(video) -> list[str]:
    from django.conf import settings

    out_dir = os.path.join(settings.OUTPUT_ROOT, _safe_name(video.path))
    os.makedirs(out_dir, exist_ok=True)

    exported_paths = []
    scenes = video.scenes.filter(exported=False).order_by("start_seconds")
    for idx, scene in enumerate(scenes, start=1):
        out_path = os.path.join(out_dir, f"scene_{idx:03d}.mp4")
        duration = scene.end_seconds - scene.start_seconds

        metadata_args = []
        if scene.description:
            metadata_args += ["-metadata", f"title={scene.description}"]
        if scene.scene_date:
            metadata_args += ["-metadata", f"date={scene.scene_date.isoformat()}"]

        tmp_path = out_path + ".tmp.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(scene.start_seconds),
                "-i", video.path,
                "-t", str(duration),
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "aac",
                *metadata_args,
                tmp_path,
            ],
            check=True,
            capture_output=True,
        )
        os.rename(tmp_path, out_path)

        scene.exported = True
        scene.exported_path = out_path
        scene.save(update_fields=["exported", "exported_path"])
        exported_paths.append(out_path)

    return exported_paths
