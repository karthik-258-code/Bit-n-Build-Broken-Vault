"""Damage scan over every chunk used by a completed version (LLD 5.7). Never repairs."""

from ..common.hashing import sha256_file
from .upload_service import version_label


class VerifyService:
    def __init__(self, db, store):
        self.db = db
        self.store = store

    def verify(self):
        version_ids = [r["version_id"] for r in self.db.query("SELECT version_id FROM versions ORDER BY version_id")]
        refs = self.db.query("SELECT chunk_id, MIN(size) AS size FROM entry_chunks GROUP BY chunk_id ORDER BY chunk_id")

        damaged = []
        for ref in refs:
            cid, size = ref["chunk_id"], ref["size"]
            actual = self.store.size(cid)
            if actual is None:
                reason, detail = "MISSING", "chunk file is absent"
            elif actual != size:
                reason, detail = "CORRUPT", f"size mismatch (expected {size}, found {actual})"
            elif sha256_file(self.store.path(cid)) != cid:
                reason, detail = "CORRUPT", "hash mismatch"
            else:
                continue
            damaged.append({"id": cid, "reason": reason, "detail": detail, "size": size, "affected": []})

        impact = {vid: {} for vid in version_ids}  # version_id -> {path: [chunk ids]}
        for item in damaged:
            rows = self.db.query(
                "SELECT DISTINCT version_id, path FROM entry_chunks WHERE chunk_id = ? ORDER BY version_id, path",
                (item["id"],))
            for r in rows:
                item["affected"].append({"version": version_label(r["version_id"]), "path": r["path"]})
                impact[r["version_id"]].setdefault(r["path"], []).append(item["id"])

        versions = []
        for vid in version_ids:
            files = [{"path": path, "chunks": ids} for path, ids in sorted(impact[vid].items())]
            versions.append({"version": version_label(vid), "status": "DAMAGED" if files else "OK", "files": files})
        return {
            "checked_versions": len(version_ids),
            "checked_chunks": len(refs),
            "healthy": not damaged,
            "damaged_chunks": damaged,
            "versions": versions,
            "repaired": 0,
        }
