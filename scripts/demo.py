"""BrokenVault demo: backup -> change -> interrupt -> restart -> restore -> damage + verify.

Runs the real server and client as subprocesses on a small generated dataset
and prints every command with its output. Standard library only.

    python scripts/demo.py            # work folder: ./demo-work (recreated each run)
    python scripts/demo.py --keep     # leave the server data and restored folders for inspection
"""

import argparse
import filecmp
import http.client
import os
import random
import shutil
import socket
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src")
CHUNK = 512 * 1024


def step(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}", flush=True)


def check(condition, text):
    print(f"  [{'PASS' if condition else 'FAIL'}] {text}", flush=True)
    if not condition:
        raise SystemExit(f"demo failed: {text}")


def same_tree(a, b):
    """True when both folders hold the same paths, types and file bytes."""
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return False
    _, mismatch, errors = filecmp.cmpfiles(a, b, cmp.common_files, shallow=False)
    if mismatch or errors:
        return False
    return all(same_tree(os.path.join(a, d), os.path.join(b, d)) for d in cmp.common_dirs)


def max_mtime_diff(a, b):
    worst = 0.0
    for dirpath, dirnames, filenames in os.walk(a):
        for name in dirnames + filenames:
            left = os.path.join(dirpath, name)
            right = os.path.join(b, os.path.relpath(left, a))
            worst = max(worst, abs(os.stat(left).st_mtime - os.stat(right).st_mtime))
    return worst


class Demo:
    def __init__(self, work, port):
        self.work = work
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.vault = os.path.join(work, "vault")
        self.env = dict(os.environ, PYTHONPATH=SRC, PYTHONIOENCODING="utf-8", BV_SERVER=self.url,
                        BV_STATE_DIR=os.path.join(work, "client-state"))
        self.env.pop("BV_FAULT", None)
        self.server = None

    def start_server(self):
        print(f"$ python -m brokenvault.server --data-dir {self.vault} --port {self.port}", flush=True)
        self.server = subprocess.Popen(
            [sys.executable, "-m", "brokenvault.server", "--data-dir", self.vault, "--port", str(self.port)],
            env=self.env)
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
                conn.request("GET", "/v1/health")
                if conn.getresponse().status == 200:
                    conn.close()
                    return
            except OSError:
                time.sleep(0.1)
        raise SystemExit("demo failed: the server did not start")

    def stop_server(self):
        if self.server and self.server.poll() is None:
            self.server.kill()
            self.server.wait(timeout=10)
        print("(server process killed)", flush=True)

    def bv(self, *args, expect=0):
        print(f"$ bv {' '.join(str(a) for a in args)}", flush=True)
        done = subprocess.run([sys.executable, "-m", "brokenvault.client", *[str(a) for a in args]],
                              env=self.env, capture_output=True, text=True, encoding="utf-8")
        text = (done.stderr + done.stdout).rstrip()  # progress (stderr) first, then the summary
        if text:
            print("\n".join("    " + line for line in text.splitlines()), flush=True)
        print(f"    (exit code {done.returncode})", flush=True)
        check(done.returncode == expect, f"exit code is {expect}")
        return done.stdout + done.stderr

    def chunk_files(self):
        root = os.path.join(self.vault, "chunks")
        return sorted(os.path.join(d, f) for d, _, files in os.walk(root) for f in files)


def build_dataset(root):
    rng = random.Random(2026)
    os.makedirs(os.path.join(root, "video"))
    os.makedirs(os.path.join(root, "art", "drafts"))
    os.makedirs(os.path.join(root, "exports"))  # empty folder
    with open(os.path.join(root, "video", "intro.bin"), "wb") as f:
        f.write(rng.randbytes(6 * 1024 * 1024 + 12345))
    poster = rng.randbytes(1024 * 1024 + 500)
    for name in ("poster.bin", os.path.join("drafts", "poster-copy.bin")):  # two identical files
        with open(os.path.join(root, "art", name), "wb") as f:
            f.write(poster)
    with open(os.path.join(root, "notes.txt"), "w", encoding="utf-8") as f:
        f.write("BrokenVault demo dataset\n")
    open(os.path.join(root, "empty.txt"), "wb").close()  # empty file


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work", default=os.path.join(REPO, "demo-work"))
    parser.add_argument("--port", type=int, default=0, help="server port (default: a free port)")
    parser.add_argument("--keep", action="store_true", help="keep the work folder after the demo")
    args = parser.parse_args()

    port = args.port
    if not port:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
    work = os.path.abspath(args.work)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    demo = Demo(work, port)
    data = os.path.join(work, "event-project")

    try:
        step("1. Start the server and show the dataset (nested folders, an empty file, an empty folder)")
        build_dataset(data)
        for dirpath, dirnames, filenames in os.walk(data):
            rel = os.path.relpath(dirpath, data)
            for name in sorted(dirnames):
                print(f"    [dir ] {os.path.join(rel, name)}")
            for name in sorted(filenames):
                print(f"    [file] {os.path.join(rel, name)}  {os.path.getsize(os.path.join(dirpath, name)):,} bytes")
        state_a = os.path.join(work, "copy-of-state-A")
        shutil.copytree(data, state_a)
        demo.start_server()

        step("2. Backup: V1 completes; identical files are sent once")
        out = demo.bv("backup", data)
        check("Version  : V1   State: COMPLETE" in out, "V1 is COMPLETE")
        demo.bv("list")
        chunks_v1 = len(demo.chunk_files())
        print(f"    stored chunk files: {chunks_v1}")

        step("3. Change: edit a small part of the large file and add a new file")
        with open(os.path.join(data, "video", "intro.bin"), "r+b") as f:
            f.seek(3 * 1024 * 1024 + 100)
            f.write(b"EDITED-IN-PLACE")
        with open(os.path.join(data, "art", "logo.bin"), "wb") as f:
            f.write(random.Random(7).randbytes(3 * CHUNK + 777))
        state_b = os.path.join(work, "copy-of-state-B")
        shutil.copytree(data, state_b)
        print("    video/intro.bin: 15 bytes changed; art/logo.bin: new file")

        step("4. Interrupt: stop the V2 backup after 2 chunks (test hook --stop-after-chunks)")
        out = demo.bv("backup", data, "--stop-after-chunks", 2, expect=130)
        check("State: UNFINISHED" in out, "the upload reports state UNFINISHED")
        upload_id = out.split("State: UNFINISHED (upload ")[1].split(")")[0]
        out = demo.bv("list")
        check("V1" in out and "V2" not in out, "list still shows only V1")
        demo.bv("status", upload_id)
        out = demo.bv("restore", upload_id, os.path.join(work, "restored-unfinished"), expect=1)
        check("VERSION_NOT_FOUND" in out, "an unfinished upload cannot be restored")

        step("5. Restart: kill the server, start it again, run the same backup command")
        demo.stop_server()
        demo.start_server()
        out = demo.bv("backup", data)
        check("Version  : V2   State: COMPLETE" in out, "V2 is COMPLETE")
        check(upload_id in out and "resumed" in out, "the client continued the same upload ID")
        demo.bv("list")

        step("6. Restore: V1 and V2 into empty folders, compared with the saved originals")
        out1, out2 = os.path.join(work, "restored-v1"), os.path.join(work, "restored-v2")
        demo.bv("restore", "V1", out1)
        demo.bv("restore", "V2", out2)
        check(same_tree(state_a, out1), "restored V1 equals the saved copy of state A (paths, types, bytes)")
        check(same_tree(state_b, out2), "restored V2 equals the saved copy of state B (paths, types, bytes)")
        check(max_mtime_diff(data, out2) <= 1.0, "V2 modification times match the source folder within 1 s")
        check(os.path.isdir(os.path.join(out1, "exports")) and os.path.getsize(os.path.join(out1, "empty.txt")) == 0,
              "the empty folder and the empty file were restored")

        step("7. Damage + verify: alter one stored chunk, delete another")
        demo.bv("verify")
        files = demo.chunk_files()
        altered, deleted = files[0], files[-1]
        with open(altered, "r+b") as f:
            first = f.read(1)
            f.seek(0)
            f.write(bytes([first[0] ^ 0xFF]))
        os.remove(deleted)
        print(f"    altered one byte of chunk {os.path.basename(altered)}")
        print(f"    deleted chunk            {os.path.basename(deleted)}")
        with open(altered, "rb") as f:
            altered_bytes = f.read()
        out = demo.bv("verify", expect=2)
        check(os.path.basename(altered) in out and "CORRUPT" in out, "the altered chunk is reported CORRUPT")
        check(os.path.basename(deleted) in out and "MISSING" in out, "the deleted chunk is reported MISSING")
        with open(altered, "rb") as f:
            check(f.read() == altered_bytes and not os.path.exists(deleted), "verify repaired nothing")
        demo.bv("list")

        step("Demo complete: every check passed")
    finally:
        demo.stop_server()
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
