import copy
import hashlib
import json

import pytest

from brokenvault.common import manifest as mf
from brokenvault.common.errors import BVError

H1 = hashlib.sha256(b"1").hexdigest()
H2 = hashlib.sha256(b"2").hexdigest()


def base():
    return {
        "format": 1,
        "root_name": "proj",
        "chunker": {"algo": "fixed", "size": 8},
        "entries": [
            {"path": "d", "type": "dir", "mtime_ns": 5},
            {"path": "d/f.bin", "type": "file", "size": 12, "mtime_ns": 6,
             "chunks": [{"id": H1, "size": 8}, {"id": H2, "size": 4}]},
            {"path": "empty.txt", "type": "file", "size": 0, "mtime_ns": 7, "chunks": []},
        ],
    }


def assert_invalid(m, code="INVALID_MANIFEST"):
    with pytest.raises(BVError) as exc:
        mf.validate(m)
    assert exc.value.code == code


def test_valid_manifest_and_derived_values():
    m = mf.validate(base())
    info = mf.describe(m)
    assert info.total_bytes == 12
    assert info.file_count == 2 and info.dir_count == 1
    assert info.unique_chunks == ((H1, 8), (H2, 4))


def test_size_chunk_sum_mismatch():
    m = base()
    m["entries"][1]["size"] = 13
    assert_invalid(m)


def test_empty_file_with_chunks():
    m = base()
    m["entries"][2]["chunks"] = [{"id": H1, "size": 8}]
    assert_invalid(m)


@pytest.mark.parametrize("bad", ["xyz", H1.upper(), H1[:-1], H1 + "\n", 5])
def test_bad_hash_format(bad):
    m = base()
    m["entries"][1]["chunks"][0]["id"] = bad
    assert_invalid(m)


def test_non_last_chunk_must_be_full_size():
    m = base()
    m["entries"][1]["chunks"] = [{"id": H1, "size": 4}, {"id": H2, "size": 8}]
    assert_invalid(m)


def test_chunk_larger_than_chunker_size():
    m = base()
    m["entries"][1]["size"] = 9
    m["entries"][1]["chunks"] = [{"id": H1, "size": 9}]
    assert_invalid(m)


def test_same_chunk_two_sizes():
    m = base()
    m["entries"].append({"path": "g.bin", "type": "file", "size": 4, "mtime_ns": 1,
                         "chunks": [{"id": H1, "size": 4}]})
    assert_invalid(m)


def test_dir_with_size_rejected():
    m = base()
    m["entries"][0]["size"] = 0
    assert_invalid(m)


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(format=2),
    lambda m: m.update(format=True),
    lambda m: m["chunker"].update(algo="cdc"),
    lambda m: m["chunker"].update(size=0),
    lambda m: m["chunker"].update(size=5 * 1024 * 1024),
    lambda m: m["entries"][0].update(type="symlink"),
    lambda m: m["entries"][0].update(mtime_ns=-1),
    lambda m: m["entries"][0].update(mtime_ns=1.5),
    lambda m: m.update(entries="nope"),
    lambda m: m.update(root_name=""),
])
def test_schema_violations(mutate):
    m = base()
    mutate(m)
    assert_invalid(m)


@pytest.mark.parametrize("path", ["../x", "/etc/x", "C:/x", "a/../b"])
def test_unsafe_paths(path):
    m = base()
    m["entries"].append({"path": path, "type": "dir", "mtime_ns": 1})
    assert_invalid(m, "INVALID_PATH")


def test_duplicate_path():
    m = base()
    m["entries"].append({"path": "d", "type": "dir", "mtime_ns": 1})
    assert_invalid(m, "INVALID_PATH")


def test_missing_parent_dir_entry():
    m = base()
    del m["entries"][0]
    assert_invalid(m)


def test_file_as_parent():
    m = base()
    m["entries"].append({"path": "empty.txt/child", "type": "dir", "mtime_ns": 1})
    assert_invalid(m)


def test_canonical_form_is_stable_under_order():
    a = base()
    b = copy.deepcopy(a)
    b["entries"].reverse()
    b = json.loads(json.dumps(b, sort_keys=False))
    b["entries"][0] = dict(reversed(list(b["entries"][0].items())))
    ca, cb = mf.validate(a), mf.validate(b)
    assert mf.canonical_json(ca) == mf.canonical_json(cb)
    assert mf.manifest_id(ca) == mf.manifest_id(cb)


def test_manifest_id_changes_with_content():
    a, b = base(), base()
    b["entries"][2]["mtime_ns"] = 8
    assert mf.manifest_id(mf.validate(a)) != mf.manifest_id(mf.validate(b))


def test_unknown_keys_are_dropped():
    m = base()
    m["extra"] = 1
    m["entries"][0]["total_bytes"] = 999
    assert mf.manifest_id(mf.validate(m)) == mf.manifest_id(mf.validate(base()))


def test_parse_rejects_bad_json():
    with pytest.raises(BVError) as exc:
        mf.parse(b"{not json")
    assert exc.value.code == "INVALID_MANIFEST"
