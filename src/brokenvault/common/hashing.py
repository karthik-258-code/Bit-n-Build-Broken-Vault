"""SHA-256 helpers. Streams are read in blocks, never whole."""

import hashlib

from .constants import IO_BLOCK


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_stream(stream):
    hasher = hashlib.sha256()
    while True:
        block = stream.read(IO_BLOCK)
        if not block:
            break
        hasher.update(block)
    return hasher.hexdigest()


def sha256_file(path):
    with open(path, "rb") as f:
        return sha256_stream(f)
