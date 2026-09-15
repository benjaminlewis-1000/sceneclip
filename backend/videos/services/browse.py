"""Backs the "pick a different file" UI. Scoped strictly under VIDEO_ROOT --
the container can't see anything else on the host, and this deliberately
doesn't try to pretend otherwise (no arbitrary host filesystem browsing).
"""
import os

from django.conf import settings

from .library import VIDEO_EXTENSIONS


class InvalidBrowsePath(Exception):
    pass


def list_directory(relative_path: str = "") -> dict:
    root = os.path.realpath(settings.VIDEO_ROOT)
    target = os.path.realpath(os.path.join(root, relative_path.lstrip("/")))

    if os.path.commonpath([root, target]) != root:
        raise InvalidBrowsePath("path escapes VIDEO_ROOT")
    if not os.path.isdir(target):
        raise InvalidBrowsePath("not a directory")

    entries = []
    for name in sorted(os.listdir(target)):
        full = os.path.join(target, name)
        if os.path.isdir(full):
            entries.append({"name": name, "type": "dir"})
        elif os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS:
            entries.append({"name": name, "type": "file", "path": full})

    rel = os.path.relpath(target, root)
    return {
        "path": "" if rel == "." else rel,
        "parent": None if target == root else os.path.relpath(os.path.dirname(target), root),
        "entries": entries,
    }
