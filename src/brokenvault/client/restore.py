"""Restorer: rebuild a completed version in an empty folder, checking every chunk (LLD 6.5)."""

import os
from collections import OrderedDict

from ..common import manifest as manifest_mod
from ..common.errors import BVError
from ..common.hashing import sha256_bytes
from ..common.paths import safe_join

CACHE_BYTES = 32 * 1024 * 1024


class _ChunkCache:
    """Small LRU so a chunk repeated within a restore is fetched once."""

    def __init__(self, limit=CACHE_BYTES):
        self._items = OrderedDict()
        self._limit = limit
        self._size = 0

    def get(self, cid):
        data = self._items.get(cid)
        if data is not None:
            self._items.move_to_end(cid)
        return data

    def put(self, cid, data):
        self._items[cid] = data
        self._size += len(data)
        while self._size > self._limit and len(self._items) > 1:
            _, old = self._items.popitem(last=False)
            self._size -= len(old)


def _check_destination(dest):
    if os.path.exists(dest):
        if not os.path.isdir(dest):
            raise BVError("DEST_NOT_EMPTY", f"restore destination {dest!r} exists and is not a folder",
                          {"dest": dest})
        if os.listdir(dest):
            raise BVError("DEST_NOT_EMPTY", f"restore destination {dest!r} is not empty", {"dest": dest})
    os.makedirs(dest, exist_ok=True)


def restore(transport, version, dest):
    info = transport.get_version(version)  # VERSION_NOT_FOUND for unfinished or unknown IDs
    manifest = manifest_mod.validate(info)  # never trust paths from the network
    dest = os.path.abspath(dest)
    _check_destination(dest)

    dirs = [e for e in manifest["entries"] if e["type"] == "dir"]
    files = [e for e in manifest["entries"] if e["type"] == "file"]
    cache = _ChunkCache()
    restored_bytes = 0
    try:
        for entry in dirs:  # sorted by path, so parents come first
            os.makedirs(safe_join(dest, entry["path"]), exist_ok=True)
        for entry in files:
            target = safe_join(dest, entry["path"])
            with open(target, "wb") as f:
                for chunk in entry["chunks"]:
                    f.write(_fetch(transport, cache, chunk, entry["path"], info["version_id"]))
            actual = os.path.getsize(target)
            if actual != entry["size"]:
                raise BVError("RESTORE_SIZE_MISMATCH",
                              f"restored file {entry['path']!r} is {actual} bytes, the version says {entry['size']}",
                              {"path": entry["path"], "version": info["version_id"]})
            os.utime(target, ns=(entry["mtime_ns"], entry["mtime_ns"]))
            restored_bytes += actual
    finally:
        # Directory mtimes last, deepest first: writing a child changes its parent's mtime.
        for entry in sorted(dirs, key=lambda e: e["path"].count("/"), reverse=True):
            try:
                os.utime(safe_join(dest, entry["path"]), ns=(entry["mtime_ns"], entry["mtime_ns"]))
            except (OSError, BVError):
                pass

    return {"version_id": info["version_id"], "result": "OK", "dest": dest, "files_restored": len(files),
            "dirs_restored": len(dirs), "bytes_restored": restored_bytes}


def _fetch(transport, cache, chunk, path, version_id):
    cid = chunk["id"]
    data = cache.get(cid)
    if data is not None:
        return data
    try:
        data = transport.get_chunk(cid)
    except BVError as exc:
        if exc.code != "CHUNK_MISSING":
            raise
        raise BVError("CHUNK_MISSING",
                      f"restore of {version_id} failed at file {path!r}: chunk {cid} is missing on the server",
                      {"path": path, "chunk": cid, "version": version_id}) from None
    if len(data) != chunk["size"] or sha256_bytes(data) != cid:
        raise BVError("RESTORE_CHUNK_INVALID",
                      f"restore of {version_id} failed at file {path!r}: chunk {cid} is damaged on the server "
                      f"(hash or size mismatch)",
                      {"path": path, "chunk": cid, "version": version_id})
    cache.put(cid, data)
    return data
