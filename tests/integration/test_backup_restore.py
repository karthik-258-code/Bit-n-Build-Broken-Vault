"""Backup, reuse and exact restore through the real CLI: T1, T2, T3, T7, T11."""

import os
import shutil

from brokenvault.common.constants import DEFAULT_CHUNK_SIZE

from tests.helpers.chunks import tree_chunks, total_bytes
from tests.helpers.tree import compare_trees, rand_bytes, sample_tree

CS = 1024  # small chunks keep the tests fast


def backup(vault, folder, *extra):
    result = vault.bv("backup", folder, "--chunk-size", CS, *extra)
    assert result.code == 0, result
    return result.json


def modify(src):
    """Change a small part of the large file in place and add a new file."""
    with open(os.path.join(src, "big.bin"), "r+b") as f:
        f.seek(CS * 7 + 10)
        f.write(b"CHANGED!")
    with open(os.path.join(src, "docs", "new.bin"), "wb") as f:
        f.write(rand_bytes(CS * 2 + 5, 99))


def test_t1_backup_lists_and_restores_every_item(vault, tmp_path):
    """T1 / AC-1.1, AC-5.1"""
    src = sample_tree(tmp_path / "src", CS)
    done = backup(vault, src)
    assert done["version_id"] == "V1" and done["state"] == "COMPLETE"
    assert done["file_count"] == 6 and done["dir_count"] == 5

    status, version = vault.http("GET", "/v1/versions/V1")
    by_path = {e["path"]: e for e in version["entries"]}
    assert by_path["empty.txt"]["chunks"] == [] and by_path["empty.txt"]["size"] == 0
    assert by_path["empty-dir"]["type"] == "dir"
    assert "docs/deep/nested/note.txt" in by_path

    dest = tmp_path / "out"
    restored = vault.bv("restore", "V1", dest)
    assert restored.code == 0, restored
    assert restored.json["result"] == "OK" and restored.json["files_restored"] == 6
    assert compare_trees(src, str(dest)) == []
    assert os.path.isdir(dest / "empty-dir") and os.path.getsize(dest / "empty.txt") == 0


def test_first_backup_counters(vault, tmp_path):
    src = sample_tree(tmp_path / "src", CS)
    done = backup(vault, src)
    distinct = tree_chunks(src, CS)
    total = total_bytes(src, CS)
    assert done["total_bytes"] == total
    assert done["uploaded_bytes"] == sum(distinct.values())  # identical files sent once (FR-2.4)
    assert done["reused_bytes"] == total - done["uploaded_bytes"] > 0
    assert done["chunks_sent"] == len(distinct) == done["unique_chunks"]
    assert set(vault.chunk_files()) == set(distinct)


def test_t2_second_backup_sends_only_new_chunks(vault, tmp_path):
    """T2 / AC-2.1, AC-2.4"""
    src = sample_tree(tmp_path / "src", CS)
    backup(vault, src)
    before = tree_chunks(src, CS)
    modify(src)
    after = tree_chunks(src, CS)
    new = {cid: size for cid, size in after.items() if cid not in before}

    done = backup(vault, src)
    assert done["version_id"] == "V2"
    assert done["uploaded_bytes"] == sum(new.values())
    assert done["uploaded_bytes"] < done["total_bytes"]
    assert done["chunks_sent"] == len(new) == 4  # 1 changed chunk + 3 chunks of the new file
    assert done["reused_bytes"] == done["total_bytes"] - done["uploaded_bytes"]
    # one stored copy per distinct hash
    stored = vault.chunk_files()
    assert set(stored) == set(before) | set(after)


def test_t3_unchanged_folder_sends_nothing_and_makes_a_version(vault, tmp_path):
    """T3 / AC-2.2"""
    src = sample_tree(tmp_path / "src", CS)
    first = backup(vault, src)
    second = backup(vault, src)
    assert second["version_id"] == "V2" and second["state"] == "COMPLETE"
    assert second["uploaded_bytes"] == 0 and second["chunks_sent"] == 0
    assert second["reused_bytes"] == second["total_bytes"] == first["total_bytes"]
    assert second["upload_id"] != first["upload_id"]
    assert [v["version_id"] for v in vault.versions()] == ["V1", "V2"]


def test_t11_restore_v1_and_v2_match_originals(vault, tmp_path):
    """T11 / AC-5.1, AC-5.3: versions are independent snapshots."""
    src = sample_tree(tmp_path / "src", CS)
    backup(vault, src)
    copy_v1 = tmp_path / "copy-v1"
    shutil.copytree(src, copy_v1, copy_function=shutil.copy2)
    os.utime(os.path.join(src, "docs"), (1_600_000_000, 1_600_000_000))  # an old directory mtime
    modify(src)
    os.remove(os.path.join(src, "empty.txt"))
    backup(vault, src)

    out2, out1 = tmp_path / "out2", tmp_path / "out1"
    assert vault.bv("restore", "V2", out2).code == 0
    assert vault.bv("restore", "v1", out1).code == 0  # V1 restored after V2 exists
    assert compare_trees(src, str(out2)) == []
    assert compare_trees(str(out1), str(out2)) != []
    # V1 content equals the saved copy (copytree does not keep directory mtimes, so compare files and types)
    diffs = [d for d in compare_trees(str(copy_v1), str(out1)) if not d.startswith("mtime differs")]
    assert diffs == []
    assert os.path.exists(out1 / "empty.txt") and not os.path.exists(out2 / "empty.txt")
    with open(out1 / "big.bin", "rb") as a, open(out2 / "big.bin", "rb") as b:
        assert a.read() != b.read()


def test_restore_sets_file_and_directory_mtimes(vault, tmp_path):
    src = sample_tree(tmp_path / "src", CS)
    old = 1_500_000_000
    for rel in ("docs/deep/nested/note.txt", "docs/deep/nested", "docs/deep", "docs", "empty-dir", "empty.txt"):
        os.utime(os.path.join(src, *rel.split("/")), (old, old))
        old += 1000
    backup(vault, src)
    dest = tmp_path / "out"
    assert vault.bv("restore", "V1", dest).code == 0
    assert compare_trees(src, str(dest)) == []
    assert int(os.stat(dest / "docs" / "deep" / "nested").st_mtime) == 1_500_001_000


def test_restore_refuses_non_empty_destination(vault, tmp_path):
    src = sample_tree(tmp_path / "src", CS)
    backup(vault, src)
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "keep.txt").write_text("mine")
    result = vault.bv("restore", "V1", dest)
    assert result.code == 1 and result.json["error"]["code"] == "DEST_NOT_EMPTY"
    assert os.listdir(dest) == ["keep.txt"]
    empty = tmp_path / "empty-out"
    empty.mkdir()
    assert vault.bv("restore", "V1", empty).code == 0  # an existing empty folder is fine


def test_restore_unknown_version(vault, tmp_path):
    result = vault.bv("restore", "V9", tmp_path / "out")
    assert result.code == 1 and result.json["error"]["code"] == "VERSION_NOT_FOUND"
    assert not os.path.exists(tmp_path / "out")


def test_t7_versions_survive_server_restart(vault, tmp_path):
    """T7 / AC-3.4"""
    src = sample_tree(tmp_path / "src", CS)
    done = backup(vault, src)
    listed = vault.versions()
    vault.restart()
    assert vault.versions() == listed
    assert listed[0]["version_id"] == done["version_id"] and listed[0]["state"] == "COMPLETE"
    dest = tmp_path / "out"
    assert vault.bv("restore", "V1", dest).code == 0
    assert compare_trees(src, str(dest)) == []


def test_empty_folder_backup(vault, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    done = backup(vault, src)
    assert done["total_bytes"] == 0 and done["file_count"] == 0 and done["uploaded_bytes"] == 0
    dest = tmp_path / "out"
    assert vault.bv("restore", "V1", dest).code == 0
    assert os.listdir(dest) == []


def test_default_chunk_size_roundtrip(vault, tmp_path):
    """One backup with the real default chunk size (256 KiB)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(rand_bytes(DEFAULT_CHUNK_SIZE * 2 + 1000, 5))
    result = vault.bv("backup", src)
    assert result.code == 0 and result.json["unique_chunks"] == 3
    status, version = vault.http("GET", "/v1/versions/V1")
    assert version["chunker"] == {"algo": "fixed", "size": DEFAULT_CHUNK_SIZE}
    assert DEFAULT_CHUNK_SIZE == 256 * 1024
    dest = tmp_path / "out"
    assert vault.bv("restore", "V1", dest).code == 0
    assert compare_trees(str(src), str(dest)) == []
