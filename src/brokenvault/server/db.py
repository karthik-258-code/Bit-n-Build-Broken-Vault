"""SQLite metadata store (LLD 5.4): one connection guarded by a lock."""

import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
  upload_id      TEXT PRIMARY KEY,
  manifest_id    TEXT NOT NULL,
  manifest_json  TEXT NOT NULL,
  state          TEXT NOT NULL CHECK (state IN ('OPEN','COMMITTED','ABORTED')),
  total_bytes    INTEGER NOT NULL,
  created_at     INTEGER NOT NULL,
  version_id     INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS uploads_open_manifest
  ON uploads(manifest_id) WHERE state = 'OPEN';

CREATE TABLE IF NOT EXISTS upload_chunks (
  upload_id TEXT NOT NULL REFERENCES uploads(upload_id),
  chunk_id  TEXT NOT NULL,
  size      INTEGER NOT NULL,
  PRIMARY KEY (upload_id, chunk_id)
);

CREATE TABLE IF NOT EXISTS versions (
  version_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  upload_id      TEXT NOT NULL UNIQUE REFERENCES uploads(upload_id),
  created_at     INTEGER NOT NULL,
  root_name      TEXT NOT NULL,
  chunker_size   INTEGER NOT NULL,
  total_bytes    INTEGER NOT NULL,
  uploaded_bytes INTEGER NOT NULL,
  reused_bytes   INTEGER NOT NULL,
  file_count     INTEGER NOT NULL,
  dir_count      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
  version_id INTEGER NOT NULL REFERENCES versions(version_id),
  path       TEXT    NOT NULL,
  type       TEXT    NOT NULL CHECK (type IN ('file','dir')),
  size       INTEGER,
  mtime_ns   INTEGER NOT NULL,
  PRIMARY KEY (version_id, path)
);

CREATE TABLE IF NOT EXISTS entry_chunks (
  version_id INTEGER NOT NULL,
  path       TEXT    NOT NULL,
  idx        INTEGER NOT NULL,
  chunk_id   TEXT    NOT NULL,
  size       INTEGER NOT NULL,
  PRIMARY KEY (version_id, path, idx),
  FOREIGN KEY (version_id, path) REFERENCES entries(version_id, path)
);
CREATE INDEX IF NOT EXISTS entry_chunks_by_chunk ON entry_chunks(chunk_id);
"""


class Database:
    def __init__(self, path):
        self._lock = threading.RLock()
        # isolation_level=None: transactions are controlled explicitly in tx()
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = FULL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        """One write transaction: BEGIN IMMEDIATE ... COMMIT, or ROLLBACK on any error."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def query(self, sql, params=()):
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql, params=()):
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def close(self):
        with self._lock:
            self._conn.close()
