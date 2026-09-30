"""Error codes shared by client and server (LLD section 7).

One exception type carries a code; the code maps to an HTTP status on the
server and to a process exit code on the client.
"""

EXIT_OK = 0
EXIT_INPUT = 1
EXIT_INTEGRITY = 2
EXIT_NETWORK = 3
EXIT_INTERRUPTED = 130

# code -> (HTTP status, CLI exit code)
CATALOG = {
    "INVALID_MANIFEST": (400, EXIT_INPUT),
    "INVALID_PATH": (400, EXIT_INPUT),
    "UNSAFE_PATH": (400, EXIT_INPUT),
    "UNKNOWN_UPLOAD": (404, EXIT_INPUT),
    "UPLOAD_CLOSED": (409, EXIT_INPUT),
    "CHUNK_HASH_MISMATCH": (400, EXIT_INTEGRITY),
    "CHUNK_SIZE_MISMATCH": (400, EXIT_INTEGRITY),
    "CHUNK_NOT_IN_MANIFEST": (422, EXIT_INTEGRITY),
    "INCOMPLETE_UPLOAD": (409, EXIT_NETWORK),
    "CORRUPT_CHUNK": (422, EXIT_INTEGRITY),
    "VERSION_NOT_FOUND": (404, EXIT_INPUT),
    "CHUNK_MISSING": (404, EXIT_INTEGRITY),
    "DEST_NOT_EMPTY": (400, EXIT_INPUT),
    "SOURCE_CHANGED": (400, EXIT_INTEGRITY),
    "SERVER_UNAVAILABLE": (503, EXIT_NETWORK),
    "RESTORE_CHUNK_INVALID": (500, EXIT_INTEGRITY),
    "RESTORE_SIZE_MISMATCH": (500, EXIT_INTEGRITY),
    # transport-level codes
    "CLIENT_ABORTED": (400, EXIT_NETWORK),
    "BAD_REQUEST": (400, EXIT_INPUT),
    "NOT_FOUND": (404, EXIT_INPUT),
    "METHOD_NOT_ALLOWED": (405, EXIT_INPUT),
    "LENGTH_REQUIRED": (411, EXIT_INPUT),
    "PAYLOAD_TOO_LARGE": (413, EXIT_INPUT),
    "INTERNAL": (500, EXIT_NETWORK),
}


class BVError(Exception):
    """An expected failure with a catalog code."""

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def http_status(self):
        return CATALOG.get(self.code, (500, EXIT_INPUT))[0]

    @property
    def exit_code(self):
        return CATALOG.get(self.code, (500, EXIT_INPUT))[1]

    def to_dict(self):
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}

    def __str__(self):
        return f"{self.code}: {self.message}"
