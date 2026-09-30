"""Completed versions: list and manifest reconstruction (LLD 5.6)."""

import re

from ..common.errors import BVError
from .upload_service import version_summary

_VERSION_RE = re.compile(r"[Vv]?(\d{1,18})")


def parse_version_id(text):
    """'V3', 'v3' or '3' -> 3. Anything else (such as an upload ID) is not a version."""
    match = _VERSION_RE.fullmatch(text or "")
    if not match:
        raise BVError("VERSION_NOT_FOUND", f"no completed version {text!r}", {"version": text})
    return int(match.group(1))


class VersionService:
    def __init__(self, db):
        self.db = db

    def list_versions(self):
        rows = self.db.query("SELECT * FROM versions ORDER BY version_id")
        return [version_summary(r) for r in rows]

    def get_version(self, text):
        vid = parse_version_id(text)
        row = self.db.query_one("SELECT * FROM versions WHERE version_id = ?", (vid,))
        if row is None:
            raise BVError("VERSION_NOT_FOUND", f"no completed version {text!r}", {"version": text})
        chunks = {}
        for c in self.db.query(
                "SELECT path, chunk_id, size FROM entry_chunks WHERE version_id = ? ORDER BY path, idx", (vid,)):
            chunks.setdefault(c["path"], []).append({"id": c["chunk_id"], "size": c["size"]})
        entries = []
        for e in self.db.query("SELECT * FROM entries WHERE version_id = ? ORDER BY path", (vid,)):
            if e["type"] == "dir":
                entries.append({"path": e["path"], "type": "dir", "mtime_ns": e["mtime_ns"]})
            else:
                entries.append({"path": e["path"], "type": "file", "size": e["size"],
                                "mtime_ns": e["mtime_ns"], "chunks": chunks.get(e["path"], [])})
        out = version_summary(row)
        out.update({"format": 1, "chunker": {"algo": "fixed", "size": row["chunker_size"]}, "entries": entries})
        return out
