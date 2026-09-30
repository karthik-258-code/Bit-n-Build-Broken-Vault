"""Fixed-size streaming chunker (LLD 4.4). Holds one chunk in memory at a time."""

from ..common.hashing import sha256_bytes


def iter_chunks(path, size):
    """Yield (offset, length, chunk_id) for each chunk of the file. Empty file yields nothing."""
    with open(path, "rb") as f:
        offset = 0
        while True:
            buf = f.read(size)
            if not buf:
                break
            yield offset, len(buf), sha256_bytes(buf)
            offset += len(buf)


def read_chunk(path, offset, length):
    """Re-read one chunk's bytes from disk."""
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(length)
