"""T20 (NFR-5): large generated dataset. Excluded from the default run.

Run with:  pytest -m slow            (1 GB)
           BV_SLOW_MB=256 pytest -m slow
"""

import os
import tracemalloc

import pytest

from brokenvault.client.backup import BackupRunner
from brokenvault.client.restore import restore
from brokenvault.client.transport import Transport
from brokenvault.common.constants import DEFAULT_CHUNK_SIZE
from tests.helpers.tree import compare_trees

MIB = 1024 * 1024


def write_random(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        left = size
        while left > 0:
            block = os.urandom(min(MIB, left))
            f.write(block)
            left -= len(block)


@pytest.mark.slow
def test_t20_large_dataset(vault, tmp_path):
    total_mb = int(os.environ.get("BV_SLOW_MB", "1024"))
    src = str(tmp_path / "src")
    sizes = [total_mb * MIB // 2, total_mb * MIB // 4, total_mb * MIB // 8, total_mb * MIB // 8]
    for i, size in enumerate(sizes):
        write_random(os.path.join(src, f"dir{i}", f"file{i}.bin"), size)
    os.makedirs(os.path.join(src, "empty-dir"))
    open(os.path.join(src, "empty.txt"), "wb").close()

    def run():
        transport = Transport(vault.url)
        try:
            return BackupRunner(transport, src, DEFAULT_CHUNK_SIZE, state_dir=vault.state_dir).run()
        finally:
            transport.close()

    tracemalloc.start()
    v1 = run()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert v1["version_id"] == "V1" and v1["total_bytes"] == sum(sizes) == v1["uploaded_bytes"]
    assert peak < 100 * MIB, f"client peak memory {peak / MIB:.0f} MiB"  # streaming, no whole-file buffers

    with open(os.path.join(src, "dir0", "file0.bin"), "r+b") as f:  # change 1 MiB in place
        f.seek(sizes[0] // 2)
        f.write(os.urandom(MIB))
    write_random(os.path.join(src, "added", "new.bin"), 2 * MIB)

    v2 = run()
    assert v2["version_id"] == "V2"
    assert v2["uploaded_bytes"] <= 5 * MIB  # far less than the total
    assert v2["reused_bytes"] == v2["total_bytes"] - v2["uploaded_bytes"]

    dest = str(tmp_path / "out")
    transport = Transport(vault.url)
    try:
        tracemalloc.start()
        done = restore(transport, "V2", dest)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    finally:
        transport.close()
    assert done["result"] == "OK" and peak < 100 * MIB
    assert compare_trees(src, dest) == []
    assert vault.bv("verify").code == 0
