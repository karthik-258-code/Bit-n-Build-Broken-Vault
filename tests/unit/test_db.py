import io
import json
import sqlite3

import pytest

from brokenvault.common.errors import BVError
from brokenvault.server.chunk_store import ChunkStore
from brokenvault.server.db import Database
from brokenvault.server.upload_service import UploadService
from brokenvault.server.version_service import VersionService
from tests.helpers.vault import make_manifest

INSERT_UPLOAD = ("INSERT INTO uploads (upload_id, manifest_id, manifest_json, state, total_bytes, created_at)"
                 " VALUES (?, ?, '{}', ?, 0, 0)")


@pytest.fixture
def services(tmp_path):
    db = Database(str(tmp_path / "meta.db"))
    store = ChunkStore(str(tmp_path))
    yield db, store, UploadService(db, store), VersionService(db)
    db.close()


def test_tx_rolls_back_on_error(services):
    db = services[0]
    with pytest.raises(RuntimeError):
        with db.tx() as conn:
            conn.execute(INSERT_UPLOAD, ("u1", "m1", "OPEN"))
            raise RuntimeError("injected")
    assert db.query("SELECT * FROM uploads") == []


def test_one_open_upload_per_manifest(services):
    db = services[0]
    with db.tx() as conn:
        conn.execute(INSERT_UPLOAD, ("u1", "m1", "OPEN"))
        conn.execute(INSERT_UPLOAD, ("u2", "m1", "COMMITTED"))  # closed uploads may share the manifest
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx() as conn:
            conn.execute(INSERT_UPLOAD, ("u3", "m1", "OPEN"))


def test_commit_rolls_back_when_transaction_fails(services, monkeypatch):
    """A failure inside the commit transaction leaves no version rows and the upload OPEN."""
    db, store, uploads, versions = services
    data = b"0123456789abcdef"
    manifest = make_manifest({"f.bin": data}, chunk_size=8)
    upload_id = uploads.create_upload(json.dumps(manifest))["upload_id"]
    for chunk in manifest["entries"][0]["chunks"]:
        body = data[:8] if chunk is manifest["entries"][0]["chunks"][0] else data[8:]
        assert uploads.put_chunk(upload_id, chunk["id"], 8, io.BytesIO(body)) is True

    def boom(conn_or_db, version_id):
        if conn_or_db is not db:
            raise RuntimeError("injected failure inside the commit transaction")

    monkeypatch.setattr(uploads, "_committed_summary", boom)
    with pytest.raises(RuntimeError):
        uploads.commit(upload_id)
    monkeypatch.undo()

    assert versions.list_versions() == []
    assert db.query("SELECT * FROM entries") == [] and db.query("SELECT * FROM entry_chunks") == []
    assert uploads.status(upload_id)["state"] == "OPEN"

    done = uploads.commit(upload_id)  # retry succeeds
    assert done["version_id"] == "V1" and done["uploaded_bytes"] == 16 and done["reused_bytes"] == 0
    assert uploads.commit(upload_id) == done  # idempotent


def test_create_upload_is_idempotent_while_open(services):
    _, _, uploads, _ = services
    raw = json.dumps(make_manifest({"f.bin": b""}))
    first = uploads.create_upload(raw)
    second = uploads.create_upload(raw)
    assert first["resumed"] is False and second["resumed"] is True
    assert first["upload_id"] == second["upload_id"]
    uploads.commit(first["upload_id"])
    third = uploads.create_upload(raw)  # after commit a new upload starts
    assert third["resumed"] is False and third["upload_id"] != first["upload_id"]


def test_unknown_and_closed_upload(services):
    _, _, uploads, _ = services
    with pytest.raises(BVError) as exc:
        uploads.missing("nope")
    assert exc.value.code == "UNKNOWN_UPLOAD"
    upload_id = uploads.create_upload(json.dumps(make_manifest({"f.bin": b""})))["upload_id"]
    uploads.commit(upload_id)
    with pytest.raises(BVError) as exc:
        uploads.missing(upload_id)
    assert exc.value.code == "UPLOAD_CLOSED"
