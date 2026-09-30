"""Content-addressed chunk files on disk (LLD 5.2, 5.3).

A chunk "exists" when its file exists. Writes go to tmp/, are hash-checked
and fsynced, then renamed into place. An existing chunk file is never
overwritten and nothing here deletes a chunk.
"""

import hashlib
import os
import threading
import uuid

from ..common.constants import HASH_RE, IO_BLOCK, MAX_CHUNK_SIZE
from ..common.errors import BVError

STORED = "STORED"
ALREADY_PRESENT = "ALREADY_PRESENT"


def drain(stream, length):
    """Read and discard length bytes. Returns the number of bytes not received."""
    while length > 0:
        block = stream.read(min(IO_BLOCK, length))
        if not block:
            break
        length -= len(block)
    return length


class ChunkStore:
    def __init__(self, data_dir):
        self.chunks_dir = os.path.join(data_dir, "chunks")
        self.tmp_dir = os.path.join(data_dir, "tmp")
        os.makedirs(self.chunks_dir, exist_ok=True)
        os.makedirs(self.tmp_dir, exist_ok=True)
        self._locks = [threading.Lock() for _ in range(256)]

    def path(self, h):
        if not HASH_RE.fullmatch(h):
            raise BVError("BAD_REQUEST", f"not a chunk ID: {h!r}")
        return os.path.join(self.chunks_dir, h[:2], h)

    def exists(self, h):
        return os.path.isfile(self.path(h))

    def size(self, h):
        """File size of a stored chunk, or None when the file is absent."""
        try:
            p = self.path(h)
            return os.path.getsize(p) if os.path.isfile(p) else None
        except OSError:
            return None

    def stage(self, h, stream, length):
        """Write the body to a temp file, check its hash, fsync. Returns the temp path."""
        tmp = os.path.join(self.tmp_dir, uuid.uuid4().hex + ".part")
        hasher = hashlib.sha256()
        received = 0
        try:
            with open(tmp, "wb") as f:
                while received < length:
                    block = stream.read(min(IO_BLOCK, length - received))
                    if not block:
                        raise BVError("CLIENT_ABORTED",
                                      f"chunk {h}: body ended after {received} of {length} bytes")
                    f.write(block)
                    hasher.update(block)
                    received += len(block)
                if hasher.hexdigest() != h:
                    raise BVError("CHUNK_HASH_MISMATCH",
                                  f"chunk body hashes to {hasher.hexdigest()}, not the declared ID {h}",
                                  {"chunk": h, "actual": hasher.hexdigest()})
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            self.discard(tmp)
            raise
        return tmp

    def publish(self, h, tmp):
        """Atomically move a staged file into place. Returns False if the chunk already existed."""
        final = self.path(h)
        with self._locks[int(h[:2], 16)]:
            if os.path.isfile(final):  # never overwrite an existing chunk file
                self.discard(tmp)
                return False
            os.makedirs(os.path.dirname(final), exist_ok=True)
            os.replace(tmp, final)
            self._fsync_dir(os.path.dirname(final))
        return True

    def put(self, h, stream, length, max_len=MAX_CHUNK_SIZE):
        """Store one chunk. Returns STORED or ALREADY_PRESENT."""
        if length > max_len:
            raise BVError("PAYLOAD_TOO_LARGE", f"chunk {h}: {length} bytes exceeds the limit {max_len}")
        if self.exists(h):
            drain(stream, length)
            return ALREADY_PRESENT
        tmp = self.stage(h, stream, length)
        return STORED if self.publish(h, tmp) else ALREADY_PRESENT

    def open(self, h):
        try:
            return open(self.path(h), "rb")
        except FileNotFoundError:
            raise BVError("CHUNK_MISSING", f"chunk {h} is not in the store", {"chunk": h}) from None

    def iter_all_hashes(self):
        for sub in sorted(os.listdir(self.chunks_dir)):
            subdir = os.path.join(self.chunks_dir, sub)
            if os.path.isdir(subdir):
                for name in sorted(os.listdir(subdir)):
                    if HASH_RE.fullmatch(name):
                        yield name

    def cleanup_tmp(self):
        """Delete in-flight writes left by a crashed process. Returns how many."""
        removed = 0
        for name in os.listdir(self.tmp_dir):
            self.discard(os.path.join(self.tmp_dir, name))
            removed += 1
        return removed

    @staticmethod
    def discard(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass

    @staticmethod
    def _fsync_dir(path):
        if os.name == "nt":  # directories cannot be fsynced on Windows
            return
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
