import os

import pytest

from brokenvault.common.errors import BVError
from brokenvault.common.paths import parent_paths, safe_join, validate_rel_path


@pytest.mark.parametrize("path", ["a", "a/b/c.txt", "with space/ünï.bin", "a.b/..c", "x" * 255])
def test_valid_paths(path):
    assert validate_rel_path(path) == path


@pytest.mark.parametrize("path", [
    "/etc/x",            # absolute
    "C:/x", "c:x",       # drive prefix
    "../x", "a/../b",    # parent escape
    "./a", "a/./b",      # dot segment
    "a//b", "a/", "",    # empty segment / empty path
    "a\\b",              # backslash
    "a\x00b",            # NUL
    "x" * 256,           # overlong segment
    None, 7,             # not a string
    "bad\udcffname",     # not valid UTF-8
])
def test_invalid_paths(path):
    with pytest.raises(BVError) as exc:
        validate_rel_path(path)
    assert exc.value.code == "INVALID_PATH"
    assert exc.value.http_status == 400


def test_parent_paths():
    assert parent_paths("a/b/c") == ["a", "a/b"]
    assert parent_paths("a") == []


def test_safe_join_inside(tmp_path):
    joined = safe_join(str(tmp_path), "a/b.txt")
    assert joined == os.path.join(os.path.realpath(tmp_path), "a", "b.txt")


def test_safe_join_rejects_escape(tmp_path):
    with pytest.raises(BVError):
        safe_join(str(tmp_path), "../outside")
