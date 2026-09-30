"""Interrupt, crash and resume: T5, T8, T18 (FR-3, FR-4)."""

import os
import shutil

import pytest

from tests.helpers.chunks import tree_chunks
from tests.helpers.tree import compare_trees, rand_bytes, sample_tree

CS = 1024


def run_backup(vault, folder, *extra):
    return vault.bv("backup", folder, "--chunk-size", CS, *extra)


def modify(src):
    with open(os.path.join(src, "big.bin"), "r+b") as f:
        f.seek(CS * 3)
        f.write(rand_bytes(CS * 6, 77))  # six changed chunks
    with open(os.path.join(src, "added.bin"), "wb") as f:
        f.write(rand_bytes(CS * 4 + 9, 78))


def test_t5_unfinished_upload_is_hidden(vault, tmp_path):
    """T5 / AC-3.1, AC-3.2"""
    src = sample_tree(tmp_path / "src", CS)
    stopped = run_backup(vault, src, "--stop-after-chunks", 5)
    assert stopped.code == 130, stopped
    assert stopped.json["state"] == "UNFINISHED" and stopped.json["chunks_stored"] == 5
    assert "UNFINISHED" in stopped.stderr and "Run the same command again" in stopped.stderr
    upload_id = stopped.json["upload_id"]

    listed = vault.bv("list")
    assert listed.code == 0 and listed.json == []

    restore = vault.bv("restore", upload_id, tmp_path / "out")
    assert restore.code == 1 and restore.json["error"]["code"] == "VERSION_NOT_FOUND"
    assert not os.path.exists(tmp_path / "out")

    status = vault.bv("status", upload_id)
    assert status.code == 0 and status.json["state"] == "OPEN" and status.json["chunks_stored"] == 5
    assert "UNFINISHED" in vault.bv("status", upload_id, json_out=False).stdout


@pytest.mark.parametrize("later_backup", [False, True], ids=["first-backup", "later-backup"])
def test_t8_interrupt_restart_resume(vault, tmp_path, later_backup):
    """T8 / AC-4.1, AC-4.2: same upload ID, nothing re-sent, bytes counted once."""
    src = sample_tree(tmp_path / "src", CS)
    have = {}
    if later_backup:
        assert run_backup(vault, src).code == 0
        have = tree_chunks(src, CS)
        modify(src)
    need = {cid: size for cid, size in tree_chunks(src, CS).items() if cid not in have}
    versions_before = vault.versions()

    stopped = run_backup(vault, src, "--stop-after-chunks", 4)
    assert stopped.code == 130
    upload_id = stopped.json["upload_id"]
    assert vault.versions() == versions_before  # nothing new is visible

    vault.restart()  # kill the server; the client process has already ended
    assert vault.versions() == versions_before

    resumed = run_backup(vault, src)
    assert resumed.code == 0, resumed
    done = resumed.json
    assert done["upload_id"] == upload_id and done["resumed"] is True
    assert 4 + done["chunks_sent"] == len(need)  # PUT count over both runs = distinct missing chunks
    assert done["uploaded_bytes"] == sum(need.values())  # no double counting
    assert done["reused_bytes"] == done["total_bytes"] - done["uploaded_bytes"]
    assert done["state"] == "COMPLETE"
    assert len(vault.versions()) == len(versions_before) + 1

    dest = tmp_path / "out"
    assert vault.bv("restore", done["version_id"], dest).code == 0
    assert compare_trees(src, str(dest)) == []
    assert os.listdir(vault.state_dir) == []  # state file removed after success


def test_resume_with_explicit_id_and_lost_state_file(vault, tmp_path):
    """--resume <id> names the upload; a lost state file still finds it through create-upload."""
    src = sample_tree(tmp_path / "src", CS)
    stopped = run_backup(vault, src, "--stop-after-chunks", 3)
    upload_id = stopped.json["upload_id"]
    shutil.rmtree(vault.state_dir)

    again = run_backup(vault, src, "--stop-after-chunks", 2)
    assert again.code == 130 and again.json["upload_id"] == upload_id  # idempotent create-upload
    shutil.rmtree(vault.state_dir)

    done = run_backup(vault, src, "--resume", upload_id)
    assert done.code == 0 and done.json["upload_id"] == upload_id and done.json["resumed"] is True
    assert 3 + 2 + done.json["chunks_sent"] == done.json["unique_chunks"]
    assert done.json["uploaded_bytes"] == sum(tree_chunks(src, CS).values())

    closed = run_backup(vault, src, "--resume", upload_id)
    assert closed.code == 1 and closed.json["error"]["code"] == "UPLOAD_CLOSED"
    unknown = run_backup(vault, src, "--resume", "feedfacefeedface")
    assert unknown.code == 1 and unknown.json["error"]["code"] == "UNKNOWN_UPLOAD"


def test_resume_refused_when_folder_changed(vault, tmp_path):
    src = sample_tree(tmp_path / "src", CS)
    stopped = run_backup(vault, src, "--stop-after-chunks", 3)
    upload_id = stopped.json["upload_id"]
    modify(src)
    explicit = run_backup(vault, src, "--resume", upload_id)
    assert explicit.code == 2 and explicit.json["error"]["code"] == "SOURCE_CHANGED"
    # Without --resume a new upload starts; the stale one stays hidden.
    done = run_backup(vault, src)
    assert done.code == 0 and done.json["upload_id"] != upload_id and done.json["version_id"] == "V1"
    assert len(vault.versions()) == 1
    assert vault.bv("status", upload_id).json["state"] == "OPEN"


def test_server_down_exits_3_with_resume_hint(stopped_vault, tmp_path):
    src = sample_tree(tmp_path / "src", CS)
    result = run_backup(stopped_vault, src)
    assert result.code == 3 and result.json["error"]["code"] == "SERVER_UNAVAILABLE"
    assert "run the same command again" in result.stderr


@pytest.mark.parametrize("fault", [
    "crash_after_chunks=3", "crash_before_rename", "crash_before_commit_tx", "crash_after_commit_tx"])
def test_t18_server_crash_points(stopped_vault, tmp_path, fault):
    """T18: kill the server at each fault point, restart, rerun; invariants hold."""
    vault = stopped_vault
    src = sample_tree(tmp_path / "src", CS)
    need = tree_chunks(src, CS)
    vault.start(fault=fault)

    crashed = run_backup(vault, src)
    assert crashed.code == 3, crashed  # retries exhausted
    assert crashed.json["state"] == "UNFINISHED"
    assert "Run the same command again" in crashed.stderr
    upload_id = crashed.json["upload_id"]
    assert vault.wait_exit() == 137  # the server really died at the fault point

    stored_after_crash = set(vault.chunk_files())
    if fault == "crash_before_rename":
        assert stored_after_crash == set()  # intent row exists, chunk file does not
    elif fault == "crash_after_chunks=3":
        assert len(stored_after_crash) == 3
    else:
        assert stored_after_crash == set(need)

    vault.start()  # no fault
    assert os.listdir(os.path.join(vault.data_dir, "tmp")) == []  # in-flight writes cleaned at startup
    versions = vault.versions()
    if fault == "crash_after_commit_tx":
        # The version was committed before the crash; it is complete and durable.
        assert [v["version_id"] for v in versions] == ["V1"]
        assert versions[0]["uploaded_bytes"] == sum(need.values())
    else:
        assert versions == []  # no unfinished version is visible

    rerun = run_backup(vault, src)
    assert rerun.code == 0, rerun
    done = rerun.json
    assert done["state"] == "COMPLETE"
    if fault == "crash_after_commit_tx":
        # LLD 6.3: the stored upload is already committed, so the client starts a new snapshot.
        assert done["version_id"] == "V2" and done["uploaded_bytes"] == 0 and done["chunks_sent"] == 0
    else:
        assert done["upload_id"] == upload_id and done["resumed"] is True
        assert done["version_id"] == "V1"
        assert done["uploaded_bytes"] == sum(need.values())  # counted exactly once across the crash
        assert done["chunks_sent"] == len(need) - len(stored_after_crash)

    versions = vault.versions()
    assert sum(v["uploaded_bytes"] for v in versions) == sum(need.values())  # no duplicate counts
    assert set(vault.chunk_files()) == set(need)
    for v in versions:
        dest = tmp_path / ("out-" + v["version_id"])
        assert vault.bv("restore", v["version_id"], dest).code == 0
        assert compare_trees(src, str(dest)) == []
    assert vault.bv("verify").code == 0
