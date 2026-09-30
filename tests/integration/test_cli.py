"""CLI output fields and exit codes (PRD section 7, IR-1 to IR-5)."""

from tests.helpers.tree import sample_tree

CS = 1024


def test_backup_human_output_shows_required_fields(vault, tmp_path):
    src = sample_tree(tmp_path / "src", CS)
    result = vault.bv("backup", src, "--chunk-size", CS, json_out=False)
    assert result.code == 0, result
    for field in ("Version  : V1", "State: COMPLETE", "Upload   :", "Total folder bytes",
                  "Uploaded chunk bytes", "Reused bytes"):
        assert field in result.stdout
    assert "Sending  :" in result.stderr  # progress goes to stderr


def test_list_human_output(vault, tmp_path):
    assert "No completed versions." in vault.bv("list", json_out=False).stdout
    src = sample_tree(tmp_path / "src", CS)
    vault.bv("backup", src, "--chunk-size", CS)
    out = vault.bv("list", json_out=False).stdout
    for field in ("VERSION", "STATE", "CREATED", "FILES", "TOTAL BYTES", "UPLOADED BYTES", "REUSED BYTES",
                  "V1", "COMPLETE"):
        assert field in out
    listed = vault.bv("list").json
    assert set(listed[0]) >= {"version_id", "state", "created_at", "file_count", "total_bytes",
                              "uploaded_bytes", "reused_bytes"}


def test_restore_human_output(vault, tmp_path):
    src = sample_tree(tmp_path / "src", CS)
    vault.bv("backup", src, "--chunk-size", CS)
    result = vault.bv("restore", "V1", tmp_path / "out", json_out=False)
    assert result.code == 0 and "Version  : V1   Result: OK" in result.stdout and "6 files" in result.stdout


def test_usage_errors_exit_1(vault, tmp_path):
    assert vault.bv(json_out=False).code == 1
    assert vault.bv("frobnicate", json_out=False).code == 1
    assert vault.bv("restore", "V1", json_out=False).code == 1
    missing = vault.bv("backup", tmp_path / "does-not-exist")
    assert missing.code == 1 and missing.json["error"]["code"] == "INVALID_PATH"
    assert "next:" in missing.stderr
    bad = vault.bv("backup", tmp_path, "--chunk-size", 0)
    assert bad.code == 1


def test_errors_have_no_stack_trace(vault, tmp_path):
    for result in (vault.bv("restore", "V7", tmp_path / "o", json_out=False),
                   vault.bv("status", "nope", json_out=False),
                   vault.bv("list", "--server", "http://127.0.0.1:1", json_out=False)):
        assert result.code in (1, 3)
        assert "Traceback" not in result.stderr and "error [" in result.stderr and "next:" in result.stderr


def test_network_failure_exits_3(vault):
    result = vault.bv("list", "--server", "http://127.0.0.1:1")
    assert result.code == 3 and result.json["error"]["code"] == "SERVER_UNAVAILABLE"
