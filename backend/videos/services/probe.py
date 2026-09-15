import json
import subprocess


def probe_duration_seconds(path: str) -> float | None:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    duration = data.get("format", {}).get("duration")
    return float(duration) if duration is not None else None
