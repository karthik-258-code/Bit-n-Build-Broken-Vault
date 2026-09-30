import hashlib
import os

import pytest

from brokenvault.client.chunker import iter_chunks, read_chunk
from brokenvault.client.scanner import scan
from brokenvault.common.errors import BVError
from tests.helpers.tree import rand_bytes, sample_tree


def write(tmp_path, data, name="f.bin"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_empty_file_yields_no_chunks(tmp_path):
    assert list(iter_chunks(write(tmp_path, b""), 8)) == []


def test_exact_multiple_boundaries(tmp_path):
    data = rand_bytes(24, 1)
    chunks = list(iter_chunks(write(tmp_path, data), 8))
    assert [(o, n) for o, n, _ in chunks] == [(0, 8), (8, 8), (16, 8)]
    assert [h for _, _, h in chunks] == [hashlib.sha256(data[i:i + 8]).hexdigest() for i in (0, 8, 16)]


def test_last_short_chunk(tmp_path):
    chunks = list(iter_chunks(write(tmp_path, rand_bytes(19, 2)), 8))
    assert [(o, n) for o, n, _ in chunks] == [(0, 8), (8, 8), (16, 3)]


def test_deterministic_and_read_back(tmp_path):
    path = write(tmp_path, rand_bytes(100, 3))
    first, second = list(iter_chunks(path, 16)), list(iter_chunks(path, 16))
    assert first == second
    for offset, length, cid in first:
        assert hashlib.sha256(read_chunk(path, offset, length)).hexdigest() == cid


# --- scanner: AC-1.1, AC-1.2 ---

def test_scan_lists_nested_empty_file_and_empty_dir(tmp_path):
    root = sample_tree(tmp_path / "src", chunk=1024)
    result = scan(root, 1024)
    by_path = {e["path"]: e for e in result.manifest["entries"]}
    assert by_path["empty-dir"]["type"] == "dir"
    assert by_path["empty.txt"] == {"path": "empty.txt", "type": "file", "size": 0,
                                    "mtime_ns": by_path["empty.txt"]["mtime_ns"], "chunks": []}
    assert by_path["docs/deep/nested"]["type"] == "dir"
    assert by_path["docs/deep/nested/note.txt"]["size"] == 1024 + 17
    assert len(by_path["big.bin"]["chunks"]) == 21
    assert result.manifest["root_name"] == "src"
    # every directory has an entry, including non-empty ones
    assert {"docs", "docs/deep", "copies"} <= set(by_path)
    # entries sorted by UTF-8 path
    paths = [e["path"] for e in result.manifest["entries"]]
    assert paths == sorted(paths, key=lambda p: p.encode())


def test_scan_twice_is_identical(tmp_path):
    root = sample_tree(tmp_path / "src")
    assert scan(root, 1024).manifest == scan(root, 1024).manifest


def test_identical_files_share_chunks_and_locations(tmp_path):
    root = sample_tree(tmp_path / "src")
    result = scan(root, 1024)
    by_path = {e["path"]: e for e in result.manifest["entries"]}
    assert by_path["copies/a.bin"]["chunks"] == by_path["copies/b.bin"]["chunks"]
    for cid, (path, offset, length) in result.locations.items():
        assert hashlib.sha256(read_chunk(path, offset, length)).hexdigest() == cid


def test_scan_missing_folder(tmp_path):
    with pytest.raises(BVError) as exc:
        scan(str(tmp_path / "nope"))
    assert exc.value.code == "INVALID_PATH"


def test_scan_skips_symlink_with_warning(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "real.txt").write_bytes(b"x")
    try:
        os.symlink(root / "real.txt", root / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available on this system")
    result = scan(str(root), 1024)
    assert [e["path"] for e in result.manifest["entries"]] == ["real.txt"]
    assert any("link.txt" in w for w in result.warnings)
