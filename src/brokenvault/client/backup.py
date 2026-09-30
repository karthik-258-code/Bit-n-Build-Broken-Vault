"""BackupRunner: scan, plan, transfer, commit, with resume by upload ID (LLD 6.3)."""

import json
import os

from ..common import manifest as manifest_mod
from ..common.errors import BVError
from ..common.hashing import sha256_bytes
from .chunker import read_chunk
from .scanner import scan


class BackupStopped(Exception):
    """The backup stopped before commit. The upload stays OPEN on the server and can be resumed."""

    def __init__(self, reason, upload_id, chunks_stored, chunks_needed, cause=None):
        super().__init__(reason)
        self.reason = reason  # "interrupt" or "network"
        self.upload_id = upload_id
        self.chunks_stored = chunks_stored
        self.chunks_needed = chunks_needed
        self.cause = cause


def default_state_dir():
    return os.environ.get("BV_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".brokenvault", "uploads")


class StateFile:
    """Remembers the open upload ID for one (folder, server) pair across client restarts."""

    def __init__(self, folder, server_url, state_dir=None):
        self.folder = os.path.abspath(folder)
        self.server = server_url
        key = sha256_bytes(f"{self.folder}\n{server_url}".encode("utf-8"))
        self.path = os.path.join(state_dir or default_state_dir(), key + ".json")

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f).get("upload_id")
        except (OSError, ValueError, AttributeError):
            return None

    def save(self, upload_id, manifest_id):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"upload_id": upload_id, "server": self.server, "folder": self.folder,
                       "manifest_id": manifest_id}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def clear(self):
        try:
            os.remove(self.path)
        except OSError:
            pass


class BackupRunner:
    def __init__(self, transport, folder, chunk_size, resume_id=None, stop_after_chunks=None,
                 state_dir=None, progress=None, notice=None):
        self.transport = transport
        self.folder = folder
        self.chunk_size = chunk_size
        self.resume_id = resume_id
        self.stop_after_chunks = stop_after_chunks
        self.state = StateFile(folder, transport.base_url, state_dir)
        self.progress = progress or (lambda *a: None)
        self.notice = notice or (lambda msg: None)

    def _find_upload(self, manifest_id):
        """Return the stored or named upload ID if it is still OPEN for this exact file list."""
        explicit = self.resume_id is not None
        upload_id = self.resume_id or self.state.load()
        if not upload_id:
            return None
        try:
            status = self.transport.get_upload(upload_id)
        except BVError as exc:
            if exc.code != "UNKNOWN_UPLOAD" or explicit:
                raise
            status = None
        if status and status["state"] == "OPEN" and status["manifest_id"] == manifest_id:
            return upload_id
        if explicit:
            if status["state"] != "OPEN":
                raise BVError("UPLOAD_CLOSED", f"upload {upload_id} is {status['state']}; it cannot be resumed",
                              {"upload_id": upload_id, "state": status["state"]})
            raise BVError("SOURCE_CHANGED",
                          f"folder {self.folder!r} no longer matches upload {upload_id} (the file list changed)",
                          {"upload_id": upload_id})
        self.state.clear()  # stale: folder changed, or the upload was already committed
        return None

    def run(self):
        scanned = scan(self.folder, self.chunk_size)
        for warning in scanned.warnings:
            self.notice(f"warning: {warning}")
        manifest = scanned.manifest
        manifest_id = manifest_mod.manifest_id(manifest)

        upload_id = self._find_upload(manifest_id)
        resumed = upload_id is not None
        if upload_id is None:
            created = self.transport.create_upload(manifest_mod.canonical_json(manifest).encode("utf-8"))
            upload_id, resumed = created["upload_id"], created["resumed"]
        self.state.save(upload_id, manifest_id)  # written before any chunk is sent
        self.notice(f"Upload   : {upload_id}  ({'resumed' if resumed else 'new'})")

        sent = {"chunks": 0, "bytes": 0}
        plan = {"unique_total": 0, "present": 0}
        try:
            self._send_missing(upload_id, scanned.locations, sent, plan)
            try:
                result = self.transport.commit(upload_id)
            except BVError as exc:
                if exc.code != "INCOMPLETE_UPLOAD":
                    raise
                self._send_missing(upload_id, scanned.locations, sent, plan)  # refresh once, then retry
                result = self.transport.commit(upload_id)
        except KeyboardInterrupt:
            raise BackupStopped("interrupt", upload_id, plan["present"], plan["unique_total"]) from None
        except BVError as exc:
            if exc.code in ("SERVER_UNAVAILABLE", "INTERNAL", "INCOMPLETE_UPLOAD"):
                raise BackupStopped("network", upload_id, plan["present"], plan["unique_total"], exc) from None
            if exc.code == "CORRUPT_CHUNK":
                chunks = exc.details.get("chunks", [])
                raise BVError("CORRUPT_CHUNK",
                              f"backup of {self.folder!r} cannot complete: stored chunk(s) "
                              f"{', '.join(chunks) or '?'} on the server are damaged. Upload {upload_id} stays "
                              f"UNFINISHED and no version was added",
                              dict(exc.details, upload_id=upload_id)) from None
            raise

        self.state.clear()
        result.update({"upload_id": upload_id, "resumed": resumed, "chunks_sent": sent["chunks"],
                       "bytes_sent": sent["bytes"], "unique_chunks": plan["unique_total"]})
        return result

    def _send_missing(self, upload_id, locations, sent, plan):
        listing = self.transport.missing(upload_id)
        missing = listing["missing"]
        plan["unique_total"], plan["present"] = listing["unique_total"], listing["present"]
        need_bytes = sum(c["size"] for c in missing)
        done_chunks = done_bytes = 0
        self.progress(0, len(missing), 0, need_bytes)
        for chunk in missing:
            if self.stop_after_chunks is not None and sent["chunks"] >= self.stop_after_chunks:
                raise KeyboardInterrupt  # test hook: simulated interrupt
            cid = chunk["id"]
            path, offset, length = locations[cid]
            try:
                data = read_chunk(path, offset, length)
            except OSError as exc:
                raise BVError("SOURCE_CHANGED", f"cannot re-read {path!r}: {exc}", {"path": path}) from None
            if len(data) != chunk["size"] or sha256_bytes(data) != cid:
                raise BVError("SOURCE_CHANGED", f"file {path!r} changed while the backup was running",
                              {"path": path, "chunk": cid})
            self.transport.put_chunk(upload_id, cid, data)
            sent["chunks"] += 1
            sent["bytes"] += length
            plan["present"] += 1
            done_chunks += 1
            done_bytes += length
            self.progress(done_chunks, len(missing), done_bytes, need_bytes)
