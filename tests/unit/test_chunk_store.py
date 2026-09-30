import hashlib
import io
import os
import threading

import pytest

from brokenvault.common.errors import BVError
from brokenvault.server.chunk_store import ALREADY_PRESENT, STORED, ChunkStore


def sha(data):
    return hashlib.sha256(data).hexdigest()


def all_files(root):
    return [os.path.join(d, f) for d, _, files in os.walk(root) for f in files]


def test_put_exists_open(tmp_path):
    store = ChunkStore(str(tmp_path))
    data = b"hello chunk"
    h = sha(data)
    assert not store.exists(h) and store.size(h) is None
    assert store.put(h, io.BytesIO(data), len(data)) == STORED
    assert store.exists(h) and store.size(h) == len(data)
    with store.open(h) as f:
        assert f.read() == data
    assert store.path(h).endswith(os.path.join("chunks", h[:2], h))
    assert list(store.iter_all_hashes()) == [h]
    assert os.listdir(store.tmp_dir) == []


def test_hash_mismatch_rejected_and_nothing_left(tmp_path):
    store = ChunkStore(str(tmp_path))
    h = sha(b"expected")
    with pytest.raises(BVError) as exc:
        store.put(h, io.BytesIO(b"tampered"), 8)
    assert exc.value.code == "CHUNK_HASH_MISMATCH"
    assert not store.exists(h)
    assert all_files(str(tmp_path)) == []


def test_short_body_rejected(tmp_path):
    store = ChunkStore(str(tmp_path))
    with pytest.raises(BVError) as exc:
        store.put(sha(b"abcdef"), io.BytesIO(b"abc"), 6)
    assert exc.value.code == "CLIENT_ABORTED"
    assert all_files(str(tmp_path)) == []


def test_duplicate_put_stores_once_and_drains(tmp_path):
    store = ChunkStore(str(tmp_path))
    data = b"same bytes"
    h = sha(data)
    assert store.put(h, io.BytesIO(data), len(data)) == STORED
    stream = io.BytesIO(data + b"NEXT")
    assert store.put(h, stream, len(data)) == ALREADY_PRESENT
    assert stream.read() == b"NEXT"  # body fully consumed
    assert len(all_files(store.chunks_dir)) == 1


def test_existing_chunk_is_never_overwritten(tmp_path):
    store = ChunkStore(str(tmp_path))
    data = b"original"
    h = sha(data)
    store.put(h, io.BytesIO(data), len(data))
    with open(store.path(h), "wb") as f:  # damage it
        f.write(b"DAMAGED!")
    assert store.put(h, io.BytesIO(data), len(data)) == ALREADY_PRESENT
    tmp = store.stage(h, io.BytesIO(data), len(data))
    assert store.publish(h, tmp) is False
    with open(store.path(h), "rb") as f:
        assert f.read() == b"DAMAGED!"
    assert os.listdir(store.tmp_dir) == []


def test_open_missing(tmp_path):
    with pytest.raises(BVError) as exc:
        ChunkStore(str(tmp_path)).open(sha(b"nope"))
    assert exc.value.code == "CHUNK_MISSING"


def test_cleanup_tmp(tmp_path):
    store = ChunkStore(str(tmp_path))
    for name in ("a.part", "b.part"):
        with open(os.path.join(store.tmp_dir, name), "wb") as f:
            f.write(b"half")
    assert store.cleanup_tmp() == 2
    assert os.listdir(store.tmp_dir) == []


def test_too_large(tmp_path):
    with pytest.raises(BVError) as exc:
        ChunkStore(str(tmp_path)).put(sha(b"x"), io.BytesIO(b"x"), 10, max_len=5)
    assert exc.value.code == "PAYLOAD_TOO_LARGE"


def test_concurrent_put_same_hash(tmp_path):
    store = ChunkStore(str(tmp_path))
    data = os.urandom(200_000)
    h = sha(data)
    results, errors = [], []

    def worker():
        try:
            results.append(store.put(h, io.BytesIO(data), len(data)))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert results.count(STORED) == 1
    assert len(all_files(store.chunks_dir)) == 1
    assert os.listdir(store.tmp_dir) == []
    with store.open(h) as f:
        assert f.read() == data
