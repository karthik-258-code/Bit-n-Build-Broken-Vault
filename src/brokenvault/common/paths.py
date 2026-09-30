"""Relative path validation shared by client and server (LLD 4.2)."""

import os
import re

from .constants import MAX_SEGMENT_BYTES
from .errors import BVError

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _bad(path, why):
    return BVError("INVALID_PATH", f"invalid path {path!r}: {why}", {"path": str(path)})


def validate_rel_path(p):
    """Return p if it is a safe relative path using '/', else raise INVALID_PATH."""
    if not isinstance(p, str):
        raise _bad(p, "not a string")
    if p == "":
        raise _bad(p, "empty path")
    try:
        p.encode("utf-8")
    except UnicodeEncodeError:
        raise _bad(p, "not valid UTF-8") from None
    if "\x00" in p:
        raise _bad(p, "contains NUL")
    if "\\" in p:
        raise _bad(p, "contains a backslash; use '/'")
    if p.startswith("/"):
        raise _bad(p, "absolute path")
    if _DRIVE_RE.match(p):
        raise _bad(p, "drive prefix")
    for segment in p.split("/"):
        if segment == "":
            raise _bad(p, "empty segment")
        if segment in (".", ".."):
            raise _bad(p, f"segment {segment!r} is not allowed")
        if len(segment.encode("utf-8")) > MAX_SEGMENT_BYTES:
            raise _bad(p, f"segment longer than {MAX_SEGMENT_BYTES} bytes")
    return p


def to_posix(rel):
    """Convert an OS relative path to the manifest form."""
    return rel.replace(os.sep, "/")


def parent_paths(p):
    """'a/b/c' -> ['a', 'a/b']."""
    parts = p.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def safe_join(root, rel):
    """Join a validated relative path under root; raise UNSAFE_PATH on escape."""
    validate_rel_path(rel)
    root_real = os.path.realpath(root)
    resolved = os.path.realpath(os.path.join(root_real, *rel.split("/")))
    if not resolved.startswith(root_real.rstrip(os.sep) + os.sep):
        raise BVError("UNSAFE_PATH", f"path {rel!r} escapes the destination folder", {"path": rel})
    return resolved
