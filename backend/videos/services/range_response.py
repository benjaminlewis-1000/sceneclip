"""Minimal HTTP Range support for serving source video / preview clips.

Django's built-in FileResponse doesn't implement byte-range responses, which
HTML5 <video> needs for scrubbing/seeking. This is a small, dependency-free
implementation rather than pulling in django-sendfile2 for what's a single
code path.
"""
import os
import re

from django.http import HttpResponseNotFound, StreamingHttpResponse

_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


def serve_file_with_range(request, path: str, content_type: str = "video/mp4"):
    if not os.path.exists(path):
        return HttpResponseNotFound()

    file_size = os.path.getsize(path)
    range_header = request.META.get("HTTP_RANGE", "")
    match = _RANGE_RE.match(range_header)

    if not match:
        response = StreamingHttpResponse(
            _read_chunks(path, 0, file_size - 1), content_type=content_type
        )
        response["Content-Length"] = str(file_size)
        response["Accept-Ranges"] = "bytes"
        return response

    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else file_size - 1
    end = min(end, file_size - 1)
    length = max(0, end - start + 1)

    response = StreamingHttpResponse(
        _read_chunks(path, start, end), status=206, content_type=content_type
    )
    response["Content-Length"] = str(length)
    response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    response["Accept-Ranges"] = "bytes"
    return response


def _read_chunks(path: str, start: int, end: int, chunk_size: int = 1024 * 1024):
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
