"""Test environment: a real server subprocess on a random loopback port, plus CLI and raw HTTP access."""

import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(REPO, "src")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_manifest(files, chunk_size=8, root_name="raw", dirs=()):
    """Build a manifest dict from {path: bytes}. Parent dirs must be listed in dirs."""
    entries = [{"path": d, "type": "dir", "mtime_ns": 1_700_000_000_000_000_000} for d in dirs]
    for path, data in files.items():
        chunks = [{"id": sha(data[i:i + chunk_size]), "size": len(data[i:i + chunk_size])}
                  for i in range(0, len(data), chunk_size)]
        entries.append({"path": path, "type": "file", "size": len(data),
                        "mtime_ns": 1_700_000_000_000_000_000, "chunks": chunks})
    return {"format": 1, "root_name": root_name, "chunker": {"algo": "fixed", "size": chunk_size},
            "entries": entries}


class CliResult:
    def __init__(self, code, stdout, stderr):
        self.code, self.stdout, self.stderr = code, stdout, stderr
        try:
            self.json = json.loads(stdout) if stdout.strip() else None
        except ValueError:
            self.json = None

    def __repr__(self):
        return f"CliResult(code={self.code}, stdout={self.stdout!r}, stderr={self.stderr!r})"


class Vault:
    def __init__(self, base_dir):
        self.base_dir = str(base_dir)
        self.data_dir = os.path.join(self.base_dir, "vault-data")
        self.state_dir = os.path.join(self.base_dir, "client-state")
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc = None

    # -- server process --------------------------------------------------

    def _env(self, extra=None):
        env = dict(os.environ)
        env["PYTHONPATH"] = SRC
        env["PYTHONIOENCODING"] = "utf-8"
        env["BV_SERVER"] = self.url
        env["BV_STATE_DIR"] = self.state_dir
        env["BV_RETRY_DELAYS"] = "0.05,0.1,0.2"
        env.pop("BV_FAULT", None)
        env.update(extra or {})
        return env

    def start(self, fault=None):
        assert self.proc is None or self.proc.poll() is not None, "server already running"
        extra = {"BV_FAULT": fault} if fault else None
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "brokenvault.server", "--data-dir", self.data_dir, "--port", str(self.port)],
            env=self._env(extra), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited with code {self.proc.returncode}")
            try:
                if self.http("GET", "/v1/health")[0] == 200:
                    return self
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("server did not start")

    def kill(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        if self.proc:
            self.proc.wait(timeout=10)

    def restart(self, fault=None):
        self.kill()
        return self.start(fault)

    def wait_exit(self, timeout=10):
        return self.proc.wait(timeout=timeout)

    # -- access ----------------------------------------------------------

    def http(self, method, path, body=None, headers=None):
        """One raw request. Returns (status, parsed JSON or raw bytes)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            if body is None and method in ("POST", "PUT"):
                body = b""
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
            if resp.getheader("Content-Type", "").startswith("application/json"):
                return resp.status, json.loads(data)
            return resp.status, data
        finally:
            conn.close()

    def bv(self, *args, json_out=True, env=None):
        """Run the client CLI as a subprocess."""
        cmd = [sys.executable, "-m", "brokenvault.client", *[str(a) for a in args]]
        if json_out:
            cmd.append("--json")
        done = subprocess.run(cmd, env=self._env(env), capture_output=True, text=True,
                              encoding="utf-8", timeout=300)
        return CliResult(done.returncode, done.stdout, done.stderr)

    def versions(self):
        status, body = self.http("GET", "/v1/versions")
        assert status == 200
        return body

    # -- store inspection and tampering ----------------------------------

    def chunk_path(self, chunk_id):
        return os.path.join(self.data_dir, "chunks", chunk_id[:2], chunk_id)

    def chunk_files(self):
        """chunk_id -> raw bytes of every stored chunk file."""
        out = {}
        root = os.path.join(self.data_dir, "chunks")
        if os.path.isdir(root):
            for sub in os.listdir(root):
                for name in os.listdir(os.path.join(root, sub)):
                    with open(os.path.join(root, sub, name), "rb") as f:
                        out[name] = f.read()
        return out

    def tamper(self, chunk_id):
        """Flip the first byte of a stored chunk (size unchanged)."""
        path = self.chunk_path(chunk_id)
        with open(path, "r+b") as f:
            first = f.read(1)
            f.seek(0)
            f.write(bytes([first[0] ^ 0xFF]))

    def delete_chunk(self, chunk_id):
        os.remove(self.chunk_path(chunk_id))
