"""HTTP calls to the server with retries and error mapping (LLD 6.4)."""

import http.client
import json
import time
from urllib.parse import urlsplit

from ..common.constants import API_PREFIX, MAX_CHUNK_SIZE
from ..common.errors import BVError

RETRY_DELAYS = (0.2, 0.5, 1.0, 2.0, 4.0)  # seconds between attempts
TIMEOUT = 60
LONG_TIMEOUT = 15 * 60  # commit and verify re-hash the stored data


class Transport:
    """One persistent connection. Every call is idempotent, so every call may be retried."""

    def __init__(self, base_url, retry_delays=RETRY_DELAYS):
        parts = urlsplit(base_url if "//" in base_url else "http://" + base_url)
        if parts.scheme != "http" or not parts.hostname:
            raise BVError("BAD_REQUEST", f"server URL {base_url!r} must look like http://host:port")
        self.base_url = f"http://{parts.hostname}:{parts.port or 80}"
        self._host, self._port = parts.hostname, parts.port or 80
        self._delays = tuple(retry_delays)
        self._conn = None

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _request(self, method, path, body=None, timeout=TIMEOUT, max_body=None):
        failure = None
        for attempt in range(len(self._delays) + 1):
            if attempt:
                time.sleep(self._delays[attempt - 1])
            try:
                if self._conn is None:
                    self._conn = http.client.HTTPConnection(self._host, self._port, timeout=timeout)
                    self._conn.connect()
                self._conn.sock.settimeout(timeout)
                self._conn.request(method, API_PREFIX + path, body=body)
                resp = self._conn.getresponse()
                length = resp.getheader("Content-Length")
                if max_body is not None and length is not None and int(length) > max_body:
                    raise http.client.HTTPException(f"response of {length} bytes exceeds the limit {max_body}")
                data = resp.read()
                if resp.will_close:
                    self.close()
            except (OSError, http.client.HTTPException) as exc:
                self.close()
                failure = BVError("SERVER_UNAVAILABLE",
                                  f"cannot reach the server at {self.base_url} ({type(exc).__name__}: {exc})",
                                  {"server": self.base_url})
                continue
            except BaseException:
                self.close()  # e.g. Ctrl-C mid-request: the connection state is unknown
                raise
            if resp.status >= 500:
                failure = self._error(resp.status, data)
                continue
            if resp.status >= 400:
                raise self._error(resp.status, data)
            return resp.status, data
        raise failure

    @staticmethod
    def _error(status, data):
        try:
            err = json.loads(data)["error"]
            return BVError(err["code"], err["message"], err.get("details"))
        except (ValueError, KeyError, TypeError):
            return BVError("INTERNAL", f"server replied HTTP {status} without an error body")

    def _json(self, method, path, body=None, timeout=TIMEOUT):
        return json.loads(self._request(method, path, body, timeout)[1])

    # -- API -------------------------------------------------------------

    def health(self):
        return self._json("GET", "/health")

    def create_upload(self, manifest_bytes):
        return self._json("POST", "/uploads", manifest_bytes)

    def get_upload(self, upload_id):
        return self._json("GET", f"/uploads/{upload_id}")

    def missing(self, upload_id):
        return self._json("GET", f"/uploads/{upload_id}/missing")

    def put_chunk(self, upload_id, chunk_id, data):
        """Returns True when the server newly stored the chunk."""
        status, _ = self._request("PUT", f"/chunks/{chunk_id}?upload_id={upload_id}", data)
        return status == 201

    def commit(self, upload_id):
        return self._json("POST", f"/uploads/{upload_id}/commit", b"", LONG_TIMEOUT)

    def list_versions(self):
        return self._json("GET", "/versions")

    def get_version(self, version):
        return self._json("GET", f"/versions/{version}")

    def get_chunk(self, chunk_id):
        return self._request("GET", f"/chunks/{chunk_id}", max_body=MAX_CHUNK_SIZE)[1]

    def verify(self):
        return self._json("POST", "/verify", b"", LONG_TIMEOUT)
