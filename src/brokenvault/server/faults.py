"""Test-only fault injection (LLD 9.4). Off unless BV_FAULT is set.

BV_FAULT values:
  crash_after_chunks=N     exit after N chunks were published in this process
  crash_before_rename      exit after the intent row, before the chunk rename
  crash_before_commit_tx   exit after commit verification, before the transaction
  crash_after_commit_tx    exit right after the commit transaction
"""

import os
import sys
import threading

POINTS = ("crash_after_chunks", "crash_before_rename", "crash_before_commit_tx", "crash_after_commit_tx")


class Faults:
    def __init__(self, spec=None):
        self.point = None
        self.limit = 0
        self._count = 0
        self._lock = threading.Lock()
        if spec:
            name, _, value = spec.partition("=")
            if name not in POINTS:
                raise ValueError(f"unknown BV_FAULT {spec!r}; expected one of {', '.join(POINTS)}")
            self.point = name
            self.limit = int(value) if value else 1

    def hit(self, point):
        """Crash the process if this fault point is armed."""
        if self.point == point:
            self._crash(point)

    def chunk_published(self):
        if self.point != "crash_after_chunks":
            return
        with self._lock:
            self._count += 1
            if self._count >= self.limit:
                self._crash(f"crash_after_chunks={self.limit}")

    @staticmethod
    def _crash(why):
        sys.stderr.write(f"BV_FAULT: simulated crash at {why}\n")
        sys.stderr.flush()
        os._exit(137)
