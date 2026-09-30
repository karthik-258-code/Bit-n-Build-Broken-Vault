# BrokenVault build progress

Source documents: `LLD.md` (how), `PRD_1.md` (what). The original brief PDF is not in this folder.

## Checklist (from PRD Appendix A)

| Brief requirement | Covered by | Status |
|---|---|---|
| Client-server app, separate programs, HTTP on loopback | `brokenvault.server`, `brokenvault.client` | done |
| Files, nested folders, empty files, empty folders | scanner, manifest, restore | done |
| Paths relative with `/`; reject absolute, `..`, duplicates | `common/paths.py`, `common/manifest.py` | done |
| Repeatable chunking, 512 KiB fixed, empty files have no chunks | `client/chunker.py` | done |
| Same hash stored once; server recomputes hash | `server/chunk_store.py` | done |
| Send only missing chunks | `missing` + `BackupRunner` | done |
| Version complete only after all chunks exist and pass a hash check | `commit` | done |
| Unfinished uploads hidden from list and restore | separate `uploads` / `versions` tables | done |
| Upload ID survives restart; resume does not resend | SQLite + client state file | done |
| Uploaded chunk bytes count new chunks once | `upload_chunks` intent rows | done |
| Exact restore (bytes, paths, empty items, mtimes within 1 s) | `client/restore.py` | done |
| Verify names every version and file for a damaged chunk; no repair | `server/verify_service.py` | done |
| Interface: backup, list, restore, verify; IDs, state, byte counters, useful errors | `client/cli.py`, `client/output.py` | done |
| Automated tests: backup, reuse, restart, restore, damage | `tests/` (T1 to T19) | done |
| README, architecture note, dependency and lock files | block 7 | done |
| No secrets, dependency folders or generated data in the repo | `.gitignore` | done |

## Build blocks (PRD section 16)

- [x] 1. common/, scanner, chunker, unit tests (AC-1.x)
- [x] 2. Server: chunk store, DB, create upload, missing, put chunk (AC-2.x)
- [x] 3. Commit, list, atomic completion (AC-3.x)
- [x] 4. Client backup flow with retries and resume (AC-4.x)
- [x] 5. Restore (AC-5.x)
- [x] 6. Verify (AC-6.x)
- [x] 7. README, docs/architecture.md, demo script, dependency and lock files, full test run

## Decisions made during the build

- The PRD file in this folder is named `PRD_1.md`; it is used as "PRD.md".
- The repository root is this folder (LLD section 3 calls it `brokenvault/`).
- The manifest is handled as a validated plain dict; a small dataclass (`ManifestInfo`) carries the derived values.
- Scanner uses `os.lstat()` instead of `DirEntry.stat()`: on Windows the cached directory-listing mtime can be stale, which made two scans of the same folder differ (it would break resume by manifest ID).
- Blocks 2 and 3 share one commit: `upload_service.py` holds put-chunk and commit together. Both blocks were tested over raw HTTP before the client existed (T4, T6, T9, T10, T17).
- `ChunkStore.publish()` checks for an existing file under the per-hash lock and discards the temp file instead of calling `os.replace` over it. This enforces "uploads never overwrite an existing chunk file" even for concurrent puts.
- Verify report adds `detail`, `affected` (version + path per damaged chunk) and `repaired: 0` to the LLD 5.7 shape. Upload status adds `chunks_needed` / `chunks_stored` for `bv status`.
- Extra transport codes beyond LLD section 7: `BAD_REQUEST`, `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `LENGTH_REQUIRED`, `PAYLOAD_TOO_LARGE`, `CLIENT_ABORTED`, `INTERNAL`, `RESTORE_CHUNK_INVALID`, `RESTORE_SIZE_MISMATCH`.
- Retries: one first try plus up to five retries with delays 0.2, 0.5, 1, 2, 4 s (LLD 6.4 says "5 attempts" and lists five delays). Test hook `BV_RETRY_DELAYS` shortens the delays; tests also set `BV_STATE_DIR` to isolate the client state file.
- After a server crash right after the commit transaction (`crash_after_commit_tx`), a rerun follows LLD 6.3 literally: the stored upload is COMMITTED, so the state file is dropped and a new snapshot (V2, 0 uploaded bytes) is created. No bytes are counted twice.
- Interrupted backup with `--json` prints `{"state": "UNFINISHED", "upload_id": ...}` on stdout; the resume hint goes to stderr.
- Blocks 4, 5 and 6 share one commit: the CLI, transport and their integration tests (T1 to T19) were written together after the server was complete.
- Lock file: `requirements-dev.lock` is a `pip freeze` of the test environment (pytest and its dependencies), made on Windows, so it lists `colorama`. `pip-compile` / `uv` were not installed.
- T20 measures client memory with `tracemalloc` (backup and restore run in-process). Server memory is not measured; the server streams 64 KiB blocks by design.
- The server swallows `ConnectionError` from clients that drop a connection instead of printing a stack trace.
- README team name and members are left as TODO; they are not in the design documents.
- Brief PDF (`Participants_guide/`) read after the build: no conflict with the PRD or LLD found. README and `docs/architecture.md` now follow the organisers' templates. `Participants_guide/` is git-ignored (70 MB of sample ZIPs).
- Organisers' sample check: V1 uploads 36,194,669 of 36,194,669 bytes; V2 uploads 524,347 of 36,194,728; both restores match their sources; verify healthy.
