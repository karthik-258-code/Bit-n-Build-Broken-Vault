"""Shared constants for the client and the server."""

import re

API_PREFIX = "/v1"
DEFAULT_CHUNK_SIZE = 512 * 1024
MAX_CHUNK_SIZE = 4 * 1024 * 1024  # server rejects larger chunk bodies
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
IO_BLOCK = 64 * 1024  # streaming block size
MAX_SEGMENT_BYTES = 255
HASH_RE = re.compile(r"[0-9a-f]{64}")  # use with fullmatch()

DEFAULT_SERVER = "http://127.0.0.1:8765"
