# One-off/rerunnable backfill: stamps every already-exported, dated
# scene's file with the metadata (title/comment/description/date/
# creation_time) matching its *current* DB fields -- covers clips exported
# before export_one_scene wrote creation_time at all, and clips whose
# description/date got edited after the original encode (export_one_scene
# only runs once; finalize_scene re-stamps at verify time now, but that
# doesn't retroactively fix a clip that was verified before that existed,
# or edited again after). Remuxes (stream-copy, no re-encode -- fast,
# lossless) in place, regardless of whether the clip is already verified
# (in OUTPUT_ROOT) or still awaiting verification (in
# TEMP_SCENE_CLIPS_DIR) -- exported_path points at wherever it actually is.
import os
import subprocess

from django.core.management.base import BaseCommand

from videos.models import Scene
from videos.services.export import metadata_args_for_scene


class Command(BaseCommand):
    help = "Re-stamps container metadata into already-exported clips to match their current DB fields."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="List what would be stamped without touching any files."
        )

    def handle(self, *args, **options):
        scenes = (
            Scene.objects.filter(exported=True, scene_date__isnull=False)
            .exclude(exported_path="")
            .select_related("video")
        )
        stamped = 0
        for scene in scenes:
            path = scene.exported_path
            if not os.path.exists(path):
                self.stdout.write(self.style.WARNING(f"scene {scene.id}: file missing at {path}, skipping"))
                continue

            if options["dry_run"]:
                self.stdout.write(f"would stamp scene {scene.id} ({scene.scene_date}): {path}")
                continue

            tmp_path = path + ".remux.tmp.mp4"
            cmd = ["ffmpeg", "-y", "-i", path, "-c", "copy", *metadata_args_for_scene(scene), tmp_path]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
            except subprocess.CalledProcessError as exc:
                self.stdout.write(self.style.ERROR(f"scene {scene.id}: ffmpeg failed: {exc.stderr.decode()[-500:]}"))
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                continue

            os.replace(tmp_path, path)
            stamped += 1
            self.stdout.write(f"stamped scene {scene.id} ({scene.scene_date}): {path}")

        self.stdout.write(self.style.SUCCESS(f"Done -- {stamped} clip(s) stamped."))
