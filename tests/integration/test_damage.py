"""Damage detection: T12 to T16 and T19 (FR-5.3, FR-6, FR-3.5)."""

import os

from tests.helpers.chunks import file_chunks
from tests.helpers.tree import build_tree, rand_bytes, sample_tree

CS = 1024


def backup(vault, folder):
    result = vault.bv("backup", folder, "--chunk-size", CS)
    assert result.code == 0, result
    return result.json


def two_versions(vault, tmp_path):
    """V1 and V2 share most chunks; V2 adds docs/new.bin and changes one chunk of big.bin."""
    src = sample_tree(tmp_path / "src", CS)
    backup(vault, src)
    v1_big = file_chunks(src, "big.bin", CS)
    with open(os.path.join(src, "big.bin"), "r+b") as f:
        f.seek(CS * 5)
        f.write(b"V2-ONLY!")
    with open(os.path.join(src, "docs", "new.bin"), "wb") as f:
        f.write(rand_bytes(CS * 2, 50))
    backup(vault, src)
    return src, v1_big


def impact(report):
    return {(v["version"], f["path"]) for v in report["versions"] for f in v["files"]}


def test_t16_healthy_store(vault, tmp_path):
    """T16 / AC-6.4"""
    two_versions(vault, tmp_path)
    result = vault.bv("verify")
    assert result.code == 0
    report = result.json
    assert report["healthy"] is True and report["damaged_chunks"] == []
    assert report["checked_versions"] == 2 and report["checked_chunks"] == len(vault.chunk_files())
    assert [(v["version"], v["status"]) for v in report["versions"]] == [("V1", "OK"), ("V2", "OK")]
    text = vault.bv("verify", json_out=False)
    assert text.code == 0 and "All chunks are healthy." in text.stdout


def test_t13_t15_corrupt_shared_chunk(vault, tmp_path):
    """T13, T15 / AC-6.1, AC-6.3: a chunk shared by V1 and V2 names both versions and every file."""
    src, _ = two_versions(vault, tmp_path)
    shared = file_chunks(src, "copies/a.bin", CS)[1]  # in a.bin and b.bin, in V1 and V2
    vault.tamper(shared)

    result = vault.bv("verify")
    assert result.code == 2
    report = result.json
    assert report["healthy"] is False
    assert [(c["id"], c["reason"]) for c in report["damaged_chunks"]] == [(shared, "CORRUPT")]
    assert "hash mismatch" in report["damaged_chunks"][0]["detail"]
    assert impact(report) == {("V1", "copies/a.bin"), ("V1", "copies/b.bin"),
                              ("V2", "copies/a.bin"), ("V2", "copies/b.bin")}
    assert all(v["status"] == "DAMAGED" for v in report["versions"])

    text = vault.bv("verify", json_out=False)
    assert text.code == 2
    assert f"DAMAGED  chunk {shared}  CORRUPT" in text.stdout
    assert "V1  copies/a.bin" in text.stdout and "V2  copies/b.bin" in text.stdout
    assert "No repairs were made." in text.stdout


def test_corrupt_by_truncation_reports_size(vault, tmp_path):
    src, _ = two_versions(vault, tmp_path)
    target = file_chunks(src, "docs/new.bin", CS)[0]  # only in V2
    with open(vault.chunk_path(target), "r+b") as f:
        f.truncate(10)
    report = vault.bv("verify").json
    assert report["damaged_chunks"][0]["reason"] == "CORRUPT"
    assert "size mismatch" in report["damaged_chunks"][0]["detail"]
    assert impact(report) == {("V2", "docs/new.bin")}
    assert [(v["version"], v["status"]) for v in report["versions"]] == [("V1", "OK"), ("V2", "DAMAGED")]


def test_t14_missing_chunk_and_no_repair(vault, tmp_path):
    """T14 / AC-6.2, FR-6.3"""
    src, v1_big = two_versions(vault, tmp_path)
    only_v1 = v1_big[5]  # the chunk V2 replaced
    shared = v1_big[0]
    vault.delete_chunk(only_v1)
    vault.tamper(shared)
    before = vault.chunk_files()

    result = vault.bv("verify")
    assert result.code == 2
    report = result.json
    reasons = {c["id"]: c["reason"] for c in report["damaged_chunks"]}
    assert reasons == {only_v1: "MISSING", shared: "CORRUPT"}
    assert impact(report) == {("V1", "big.bin"), ("V2", "big.bin")}
    v1_files = next(v for v in report["versions"] if v["version"] == "V1")["files"]
    assert sorted(v1_files[0]["chunks"]) == sorted([only_v1, shared])
    v2_files = next(v for v in report["versions"] if v["version"] == "V2")["files"]
    assert v2_files[0]["chunks"] == [shared]

    assert vault.chunk_files() == before  # verify changed nothing
    assert report["repaired"] == 0
    assert vault.bv("verify").code == 2  # still damaged on a second run
    assert len(vault.versions()) == 2  # versions stay listed


def test_t12_restore_fails_loudly_on_tampered_chunk(vault, tmp_path):
    """T12 / AC-5.2"""
    src, _ = two_versions(vault, tmp_path)
    bad = file_chunks(src, "docs/deep/nested/note.txt", CS)[0]
    vault.tamper(bad)
    dest = tmp_path / "out"
    result = vault.bv("restore", "V1", dest)
    assert result.code == 2
    err = result.json["error"]
    assert err["code"] == "RESTORE_CHUNK_INVALID"
    assert err["details"]["path"] == "docs/deep/nested/note.txt" and err["details"]["chunk"] == bad
    assert "docs/deep/nested/note.txt" in result.stderr and bad in result.stderr
    assert "result" not in result.json  # never reports success

    text = vault.bv("restore", "V1", tmp_path / "out-text", json_out=False)
    assert text.code == 2 and "Result: OK" not in text.stdout


def test_restore_fails_on_missing_chunk(vault, tmp_path):
    src, _ = two_versions(vault, tmp_path)
    gone = file_chunks(src, "docs/new.bin", CS)[1]
    vault.delete_chunk(gone)
    result = vault.bv("restore", "V2", tmp_path / "out")
    assert result.code == 2 and result.json["error"]["code"] == "CHUNK_MISSING"
    assert result.json["error"]["details"]["path"] == "docs/new.bin"
    assert vault.bv("restore", "V1", tmp_path / "out1").code == 0  # V1 does not need that chunk


def test_t19_commit_refuses_damaged_shared_chunk(vault, tmp_path):
    """T19 / AC-3.5: a new backup that needs a damaged stored chunk does not complete."""
    shared_data = rand_bytes(CS * 3, 1)
    first = build_tree(str(tmp_path / "first"), {"shared.bin": shared_data})
    backup(vault, first)
    damaged = file_chunks(first, "shared.bin", CS)[1]
    vault.tamper(damaged)
    tampered_bytes = vault.chunk_files()[damaged]

    second = build_tree(str(tmp_path / "second"), {"copy/shared.bin": shared_data,
                                                   "extra.bin": rand_bytes(CS, 2)})
    result = vault.bv("backup", second, "--chunk-size", CS)
    assert result.code == 2, result
    err = result.json["error"]
    assert err["code"] == "CORRUPT_CHUNK" and err["details"]["chunks"] == [damaged]
    assert damaged in result.stderr and "bv verify" in result.stderr

    assert vault.chunk_files()[damaged] == tampered_bytes  # byte-identical: not repaired, not overwritten
    assert [v["version_id"] for v in vault.versions()] == ["V1"]  # no new version
    status = vault.bv("status", err["details"]["upload_id"])
    assert status.json["state"] == "OPEN"  # the upload stays unfinished and resumable

    report = vault.bv("verify")
    assert report.code == 2
    assert impact(report.json) == {("V1", "shared.bin")}

    again = vault.bv("backup", second, "--chunk-size", CS)  # still refused, same upload
    assert again.code == 2 and again.json["error"]["details"]["upload_id"] == err["details"]["upload_id"]
    assert vault.chunk_files()[damaged] == tampered_bytes
