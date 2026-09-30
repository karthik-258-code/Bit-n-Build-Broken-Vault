"""Raw HTTP tests of the server API: T4, T6, T9, T10, T17 and protocol edge cases."""

import http.client
import socket

import pytest

from tests.helpers.vault import make_manifest, sha

A, B = b"AAAAAAAA", b"BBBBBBBB"


def open_upload(vault, files=None, **kw):
    manifest = make_manifest(files or {"f.bin": A + B}, **kw)
    status, body = vault.http("POST", "/v1/uploads", manifest)
    assert status == 200, body
    return body["upload_id"], manifest


def put(vault, upload_id, chunk_id, data):
    return vault.http("PUT", f"/v1/chunks/{chunk_id}?upload_id={upload_id}", data)


def test_health_and_routing(vault):
    assert vault.http("GET", "/v1/health") == (200, {"ok": True})
    status, body = vault.http("GET", "/v1/nope")
    assert status == 404 and body["error"]["code"] == "NOT_FOUND"
    status, body = vault.http("DELETE", "/v1/versions")
    assert status == 405 and body["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_create_upload_and_missing(vault):
    upload_id, _ = open_upload(vault)
    status, body = vault.http("GET", f"/v1/uploads/{upload_id}/missing")
    assert status == 200
    assert body == {"missing": [{"id": sha(A), "size": 8}, {"id": sha(B), "size": 8}],
                    "unique_total": 2, "present": 0}
    assert put(vault, upload_id, sha(A), A) == (201, {"stored": True})
    status, body = vault.http("GET", f"/v1/uploads/{upload_id}/missing")
    assert body["missing"] == [{"id": sha(B), "size": 8}] and body["present"] == 1
    status, body = vault.http("GET", f"/v1/uploads/{upload_id}")
    assert body["state"] == "OPEN" and body["uploaded_bytes"] == 8 and body["total_bytes"] == 16
    assert vault.http("GET", "/v1/uploads/doesnotexist")[0] == 404


def test_t4_wrong_bytes_rejected_and_not_stored(vault):
    """T4 / AC-2.3"""
    upload_id, _ = open_upload(vault)
    status, body = put(vault, upload_id, sha(A), b"XXXXXXXX")
    assert status == 400 and body["error"]["code"] == "CHUNK_HASH_MISMATCH"
    assert vault.chunk_files() == {}
    assert vault.http("GET", f"/v1/uploads/{upload_id}")[1]["uploaded_bytes"] == 0
    assert vault.http("GET", f"/v1/chunks/{sha(A)}")[0] == 404


def test_put_chunk_errors(vault):
    upload_id, _ = open_upload(vault)
    status, body = put(vault, upload_id, sha(A), A + b"x")
    assert status == 400 and body["error"]["code"] == "CHUNK_SIZE_MISMATCH"
    status, body = put(vault, upload_id, sha(b"other"), b"other")
    assert status == 422 and body["error"]["code"] == "CHUNK_NOT_IN_MANIFEST"
    status, body = put(vault, "nosuchupload", sha(A), A)
    assert status == 404 and body["error"]["code"] == "UNKNOWN_UPLOAD"
    status, body = vault.http("PUT", f"/v1/chunks/{sha(A)}", A)
    assert status == 400 and body["error"]["code"] == "BAD_REQUEST"
    assert vault.chunk_files() == {}


def test_keep_alive_survives_rejected_body(vault):
    """LLD 5.8: an error sent before the body is read must not poison the connection."""
    upload_id, _ = open_upload(vault)
    conn = http.client.HTTPConnection("127.0.0.1", vault.port, timeout=30)
    try:
        conn.request("PUT", f"/v1/chunks/{sha(A)}?upload_id=nosuchupload", body=b"Z" * 100_000)
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 404
        conn.request("PUT", f"/v1/chunks/{sha(A)}?upload_id={upload_id}", body=A)  # same connection
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 201
        conn.request("GET", "/v1/health")
        resp = conn.getresponse()
        assert resp.status == 200 and resp.read() == b'{"ok": true}'
    finally:
        conn.close()


def _raw(vault, request_head):
    with socket.create_connection(("127.0.0.1", vault.port), timeout=10) as s:
        s.sendall(request_head)
        data = b""
        while True:
            block = s.recv(65536)
            if not block:
                break
            data += block
    return data


def test_length_required_and_too_large(vault):
    upload_id, _ = open_upload(vault)
    target = f"/v1/chunks/{sha(A)}?upload_id={upload_id}".encode()
    reply = _raw(vault, b"PUT " + target + b" HTTP/1.1\r\nHost: x\r\n\r\n")
    assert reply.startswith(b"HTTP/1.1 411") and b"Connection: close" in reply
    reply = _raw(vault, b"PUT " + target + b" HTTP/1.1\r\nHost: x\r\nContent-Length: 99999999\r\n\r\n")
    assert reply.startswith(b"HTTP/1.1 413") and b"Connection: close" in reply
    assert vault.chunk_files() == {}


def test_t9_same_chunk_twice_counted_once(vault):
    """T9 / AC-4.3"""
    upload_id, _ = open_upload(vault)
    assert put(vault, upload_id, sha(A), A) == (201, {"stored": True})
    assert put(vault, upload_id, sha(A), A) == (200, {"stored": False})
    assert list(vault.chunk_files()) == [sha(A)]
    assert vault.http("GET", f"/v1/uploads/{upload_id}")[1]["uploaded_bytes"] == 8
    assert vault.http("GET", f"/v1/chunks/{sha(A)}") == (200, A)


def test_identical_chunks_in_one_version_stored_once(vault):
    """FR-2.4: two identical files need each chunk once."""
    upload_id, _ = open_upload(vault, {"a.bin": A + B, "b.bin": A + B, "c.bin": A})
    status, body = vault.http("GET", f"/v1/uploads/{upload_id}/missing")
    assert body["unique_total"] == 2 and len(body["missing"]) == 2
    put(vault, upload_id, sha(A), A)
    put(vault, upload_id, sha(B), B)
    status, body = vault.http("POST", f"/v1/uploads/{upload_id}/commit")
    assert status == 200
    assert body["total_bytes"] == 40 and body["uploaded_bytes"] == 16 and body["reused_bytes"] == 24
    assert len(vault.chunk_files()) == 2


def test_create_upload_idempotent(vault):
    first, manifest = open_upload(vault)
    status, body = vault.http("POST", "/v1/uploads", manifest)
    assert body["upload_id"] == first and body["resumed"] is True


def test_t6_commit_with_missing_chunk_refused(vault):
    """T6 / AC-3.3"""
    upload_id, _ = open_upload(vault)
    put(vault, upload_id, sha(A), A)
    status, body = vault.http("POST", f"/v1/uploads/{upload_id}/commit")
    assert status == 409 and body["error"]["code"] == "INCOMPLETE_UPLOAD"
    assert body["error"]["details"]["missing"] == [sha(B)]
    assert vault.versions() == []
    assert vault.http("GET", f"/v1/uploads/{upload_id}")[1]["state"] == "OPEN"  # still resumable
    # an unfinished upload is not a version
    status, body = vault.http("GET", f"/v1/versions/{upload_id}")
    assert status == 404 and body["error"]["code"] == "VERSION_NOT_FOUND"


def test_t10_commit_twice_same_version(vault):
    """T10 / AC-4.4"""
    upload_id, _ = open_upload(vault)
    put(vault, upload_id, sha(A), A)
    put(vault, upload_id, sha(B), B)
    first = vault.http("POST", f"/v1/uploads/{upload_id}/commit")
    second = vault.http("POST", f"/v1/uploads/{upload_id}/commit")
    assert first[0] == second[0] == 200 and first[1] == second[1]
    assert first[1]["version_id"] == "V1" and first[1]["state"] == "COMPLETE"
    assert [v["version_id"] for v in vault.versions()] == ["V1"]
    # the closed upload accepts no more chunks
    status, body = put(vault, upload_id, sha(A), A)
    assert status == 409 and body["error"]["code"] == "UPLOAD_CLOSED"
    status, body = vault.http("GET", f"/v1/uploads/{upload_id}")
    assert body["state"] == "COMMITTED" and body["version_id"] == "V1"


def test_get_version_returns_manifest(vault):
    upload_id, manifest = open_upload(vault, {"d/f.bin": A + B, "e.txt": b""}, dirs=("d", "empty"))
    put(vault, upload_id, sha(A), A)
    put(vault, upload_id, sha(B), B)
    vault.http("POST", f"/v1/uploads/{upload_id}/commit")
    for name in ("V1", "v1", "1"):
        status, body = vault.http("GET", f"/v1/versions/{name}")
        assert status == 200
        assert [e["path"] for e in body["entries"]] == ["d", "d/f.bin", "e.txt", "empty"]
        assert body["entries"][1]["chunks"] == [{"id": sha(A), "size": 8}, {"id": sha(B), "size": 8}]
        assert body["entries"][2]["chunks"] == [] and body["chunker"]["size"] == 8
    assert vault.http("GET", "/v1/versions/V2")[0] == 404


@pytest.mark.parametrize("path", ["../x", "/abs", "C:/x", "a/../../x", "a\\b"])
def test_t17_unsafe_paths_rejected(vault, path):
    """T17 / AC-1.3"""
    manifest = make_manifest({path: A})
    status, body = vault.http("POST", "/v1/uploads", manifest)
    assert status == 400 and body["error"]["code"] == "INVALID_PATH"
    assert path in body["error"]["message"] or repr(path) in body["error"]["message"]


def test_t17_duplicate_path_rejected(vault):
    manifest = make_manifest({"f.bin": A})
    manifest["entries"].append(dict(manifest["entries"][0]))
    status, body = vault.http("POST", "/v1/uploads", manifest)
    assert status == 400 and body["error"]["code"] == "INVALID_PATH"
    assert "duplicate" in body["error"]["message"]


def test_invalid_manifest_rejected(vault):
    status, body = vault.http("POST", "/v1/uploads", b"{broken")
    assert status == 400 and body["error"]["code"] == "INVALID_MANIFEST"
    manifest = make_manifest({"f.bin": A})
    manifest["entries"][0]["size"] = 999
    status, body = vault.http("POST", "/v1/uploads", manifest)
    assert status == 400 and body["error"]["code"] == "INVALID_MANIFEST"
