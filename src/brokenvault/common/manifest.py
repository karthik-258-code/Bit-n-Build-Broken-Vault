"""Manifest validation, canonical form and manifest ID (LLD 4.3).

A manifest is a plain dict. validate() returns a clean copy with only the
known keys and entries sorted by UTF-8 path, so the canonical JSON and the
manifest ID do not depend on the order the client used.
"""

import json
from dataclasses import dataclass

from .constants import HASH_RE, MAX_CHUNK_SIZE, MAX_SEGMENT_BYTES
from .errors import BVError
from .hashing import sha256_bytes
from .paths import parent_paths, validate_rel_path

FORMAT = 1


@dataclass(frozen=True)
class ManifestInfo:
    """Values the server derives from a manifest; never taken from the client."""

    manifest_id: str
    total_bytes: int
    file_count: int
    dir_count: int
    unique_chunks: tuple  # ordered distinct (chunk_id, size)


def _fail(message, **details):
    return BVError("INVALID_MANIFEST", message, details)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_file(entry, path, chunk_size, chunk_sizes):
    size = entry.get("size")
    if not _is_int(size) or size < 0:
        raise _fail(f"file {path!r}: size must be a non-negative integer", path=path)
    chunks = entry.get("chunks")
    if not isinstance(chunks, list):
        raise _fail(f"file {path!r}: chunks must be a list", path=path)
    clean = []
    total = 0
    for idx, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            raise _fail(f"file {path!r}: chunk {idx} must be an object", path=path)
        cid, csize = chunk.get("id"), chunk.get("size")
        if not isinstance(cid, str) or not HASH_RE.fullmatch(cid):
            raise _fail(f"file {path!r}: chunk {idx} id is not lowercase SHA-256 hex", path=path)
        if not _is_int(csize) or not 1 <= csize <= chunk_size:
            raise _fail(f"file {path!r}: chunk {idx} size must be 1..{chunk_size}", path=path)
        if idx < len(chunks) - 1 and csize != chunk_size:
            raise _fail(f"file {path!r}: chunk {idx} must be {chunk_size} bytes (only the last may be short)", path=path)
        if chunk_sizes.setdefault(cid, csize) != csize:
            raise _fail(f"chunk {cid} is declared with two different sizes", chunk=cid)
        total += csize
        clean.append({"id": cid, "size": csize})
    if total != size:
        raise _fail(f"file {path!r}: chunk sizes add up to {total}, not the file size {size}", path=path)
    return size, clean


def validate(obj):
    """Validate a parsed manifest. Return the canonical dict or raise BVError."""
    if not isinstance(obj, dict):
        raise _fail("manifest must be a JSON object")
    if not _is_int(obj.get("format")) or obj["format"] != FORMAT:
        raise _fail(f"unsupported manifest format {obj.get('format')!r}; expected {FORMAT}")
    root_name = obj.get("root_name")
    if (not isinstance(root_name, str) or not root_name or "\x00" in root_name
            or len(root_name.encode("utf-8", "replace")) > MAX_SEGMENT_BYTES):
        raise _fail("root_name must be a non-empty string of at most 255 bytes")
    chunker = obj.get("chunker")
    if not isinstance(chunker, dict) or chunker.get("algo") != "fixed":
        raise _fail("chunker.algo must be 'fixed'")
    chunk_size = chunker.get("size")
    if not _is_int(chunk_size) or not 0 < chunk_size <= MAX_CHUNK_SIZE:
        raise _fail(f"chunker.size must be 1..{MAX_CHUNK_SIZE}")
    entries = obj.get("entries")
    if not isinstance(entries, list):
        raise _fail("entries must be a list")

    types = {}  # path -> type
    chunk_sizes = {}  # chunk id -> size
    clean = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise _fail("every entry must be an object")
        path = validate_rel_path(entry.get("path"))
        if path in types:
            raise BVError("INVALID_PATH", f"duplicate path {path!r}", {"path": path})
        etype = entry.get("type")
        mtime_ns = entry.get("mtime_ns")
        if not _is_int(mtime_ns) or mtime_ns < 0:
            raise _fail(f"{path!r}: mtime_ns must be a non-negative integer", path=path)
        if etype == "dir":
            if "size" in entry or "chunks" in entry:
                raise _fail(f"dir {path!r} must not have size or chunks", path=path)
            clean.append({"path": path, "type": "dir", "mtime_ns": mtime_ns})
        elif etype == "file":
            size, chunks = _validate_file(entry, path, chunk_size, chunk_sizes)
            clean.append({"path": path, "type": "file", "size": size, "mtime_ns": mtime_ns, "chunks": chunks})
        else:
            raise _fail(f"{path!r}: type must be 'file' or 'dir'", path=path)
        types[path] = etype

    for path in types:
        for parent in parent_paths(path):
            if types.get(parent) != "dir":
                raise _fail(f"{path!r}: parent {parent!r} is not a dir entry in the manifest", path=path)

    clean.sort(key=lambda e: e["path"].encode("utf-8"))
    return {
        "format": FORMAT,
        "root_name": root_name,
        "chunker": {"algo": "fixed", "size": chunk_size},
        "entries": clean,
    }


def canonical_json(manifest):
    """Canonical JSON text of a validated manifest."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def manifest_id(manifest):
    return sha256_bytes(canonical_json(manifest).encode("utf-8"))


def unique_chunks(manifest):
    """Ordered distinct (chunk_id, size) across all files, first occurrence first."""
    seen = {}
    for entry in manifest["entries"]:
        if entry["type"] == "file":
            for chunk in entry["chunks"]:
                seen.setdefault(chunk["id"], chunk["size"])
    return tuple(seen.items())


def describe(manifest):
    """Derived values for a validated manifest."""
    files = [e for e in manifest["entries"] if e["type"] == "file"]
    return ManifestInfo(
        manifest_id=manifest_id(manifest),
        total_bytes=sum(e["size"] for e in files),
        file_count=len(files),
        dir_count=len(manifest["entries"]) - len(files),
        unique_chunks=unique_chunks(manifest),
    )


def parse(raw):
    """Parse manifest bytes/str and validate. Raises INVALID_MANIFEST on bad JSON."""
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8")
        obj = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _fail(f"manifest is not valid UTF-8 JSON: {exc}") from None
    return validate(obj)
