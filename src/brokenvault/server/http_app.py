"""HTTP router and thin handlers (LLD 5.8)."""

import json
import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from ..common.constants import API_PREFIX, IO_BLOCK, MAX_CHUNK_SIZE, MAX_MANIFEST_BYTES
from ..common.errors import BVError
from .chunk_store import ChunkStore, drain
from .db import Database
from .faults import Faults
from .upload_service import UploadService
from .verify_service import VerifyService
from .version_service import VersionService

_ID = r"([A-Za-z0-9_-]{1,64})"
_HASH = r"([0-9a-f]{64})"
ROUTES = [
    ("GET", re.compile(rf"{API_PREFIX}/health"), "health"),
    ("POST", re.compile(rf"{API_PREFIX}/uploads"), "create_upload"),
    ("GET", re.compile(rf"{API_PREFIX}/uploads/{_ID}"), "upload_status"),
    ("GET", re.compile(rf"{API_PREFIX}/uploads/{_ID}/missing"), "missing"),
    ("POST", re.compile(rf"{API_PREFIX}/uploads/{_ID}/commit"), "commit"),
    ("PUT", re.compile(rf"{API_PREFIX}/chunks/{_HASH}"), "put_chunk"),
    ("GET", re.compile(rf"{API_PREFIX}/chunks/{_HASH}"), "get_chunk"),
    ("GET", re.compile(rf"{API_PREFIX}/versions"), "list_versions"),
    ("GET", re.compile(rf"{API_PREFIX}/versions/{_ID}"), "get_version"),
    ("POST", re.compile(rf"{API_PREFIX}/verify"), "verify"),
]


class App:
    """Services wired to one data directory."""

    def __init__(self, data_dir, fault_spec=None, fast_commit=False):
        os.makedirs(data_dir, exist_ok=True)
        self.data_dir = data_dir
        self.db = Database(os.path.join(data_dir, "meta.db"))
        self.store = ChunkStore(data_dir)
        self.tmp_removed = self.store.cleanup_tmp()
        self.uploads = UploadService(self.db, self.store, Faults(fault_spec), fast_commit)
        self.versions = VersionService(self.db)
        self.verifier = VerifyService(self.db, self.store)

    def startup_counts(self):
        return {
            "versions": self.db.query_one("SELECT COUNT(*) AS n FROM versions")["n"],
            "open_uploads": self.db.query_one("SELECT COUNT(*) AS n FROM uploads WHERE state = 'OPEN'")["n"],
            "chunk_files": sum(1 for _ in self.store.iter_all_hashes()),
            "tmp_removed": self.tmp_removed,
        }


class _Body:
    """The request body as a stream that cannot read past Content-Length."""

    def __init__(self, handler):
        self._handler = handler

    def read(self, n):
        h = self._handler
        n = min(n, h._remaining)
        if n <= 0:
            return b""
        block = h.rfile.read(n)
        h._remaining -= len(block)
        return block


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive
    timeout = 60
    server_version = "BrokenVault/1"

    # -- plumbing --------------------------------------------------------

    def _dispatch(self, method):
        self._remaining = 0
        self._length = None
        self._responded = False
        try:
            self._read_length()
            path = urlsplit(self.path)
            path_known = False
            for route_method, pattern, name in ROUTES:
                match = pattern.fullmatch(path.path)
                if not match:
                    continue
                path_known = True
                if route_method == method:
                    getattr(self, "h_" + name)(parse_qs(path.query), *match.groups())
                    return
            if path_known:
                raise BVError("METHOD_NOT_ALLOWED", f"{method} is not allowed on {path.path}")
            raise BVError("NOT_FOUND", f"no such endpoint: {path.path}")
        except BVError as exc:
            if exc.code == "CLIENT_ABORTED":
                self.close_connection = True
            self._send_json(exc.http_status, exc.to_dict())
        except (ConnectionError, TimeoutError):
            self.close_connection = True
        except Exception:
            sys.stderr.write(f"INTERNAL error on {method} {self.path}\n{traceback.format_exc()}")
            self._send_json(500, BVError("INTERNAL", "internal server error; see the server log").to_dict())

    def _read_length(self):
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise BVError("LENGTH_REQUIRED", "chunked transfer encoding is not supported; send Content-Length")
        raw = self.headers.get("Content-Length")
        if raw is None:
            return
        if not raw.isdigit():
            self.close_connection = True
            raise BVError("BAD_REQUEST", f"invalid Content-Length {raw!r}")
        self._length = self._remaining = int(raw)

    def _send_json(self, status, obj):
        if self._responded:  # failure after the response started: the connection is unusable
            self.close_connection = True
            return
        # Unread body bytes would be parsed as the next request on a keep-alive
        # connection: drain them when small, otherwise close.
        if self._remaining > 0:
            if self._remaining > MAX_CHUNK_SIZE or self.close_connection:
                self.close_connection = True
            else:
                try:
                    if drain(_Body(self), self._remaining):
                        self.close_connection = True
                except OSError:
                    self.close_connection = True
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._responded = True
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            self.close_connection = True

    def _read_body(self, limit):
        if self._length is None:
            self.close_connection = True
            raise BVError("LENGTH_REQUIRED", "Content-Length is required")
        if self._length > limit:
            raise BVError("PAYLOAD_TOO_LARGE", f"body of {self._length} bytes exceeds the limit {limit}")
        data = self.rfile.read(self._length)
        self._remaining -= len(data)
        if len(data) != self._length:
            raise BVError("CLIENT_ABORTED", "request body ended early")
        return data

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_PATCH(self):
        self._dispatch("PATCH")

    # -- handlers --------------------------------------------------------

    @property
    def app(self):
        return self.server.app

    def h_health(self, query):
        self._send_json(200, {"ok": True})

    def h_create_upload(self, query):
        self._send_json(200, self.app.uploads.create_upload(self._read_body(MAX_MANIFEST_BYTES)))

    def h_upload_status(self, query, upload_id):
        self._send_json(200, self.app.uploads.status(upload_id))

    def h_missing(self, query, upload_id):
        self._send_json(200, self.app.uploads.missing(upload_id))

    def h_commit(self, query, upload_id):
        self._send_json(200, self.app.uploads.commit(upload_id))

    def h_put_chunk(self, query, chunk_id):
        if self._length is None:
            self.close_connection = True
            raise BVError("LENGTH_REQUIRED", f"chunk {chunk_id}: Content-Length is required")
        if self._length > MAX_CHUNK_SIZE:
            raise BVError("PAYLOAD_TOO_LARGE",
                          f"chunk {chunk_id}: {self._length} bytes exceeds the limit {MAX_CHUNK_SIZE}")
        upload_id = (query.get("upload_id") or [""])[0]
        if not upload_id:
            raise BVError("BAD_REQUEST", "query parameter upload_id is required")
        stored = self.app.uploads.put_chunk(upload_id, chunk_id, self._length, _Body(self))
        self._send_json(201 if stored else 200, {"stored": stored})

    def h_get_chunk(self, query, chunk_id):
        with self.app.store.open(chunk_id) as f:
            size = os.fstat(f.fileno()).st_size
            self._responded = True
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            while True:
                block = f.read(IO_BLOCK)
                if not block:
                    break
                self.wfile.write(block)

    def h_list_versions(self, query):
        self._send_json(200, self.app.versions.list_versions())

    def h_get_version(self, query, version):
        self._send_json(200, self.app.versions.get_version(version))

    def h_verify(self, query):
        self._send_json(200, self.app.verifier.verify())


class BVServer(ThreadingHTTPServer):
    daemon_threads = True
    # SO_REUSEADDR on Windows would let two servers bind the same port.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, app, verbose=False):
        super().__init__(address, Handler)
        self.app = app
        self.verbose = verbose

    def handle_error(self, request, client_address):
        """A client that drops its connection is normal; log anything else in one line."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return
        sys.stderr.write(f"connection error from {client_address}: {type(exc).__name__}: {exc}\n")
