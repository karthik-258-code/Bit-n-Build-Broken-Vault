# Architecture Note

Short design note for BrokenVault. The full design is in [`LLD.md`](../LLD.md); requirements are in [`PRD_1.md`](../PRD_1.md).

## Main parts

**Client (`bv`, `src/brokenvault/client`)** scans the folder, splits files into chunks, builds the file list, asks the server what is missing, sends those chunks, asks for the commit, and restores versions. It keeps one small state file per folder and server with the open upload ID.

**Server (`bv-server`, `src/brokenvault/server`)** validates everything it receives, stores chunks, records uploads and versions, completes versions, serves chunks for restore, and runs `verify`. It never trusts a hash, size or path from the client.

They talk over HTTP/1.1 (JSON control messages, raw chunk bodies). Shared code (`common/`) holds path validation, manifest validation, hashing and error codes.

```
Client                                        Server
  cli.py        argparse, exit codes            http_app.py        router + thin handlers
  scanner.py    walk, stat, build manifest      upload_service.py  create, missing, put_chunk, commit
  chunker.py    fixed-size streaming chunks     version_service.py list, manifest of a version
  backup.py     BackupRunner, state file        verify_service.py  damage scan + impact
  restore.py    Restorer, hash check            chunk_store.py     chunk files, atomic put
  transport.py  HTTP, retries, error mapping    db.py              SQLite schema, transactions
  output.py     human and JSON output           faults.py          test-only crash points
```

**Stored data** lives in the server's data directory:

```
<data-dir>/
  meta.db            SQLite (WAL, synchronous=FULL): uploads, versions, entries, chunk references
  chunks/ab/ab12…    one file per distinct chunk hash, raw bytes only
  tmp/<uuid>.part    in-flight chunk writes, deleted at startup
```

## File list and chunks

- **Recorded for a version:** for every item its relative path (with `/`), type (`file` or `dir`), modification time in nanoseconds; for files also the size and the ordered list of chunk IDs with sizes. Every folder has an entry, including empty ones. Empty files have zero chunks. The chunk size is recorded too.
- **Splitting:** fixed-size chunks of 256 KiB (configurable with `--chunk-size`), read as a stream; only the last chunk of a file may be shorter. The same bytes always give the same chunks.
- **Chunk ID:** lowercase hex SHA-256 of the chunk's original bytes. The server recomputes it before a chunk becomes visible.
- **One copy per hash:** a chunk is stored as `chunks/<first 2 hex>/<hash>`. "The server has the chunk" means that file exists. A `PUT` for an existing file stores nothing and answers `200 stored:false`. New chunks are written to `tmp/`, hash-checked, fsynced, then atomically renamed, under a per-hash lock. An existing chunk file is never overwritten or deleted.
- **Paths:** one validator used by both sides rejects absolute paths, drive prefixes, `..`, `.`, empty segments, backslashes and duplicates. The server also checks that chunk sizes add up to the file size.

## Safe completion

- **Unfinished upload:** a row in the `uploads` table with state `OPEN`, holding the file list. Completed versions live in separate tables (`versions`, `entries`, `entry_chunks`).
- **What keeps it hidden:** `list` and `restore` read only the `versions` tables, and rows are written there only by commit. Commit first checks every distinct chunk the version needs: the file must exist, have the right size and hash to its ID (also for chunks reused from older versions). A missing chunk gives `INCOMPLETE_UPLOAD`, a damaged one gives `CORRUPT_CHUNK`; in both cases nothing changes and the upload stays `OPEN`. If all pass, one SQLite transaction inserts the version, its entries and chunk references and marks the upload `COMMITTED`. There is no state in between. Version IDs (`V1`, `V2`, …) are assigned in that transaction.

## Continue after a stop

- **Same upload:** the server keeps the upload ID in SQLite; the client keeps it in a state file written before any chunk is sent, prints it, and accepts `--resume <id>`. On the next run the client checks that the upload is still `OPEN` and describes the same file list (same manifest ID). If the state file is lost, creating an upload with an identical file list returns the existing open upload.
- **What is missing:** `GET /v1/uploads/{id}/missing` lists the chunks of that upload whose files are absent on disk. Only those are sent.
- **Repeated requests:** create-upload returns the same open upload; a repeated chunk is not stored or counted again; a repeated commit returns the same version ID.
- **Byte counting:** before the rename, the server commits a row `(upload, chunk, size)`. Uploaded chunk bytes are the sum of those rows, so each newly accepted chunk counts exactly once whatever the crash point.

| Crash point | State afterwards | On retry |
|---|---|---|
| Client dies mid-upload | Stored chunks; upload `OPEN` | Same command resumes; stored chunks are skipped |
| Server dies while writing a chunk | Orphan temp file, no chunk file | Temp cleaned at startup; chunk re-sent |
| Server dies after the count row, before the rename | Row, no chunk file | Chunk re-sent; row already exists, counted once |
| Server dies after the rename, before the reply | Chunk file and row | Retry gets `stored:false`; not counted again |
| Server dies inside the commit transaction | SQLite rolls back; no version | Upload still `OPEN`; commit retried |
| Server dies after commit, before the reply | Version exists | Repeated commit returns the same version ID |

## Restore and verification

- **Rebuilding:** the client fetches the version's file list, validates it again, and requires an empty or absent destination. It creates folders (parents first), then writes each file chunk by chunk in the recorded order. Each path is resolved and must stay inside the destination.
- **Hash checks:** on upload (server, before the rename), at commit (server, every needed chunk), on restore (client, every chunk's size and hash as it is read, then the final file size), and in `verify`. A bad or missing chunk stops the restore with the file path and chunk ID; it never reports success.
- **Modification times:** set on each file after writing, then on folders, deepest first, because writing a child changes its parent's time.
- **Damage report:** `verify` runs on the server. It checks every distinct chunk referenced by a completed version (exists, size, hash), marks it `MISSING` or `CORRUPT`, and uses an index on chunk ID to list every version and file path that uses it. It writes nothing and repairs nothing. Exit code 2 when damage is found.

## Important choices and limits

- **Fixed-size chunks** are simple and repeatable and reuse well after in-place edits and appends. Bytes inserted in the middle of a file shift later chunks, which are then sent again. Content-defined chunking is not implemented.
- **File existence as the source of truth** makes deleted or altered chunks visible to `verify` and later backups, at the cost of a file-system check per chunk.
- **Full re-hash at commit** costs one more read of the version's data, in exchange for the guarantee that a completed version passed a hash check. `--fast-commit` on the server skips re-hashing chunks received by the same upload.
- **No repair, no deletion:** a backup that needs a damaged stored chunk is refused until the operator deals with that file. There is no garbage collection; chunks of abandoned uploads stay.
- **Sequential uploads, one backup at a time, one user, no authentication.**
- **Standard library only** (Python `http.server`, `sqlite3`): nothing to install, but the server is not built for many clients.
- A failed restore leaves a partial destination. Symlinks and special files are skipped; permissions are not saved.
- Test hooks, off by default: server variable `BV_FAULT` (four crash points) and client flag `--stop-after-chunks N`.
