"""Walk a source folder and build its manifest (LLD 6.2)."""

import os
from dataclasses import dataclass, field

from ..common import manifest as manifest_mod
from ..common.constants import DEFAULT_CHUNK_SIZE, MAX_CHUNK_SIZE
from ..common.errors import BVError
from ..common.paths import validate_rel_path
from .chunker import iter_chunks


@dataclass
class ScanResult:
    manifest: dict
    # chunk_id -> (absolute file path, offset, length): where to re-read a chunk for upload
    locations: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def _is_link(entry):
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)  # Python 3.12+, Windows
    return bool(is_junction and is_junction())


def scan(root, chunk_size=DEFAULT_CHUNK_SIZE):
    """Scan root and return its validated manifest plus chunk locations."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise BVError("INVALID_PATH", f"source folder {root!r} does not exist or is not a folder", {"path": root})
    if not 0 < chunk_size <= MAX_CHUNK_SIZE:
        raise BVError("INVALID_MANIFEST", f"chunk size must be 1..{MAX_CHUNK_SIZE} bytes")

    entries, locations, warnings = [], {}, []

    def walk(dir_abs, prefix):
        with os.scandir(dir_abs) as it:
            items = sorted(it, key=lambda e: e.name)
        for item in items:
            rel = f"{prefix}/{item.name}" if prefix else item.name
            if _is_link(item):
                warnings.append(f"skipped symlink: {rel}")
                continue
            if item.is_dir(follow_symlinks=False):
                validate_rel_path(rel)
                st = os.lstat(item.path)  # DirEntry.stat() can be stale on Windows
                entries.append({"path": rel, "type": "dir", "mtime_ns": max(0, st.st_mtime_ns)})
                walk(item.path, rel)
            elif item.is_file(follow_symlinks=False):
                validate_rel_path(rel)
                st = os.lstat(item.path)  # DirEntry.stat() can be stale on Windows
                chunks, size = [], 0
                for offset, length, cid in iter_chunks(item.path, chunk_size):
                    chunks.append({"id": cid, "size": length})
                    locations.setdefault(cid, (item.path, offset, length))
                    size += length
                entries.append({"path": rel, "type": "file", "size": size,
                                "mtime_ns": max(0, st.st_mtime_ns), "chunks": chunks})
            else:
                warnings.append(f"skipped unsupported item (not a regular file or folder): {rel}")

    walk(root, "")
    raw = {
        "format": manifest_mod.FORMAT,
        "root_name": os.path.basename(root) or "root",
        "chunker": {"algo": "fixed", "size": chunk_size},
        "entries": entries,
    }
    return ScanResult(manifest_mod.validate(raw), locations, warnings)
