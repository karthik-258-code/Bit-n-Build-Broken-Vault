# BrokenVault build progress

Source documents: `LLD.md` (how), `PRD_1.md` (what). The original brief PDF is not in this folder.

## Checklist (from PRD Appendix A)

| Brief requirement | Covered by | Status |
|---|---|---|
| Client-server app, separate programs, HTTP on loopback | `brokenvault.server`, `brokenvault.client` | pending |
| Files, nested folders, empty files, empty folders | scanner, manifest, restore | pending |
| Paths relative with `/`; reject absolute, `..`, duplicates | `common/paths.py`, `common/manifest.py` | pending |
| Repeatable chunking, 512 KiB fixed, empty files have no chunks | `client/chunker.py` | pending |
| Same hash stored once; server recomputes hash | `server/chunk_store.py` | pending |
| Send only missing chunks | `missing` + `BackupRunner` | pending |
| Version complete only after all chunks exist and pass a hash check | `commit` | pending |
| Unfinished uploads hidden from list and restore | separate `uploads` / `versions` tables | pending |
| Upload ID survives restart; resume does not resend | SQLite + client state file | pending |
| Uploaded chunk bytes count new chunks once | `upload_chunks` intent rows | pending |
| Exact restore (bytes, paths, empty items, mtimes within 1 s) | `client/restore.py` | pending |
| Verify names every version and file for a damaged chunk; no repair | `server/verify_service.py` | pending |
| Interface: backup, list, restore, verify; IDs, state, byte counters, useful errors | `client/cli.py`, `client/output.py` | pending |
| Automated tests: backup, reuse, restart, restore, damage | `tests/` (T1 to T19) | pending |
| README, architecture note, dependency and lock files | block 7 | pending |
| No secrets, dependency folders or generated data in the repo | `.gitignore` | done |

## Build blocks (PRD section 16)

- [x] 1. common/, scanner, chunker, unit tests (AC-1.x)
- [ ] 2. Server: chunk store, DB, create upload, missing, put chunk (AC-2.x)
- [ ] 3. Commit, list, atomic completion (AC-3.x)
- [ ] 4. Client backup flow with retries and resume (AC-4.x)
- [ ] 5. Restore (AC-5.x)
- [ ] 6. Verify (AC-6.x)
- [ ] 7. README, docs/architecture.md, demo script, dependency and lock files, full test run

## Decisions made during the build

- The PRD file in this folder is named `PRD_1.md`; it is used as "PRD.md".
- The repository root is this folder (LLD section 3 calls it `brokenvault/`).
- The manifest is handled as a validated plain dict; a small dataclass (`ManifestInfo`) carries the derived values.
- Scanner uses `os.lstat()` instead of `DirEntry.stat()`: on Windows the cached directory-listing mtime can be stale, which made two scans of the same folder differ (it would break resume by manifest ID).
