"""BrokenVault live demo in one script, on two real folders (version 1 and version 2 of a dataset).

Runs, in order: start server, backup V1, list, interrupted backup V2, list, status,
restore of the unfinished upload (must fail), server restart, resumed backup V2, list,
restore V1 and V2, folder comparison, verify, damage two chunks, verify, list.

    python scripts/live_demo.py <v1-folder> <v2-folder>
    python scripts/live_demo.py <v1-folder> <v2-folder> --pause     # wait for Enter before each step
    python scripts/live_demo.py                                     # uses the organisers' sample ZIPs
                                                                    # from ./Participants_guide

Standard library only. Server data and restored folders go to a new temp folder (printed at the start).
"""

import argparse
import filecmp
import glob
import http.client
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src")
PAUSE = False


def step(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}", flush=True)
    if PAUSE:
        input("  (press Enter to run this step) ")


def check(ok, text):
    print(f"  [{'PASS' if ok else 'FAIL'}] {text}", flush=True)
    return ok


def compare(a, b):
    """List of differences in paths, types and file bytes between two folders."""
    cmp = filecmp.dircmp(a, b)
    diffs = [f"only in source: {n}" for n in cmp.left_only] + [f"only in restore: {n}" for n in cmp.right_only]
    diffs += [f"type differs: {n}" for n in cmp.common_funny]
    _, mismatch, errors = filecmp.cmpfiles(a, b, cmp.common_files, shallow=False)
    diffs += [f"bytes differ: {n}" for n in mismatch + errors]
    for name in cmp.common_files:
        if abs(os.stat(os.path.join(a, name)).st_mtime - os.stat(os.path.join(b, name)).st_mtime) > 1.0:
            diffs.append(f"modification time differs: {name}")
    for name in cmp.common_dirs:
        diffs += [f"{name}/{d}" for d in compare(os.path.join(a, name), os.path.join(b, name))]
    return diffs


class Demo:
    def __init__(self, work, port):
        self.work, self.port = work, port
        self.vault = os.path.join(work, "vault")
        self.env = dict(os.environ, PYTHONPATH=SRC, PYTHONIOENCODING="utf-8",
                        BV_SERVER=f"http://127.0.0.1:{port}", BV_STATE_DIR=os.path.join(work, "client-state"))
        self.env.pop("BV_FAULT", None)
        self.server = None
        self.failed = 0

    def check(self, ok, text):
        if not check(ok, text):
            self.failed += 1

    def start_server(self):
        print(f"$ bv-server --data-dir {self.vault} --port {self.port}", flush=True)
        self.server = subprocess.Popen(
            [sys.executable, "-m", "brokenvault.server", "--data-dir", self.vault, "--port", str(self.port)],
            env=self.env)
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
                conn.request("GET", "/v1/health")
                ok = conn.getresponse().status == 200
                conn.close()
                if ok:
                    return
            except OSError:
                time.sleep(0.1)
        raise SystemExit("the server did not start")

    def stop_server(self):
        if self.server and self.server.poll() is None:
            self.server.kill()
            self.server.wait(timeout=10)
            print("(server process killed)", flush=True)

    def bv(self, *args):
        """Run one client command, print it with its output, return (exit code, output)."""
        print(f"$ bv {' '.join(str(a) for a in args)}", flush=True)
        done = subprocess.run([sys.executable, "-m", "brokenvault.client", *[str(a) for a in args]],
                              env=self.env, capture_output=True, text=True, encoding="utf-8")
        text = (done.stderr + done.stdout).rstrip()
        for line in text.splitlines():
            print("    " + line, flush=True)
        print(f"    (exit code {done.returncode})", flush=True)
        return done.returncode, text

    def chunk_files(self):
        return sorted(glob.glob(os.path.join(self.vault, "chunks", "*", "*")))


def sample_folders(work):
    """Extract the organisers' two sample ZIPs and return their folders."""
    found = []
    for name in ("brokenvault_sample_v1", "brokenvault_sample_v2"):
        archive = os.path.join(REPO, "Participants_guide", name + ".zip")
        if not os.path.isfile(archive):
            raise SystemExit("usage: python scripts/live_demo.py <v1-folder> <v2-folder>\n"
                             f"(no folders given and {archive} was not found)")
        target = os.path.join(work, "dataset")
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
        found.append(os.path.join(target, name))
    return found


def main():
    global PAUSE
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("v1", nargs="?", help="folder for the first backup")
    parser.add_argument("v2", nargs="?", help="folder for the second backup (the changed state)")
    parser.add_argument("--pause", action="store_true", help="wait for Enter before each step")
    parser.add_argument("--keep", action="store_true", help="keep the work folder afterwards")
    parser.add_argument("--port", type=int, default=0, help="server port (default: a free port)")
    args = parser.parse_args()
    PAUSE = args.pause
    if bool(args.v1) != bool(args.v2):
        parser.error("give both folders, or none to use the organisers' sample")

    port = args.port
    if not port:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
    work = tempfile.mkdtemp(prefix="brokenvault-live-")
    print(f"work folder: {work}", flush=True)
    demo = Demo(work, port)

    try:
        if args.v1:
            v1, v2 = os.path.abspath(args.v1), os.path.abspath(args.v2)
            for folder in (v1, v2):
                if not os.path.isdir(folder):
                    raise SystemExit(f"not a folder: {folder}")
        else:
            v1, v2 = sample_folders(work)
        out1, out2 = os.path.join(work, "restored-v1"), os.path.join(work, "restored-v2")

        step("1. Start the server")
        demo.start_server()

        step("2. Backup version 1, then list")
        code, out = demo.bv("backup", v1)
        demo.check(code == 0 and "Version  : V1   State: COMPLETE" in out, "V1 is COMPLETE")
        demo.bv("list")

        step("3. Change: version 2 is the changed folder")
        print(f"    version 1: {v1}\n    version 2: {v2}", flush=True)

        step("4. Interrupt the backup of version 2 after 1 chunk, then list and status")
        code, out = demo.bv("backup", v2, "--stop-after-chunks", 1)
        match = re.search(r"State: UNFINISHED \(upload ([0-9a-f]+)\)", out)
        if code == 130 and match:
            upload_id = match.group(1)
            demo.check(True, "the backup stopped; state is UNFINISHED")
            code, out = demo.bv("list")
            demo.check("V1" in out and "V2" not in out, "list shows only V1")
            demo.bv("status", upload_id)
            code, out = demo.bv("restore", upload_id, os.path.join(work, "restored-unfinished"))
            demo.check(code == 1 and "VERSION_NOT_FOUND" in out, "the unfinished upload cannot be restored")
        else:
            upload_id = None
            print("    note: version 2 needed at most 1 new chunk, so there was nothing to interrupt", flush=True)

        step("5. Restart the server, run the same backup command again")
        demo.stop_server()
        demo.start_server()
        if upload_id:
            code, out = demo.bv("backup", v2)
            demo.check(code == 0 and "Version  : V2   State: COMPLETE" in out, "V2 is COMPLETE")
            demo.check(upload_id in out and "resumed" in out, "the client continued the same upload ID")
        code, out = demo.bv("list")
        demo.check("V1" in out and "V2" in out, "list shows V1 and V2 after the restart")

        step("6. Restore V1 and V2 into empty folders and compare with the sources")
        code, _ = demo.bv("restore", "V1", out1)
        demo.check(code == 0, "restore V1 succeeded")
        code, _ = demo.bv("restore", "V2", out2)
        demo.check(code == 0, "restore V2 succeeded")
        for label, source, restored in (("V1", v1, out1), ("V2", v2, out2)):
            diffs = compare(source, restored)
            for d in diffs[:10]:
                print(f"    {d}", flush=True)
            demo.check(not diffs, f"restored {label} equals its source (paths, types, bytes, file times within 1 s)")

        step("7. Verify, damage two stored chunks, verify again")
        code, _ = demo.bv("verify")
        demo.check(code == 0, "the healthy store verifies with exit code 0")
        files = demo.chunk_files()
        altered, deleted = files[0], files[-1]
        with open(altered, "r+b") as f:
            first = f.read(1)
            f.seek(0)
            f.write(bytes([first[0] ^ 0xFF]))
        os.remove(deleted)
        print(f"    altered one byte of chunk {os.path.basename(altered)}", flush=True)
        print(f"    deleted chunk            {os.path.basename(deleted)}", flush=True)
        with open(altered, "rb") as f:
            altered_bytes = f.read()
        code, out = demo.bv("verify")
        demo.check(code == 2, "verify exits with code 2")
        demo.check(os.path.basename(altered) in out and "CORRUPT" in out, "the altered chunk is reported CORRUPT")
        demo.check(os.path.basename(deleted) in out and "MISSING" in out, "the deleted chunk is reported MISSING")
        with open(altered, "rb") as f:
            demo.check(f.read() == altered_bytes and not os.path.exists(deleted), "verify repaired nothing")
        demo.bv("list")

        step("Result: every check passed" if not demo.failed else f"Result: {demo.failed} check(s) FAILED")
    finally:
        demo.stop_server()
        if args.keep:
            print(f"kept: {work}", flush=True)
        else:
            for _ in range(20):
                shutil.rmtree(work, ignore_errors=True)
                if not os.path.exists(work):
                    break
                time.sleep(0.25)
    return 1 if demo.failed else 0


if __name__ == "__main__":
    sys.exit(main())
