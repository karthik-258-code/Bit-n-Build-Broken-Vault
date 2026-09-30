"""Upload lifecycle: create, missing, put_chunk, commit, status (LLD 5.5)."""

import json
import threading
import time
import uuid

from ..common import manifest as manifest_mod
from ..common.errors import BVError
from ..common.hashing import sha256_file
from .faults import Faults


def version_label(version_id):
    return f"V{version_id}"


def version_summary(row):
    """Public summary of a versions row."""
    return {
        "version_id": version_label(row["version_id"]),
        "state": "COMPLETE",
        "created_at": row["created_at"],
        "root_name": row["root_name"],
        "file_count": row["file_count"],
        "dir_count": row["dir_count"],
        "total_bytes": row["total_bytes"],
        "uploaded_bytes": row["uploaded_bytes"],
        "reused_bytes": row["reused_bytes"],
    }


class UploadService:
    def __init__(self, db, store, faults=None, fast_commit=False):
        self.db = db
        self.store = store
        self.faults = faults or Faults()
        self.fast_commit = fast_commit
        self._cache = {}  # upload_id -> (manifest, ordered unique chunks, {chunk_id: size})
        self._cache_lock = threading.Lock()

    # -- helpers ---------------------------------------------------------

    def _row(self, upload_id):
        row = self.db.query_one("SELECT * FROM uploads WHERE upload_id = ?", (upload_id,))
        if row is None:
            raise BVError("UNKNOWN_UPLOAD", f"no upload with ID {upload_id!r}", {"upload_id": upload_id})
        return row

    def _open_row(self, upload_id):
        row = self._row(upload_id)
        if row["state"] != "OPEN":
            raise BVError("UPLOAD_CLOSED", f"upload {upload_id} is {row['state']}, not OPEN",
                          {"upload_id": upload_id, "state": row["state"]})
        return row

    def _manifest(self, row):
        """Parsed manifest of an upload (immutable, so cached per upload ID)."""
        with self._cache_lock:
            hit = self._cache.get(row["upload_id"])
            if hit is None:
                m = json.loads(row["manifest_json"])
                unique = manifest_mod.unique_chunks(m)
                hit = (m, unique, dict(unique))
                if len(self._cache) >= 8:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[row["upload_id"]] = hit
            return hit

    def _uploaded_bytes(self, conn_or_db, upload_id):
        sql = "SELECT COALESCE(SUM(size), 0) AS n FROM upload_chunks WHERE upload_id = ?"
        if conn_or_db is self.db:
            return self.db.query_one(sql, (upload_id,))["n"]
        return conn_or_db.execute(sql, (upload_id,)).fetchone()["n"]

    # -- operations ------------------------------------------------------

    def create_upload(self, raw_manifest):
        m = manifest_mod.parse(raw_manifest)
        info = manifest_mod.describe(m)
        with self.db.tx() as conn:
            row = conn.execute("SELECT upload_id FROM uploads WHERE manifest_id = ? AND state = 'OPEN'",
                               (info.manifest_id,)).fetchone()
            if row:
                upload_id, resumed = row["upload_id"], True
            else:
                upload_id, resumed = uuid.uuid4().hex, False
                conn.execute(
                    "INSERT INTO uploads (upload_id, manifest_id, manifest_json, state, total_bytes, created_at)"
                    " VALUES (?, ?, ?, 'OPEN', ?, ?)",
                    (upload_id, info.manifest_id, manifest_mod.canonical_json(m), info.total_bytes,
                     int(time.time())))
        return {"upload_id": upload_id, "resumed": resumed, "state": "OPEN",
                "total_bytes": info.total_bytes, "manifest_id": info.manifest_id}

    def status(self, upload_id):
        row = self._row(upload_id)
        _, unique, _ = self._manifest(row)
        out = {
            "upload_id": upload_id,
            "state": row["state"],
            "total_bytes": row["total_bytes"],
            "uploaded_bytes": self._uploaded_bytes(self.db, upload_id),
            "manifest_id": row["manifest_id"],
            "chunks_needed": len(unique),
            "chunks_stored": sum(1 for cid, _ in unique if self.store.exists(cid)),
        }
        if row["version_id"] is not None:
            out["version_id"] = version_label(row["version_id"])
        return out

    def missing(self, upload_id):
        row = self._open_row(upload_id)
        _, unique, _ = self._manifest(row)
        missing = [{"id": cid, "size": size} for cid, size in unique if not self.store.exists(cid)]
        return {"missing": missing, "unique_total": len(unique), "present": len(unique) - len(missing)}

    def put_chunk(self, upload_id, chunk_id, length, stream):
        """Returns True when the chunk was newly stored, False when it was already present."""
        row = self._open_row(upload_id)
        _, _, sizes = self._manifest(row)
        size = sizes.get(chunk_id)
        if size is None:
            raise BVError("CHUNK_NOT_IN_MANIFEST", f"chunk {chunk_id} is not part of upload {upload_id}",
                          {"chunk": chunk_id, "upload_id": upload_id})
        if length != size:
            raise BVError("CHUNK_SIZE_MISMATCH",
                          f"chunk {chunk_id}: body is {length} bytes, the manifest says {size}",
                          {"chunk": chunk_id, "expected": size, "actual": length})
        if self.store.exists(chunk_id):
            return False  # nothing counted; the existing file is never overwritten
        tmp = self.store.stage(chunk_id, stream, length)
        try:
            # Intent row is committed BEFORE the rename, so the bytes are counted exactly once
            # whatever the crash point (LLD 5.5, 5.10).
            with self.db.tx() as conn:
                conn.execute("INSERT OR IGNORE INTO upload_chunks (upload_id, chunk_id, size) VALUES (?, ?, ?)",
                             (upload_id, chunk_id, size))
        except BaseException:
            self.store.discard(tmp)
            raise
        self.faults.hit("crash_before_rename")
        self.store.publish(chunk_id, tmp)
        self.faults.chunk_published()
        return True

    def commit(self, upload_id):
        row = self._row(upload_id)
        if row["state"] == "COMMITTED":
            return self._committed_summary(self.db, row["version_id"])
        if row["state"] != "OPEN":
            raise BVError("UPLOAD_CLOSED", f"upload {upload_id} is {row['state']}", {"upload_id": upload_id})

        m, unique, _ = self._manifest(row)
        trusted = set()
        if self.fast_commit:
            trusted = {r["chunk_id"] for r in self.db.query(
                "SELECT chunk_id FROM upload_chunks WHERE upload_id = ?", (upload_id,))}
        missing, corrupt = [], []
        for cid, size in unique:
            actual = self.store.size(cid)
            if actual is None:
                missing.append(cid)
            elif actual != size or (cid not in trusted and sha256_file(self.store.path(cid)) != cid):
                corrupt.append(cid)
        if missing:
            raise BVError("INCOMPLETE_UPLOAD",
                          f"upload {upload_id} still lacks {len(missing)} of {len(unique)} chunks",
                          {"upload_id": upload_id, "missing": missing})
        if corrupt:
            raise BVError("CORRUPT_CHUNK",
                          f"{len(corrupt)} stored chunk(s) needed by upload {upload_id} are damaged: "
                          + ", ".join(corrupt[:5]) + (" ..." if len(corrupt) > 5 else ""),
                          {"upload_id": upload_id, "chunks": corrupt})

        self.faults.hit("crash_before_commit_tx")
        info = manifest_mod.describe(m)
        with self.db.tx() as conn:
            row = conn.execute("SELECT * FROM uploads WHERE upload_id = ?", (upload_id,)).fetchone()
            if row["state"] == "COMMITTED":
                return self._committed_summary(conn, row["version_id"])
            if row["state"] != "OPEN":
                raise BVError("UPLOAD_CLOSED", f"upload {upload_id} is {row['state']}", {"upload_id": upload_id})
            uploaded = self._uploaded_bytes(conn, upload_id)
            reused = max(0, info.total_bytes - uploaded)
            cur = conn.execute(
                "INSERT INTO versions (upload_id, created_at, root_name, chunker_size, total_bytes,"
                " uploaded_bytes, reused_bytes, file_count, dir_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (upload_id, int(time.time()), m["root_name"], m["chunker"]["size"], info.total_bytes,
                 uploaded, reused, info.file_count, info.dir_count))
            vid = cur.lastrowid
            conn.executemany(
                "INSERT INTO entries (version_id, path, type, size, mtime_ns) VALUES (?, ?, ?, ?, ?)",
                ((vid, e["path"], e["type"], e.get("size"), e["mtime_ns"]) for e in m["entries"]))
            conn.executemany(
                "INSERT INTO entry_chunks (version_id, path, idx, chunk_id, size) VALUES (?, ?, ?, ?, ?)",
                ((vid, e["path"], idx, c["id"], c["size"])
                 for e in m["entries"] if e["type"] == "file"
                 for idx, c in enumerate(e["chunks"])))
            conn.execute("UPDATE uploads SET state = 'COMMITTED', version_id = ? WHERE upload_id = ?",
                         (vid, upload_id))
            summary = self._committed_summary(conn, vid)
        self.faults.hit("crash_after_commit_tx")
        return summary

    def _committed_summary(self, conn_or_db, version_id):
        sql = "SELECT * FROM versions WHERE version_id = ?"
        if conn_or_db is self.db:
            row = self.db.query_one(sql, (version_id,))
        else:
            row = conn_or_db.execute(sql, (version_id,)).fetchone()
        return version_summary(row)
