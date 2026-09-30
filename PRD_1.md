# BrokenVault — Product Requirements Document

| | |
|---|---|
| **Product** | BrokenVault — deduplicating, resumable, verifiable folder backup |
| **Source** | Participant Problem Statement, Version 2.2 (202601) |
| **Document version** | 1.0 |
| **Date** | 2026-09-30 |
| **Format** | One-day build, CLI-first, client–server |

---

## 1. Summary

BrokenVault is a client–server backup system for a single folder tree. The client splits files into content-addressed chunks (SHA-256), asks the server which chunks it lacks, and uploads only those. A backup becomes a **completed version** only after every chunk it needs is present and hash-checked. An interrupted upload can be continued later without resending verified data. Any completed version can be restored byte-for-byte, and a `verify` command finds damaged stored chunks and names every version and file they affect.

The one-sentence success test from the brief:

> Less data is sent, interrupted work continues, unfinished versions stay hidden, and completed versions restore exactly.

## 2. Problem and goals

### 2.1 Problem
Most backups repeat data saved before. Re-sending it wastes time and storage. Uploads that stop midway are either restarted from zero or, worse, appear as complete backups that cannot be restored.

### 2.2 Goals

| ID | Goal |
|---|---|
| G1 | Send only data the server does not already have (chunk-level reuse across files and versions). |
| G2 | Never show a half-finished backup as complete; never restore one. |
| G3 | Continue an interrupted upload from server-verified progress after a client, server or connection stop. |
| G4 | Restore any completed version exactly (bytes, relative paths, empty files, empty folders, modification times). |
| G5 | Detect damaged or missing stored data and report the exact impact (versions and file paths). |
| G6 | Be easy for a judge to run, repeat and inspect (README, tests, clear numbers). |

### 2.3 Non-goals
Multi-user support, login/authentication, concurrent backups, deletion, garbage collection, partial restore, symlinks/sparse files/devices/ACLs/OS attributes, file permissions, encryption, cloud deployment/billing/replication, and a graphical interface. Some of these may appear as stretch features (section 13) only after the core is complete.

## 3. Users and context

| Actor | Description | Needs |
|---|---|---|
| **Operator** | The single user of the single backup store. Runs the client and server, typically on one laptop. | Back up a folder, list versions, restore, verify, see byte counts. |
| **Judge** | Evaluates the submission from the repo/ZIP, using a judge dataset or the team's sample. | Clean setup from README, repeatable demo, automated tests, clear errors and numbers. |

Environment assumptions: one operating system for backup and restore, client and server are separate programs (may share a machine, loopback allowed), datasets of 250 MB–1 GB, source files do not change during a backup, test names do not differ only by letter case.

## 4. Scope

| In scope (required) | Out of scope |
|---|---|
| Regular files, nested folders, empty files, empty folders | Symlinks, sparse files, devices, ACLs, OS-specific attributes |
| Separate client and server programs over HTTP/gRPC/TCP or similar | Shared database as the only channel between client and server |
| Backup, list, restore, verify actions | Login/authentication, multiple users |
| Chunk dedup, resume, atomic completion, exact restore, damage report | Cloud, billing, replication, multi-region |
| CLI (documented) | Web/desktop UI (optional) |

## 5. Definitions (normative)

| Term | Meaning |
|---|---|
| **Chunk** | A part of a file. Chunk ID = lowercase hex SHA-256 of its original bytes. The server recomputes the hash before saving. |
| **File list (manifest)** | Full description of one version: safe relative paths, item type, file size, modification time, and each file's ordered chunk IDs. |
| **Unfinished version** | An upload still missing data. May continue later. Not in the normal version list. Cannot be restored. |
| **Completed version** | Shown as complete only after all chunks exist and pass a hash check. Must survive a server restart. |
| **Resume** | Client returns to the same unfinished upload and asks what is still missing. Re-sending a chunk is safe and creates no second copy. |
| **Exact restore** | File bytes, relative paths, empty items and modification times match the saved version (±1 s allowed if the file system requires it). |
| **Uploaded chunk bytes** | Original bytes of newly accepted chunks only. Headers, manifest and compression are not counted. |
| **Reused bytes** | Logical folder bytes that did not need to be sent (total folder bytes − uploaded chunk bytes). |

## 6. Functional requirements

### FR-1 Read and split

The client scans the whole source folder and builds a manifest.

- FR-1.1 Record for every item: relative path (using `/`), type (`file` or `dir`), size (files), modification time, and the ordered list of chunks (ID + size) for files.
- FR-1.2 Regular files, nested folders, empty files and empty folders are all represented. **Empty files have zero chunks.**
- FR-1.3 Chunking is repeatable: the same bytes always yield the same chunk IDs and boundaries. Any repeatable method is valid; 256 KiB–1 MiB fixed-size is the suggested default.
- FR-1.4 Chunk IDs are SHA-256 of the chunk's original bytes, lowercase hex.
- FR-1.5 Path safety: reject absolute paths, `..` escapes and duplicate paths (client on scan, server on receipt).
- FR-1.6 Unsupported items (symlinks, devices, etc.) are skipped or rejected with a clear message; they are never silently followed.

**Acceptance criteria**
- AC-1.1 Scanning a folder with nested paths, an empty file and an empty folder produces a manifest containing all of them.
- AC-1.2 Scanning the same folder twice yields an identical manifest chunk list.
- AC-1.3 A path such as `../x`, `/etc/x` or a duplicate is rejected with a useful error.

### FR-2 Check and skip

- FR-2.1 The client asks the server which chunks it already has and sends only missing ones.
- FR-2.2 The server stores each distinct chunk hash exactly once.
- FR-2.3 The server validates every received chunk (recomputes SHA-256, compares to the declared ID) before it becomes visible.
- FR-2.4 Identical chunks inside one version (e.g. two identical files) are sent at most once.

**Acceptance criteria**
- AC-2.1 Backing up a folder, changing a small part of one large file, adding a file, and backing up again sends fewer bytes than the folder's total size, and only chunks not already stored.
- AC-2.2 Backing up an unchanged folder again sends 0 chunk bytes and still yields a new completed version.
- AC-2.3 A chunk whose bytes do not match its declared ID is rejected and not stored.
- AC-2.4 Storage contains one copy per distinct hash (checked by counting stored chunk files).

### FR-3 Save only when complete

- FR-3.1 A version stays unfinished until every chunk in its manifest exists on the server and passes a hash check.
- FR-3.2 The change from unfinished to completed is a single atomic step. There is no observable intermediate state.
- FR-3.3 Unfinished uploads never appear in `list`, cannot be restored, and are not counted as versions.
- FR-3.4 A commit attempt on an incomplete upload fails with a message listing what is missing; the upload stays unfinished and resumable.
- FR-3.5 If a stored chunk the version needs is damaged (wrong size or hash), commit is refused and names the chunk; nothing is deleted or repaired automatically.

**Acceptance criteria**
- AC-3.1 Stop a backup before completion: `list` shows no new version.
- AC-3.2 Attempting to restore an unfinished upload fails with a clear error.
- AC-3.3 Commit with any missing chunk is refused; the version list is unchanged.
- AC-3.4 After a server restart, completed versions are still listed and restorable.
- AC-3.5 With a damaged stored chunk needed by a new backup, commit fails naming the chunk, the chunk file is unchanged, and no version is added.

### FR-4 Continue safely

- FR-4.1 After a client, server or connection stop, the client returns to the **same** upload (same upload ID) and asks what is still missing.
- FR-4.2 Chunks the server already verified are not sent again.
- FR-4.3 The upload ID survives restarts of both programs: the server keeps it in its database and the client keeps it in a local state file, so the client can return to the same upload by ID. The format of upload and version IDs is the server's choice.
- FR-4.4 Repeated requests are safe: re-sending a chunk, re-issuing create-upload for the same content, and re-issuing commit all produce the same end state without duplicates.
- FR-4.5 Transient network errors are retried with a bounded backoff; if retries are exhausted the client exits with a message that says how to resume.

**Acceptance criteria**
- AC-4.1 Interrupt a backup part-way, restart both client and server, run backup again: the client returns to the same upload ID it stored, previously stored chunks are not re-sent, and the version completes.
- AC-4.2 Uploaded chunk bytes across the interrupted + resumed runs equal the bytes of distinct missing chunks (no double counting, no double sending of verified chunks).
- AC-4.3 Sending the same chunk twice leaves one stored copy and does not inflate uploaded-byte counters.
- AC-4.4 Issuing commit twice returns the same version ID.

### FR-5 Restore exactly

- FR-5.1 Restore any completed version into an empty (or non-existent) destination folder; refuse a non-empty destination.
- FR-5.2 Recreate paths, empty folders, empty files, file bytes and modification times.
- FR-5.3 Verify each chunk's hash while reading it; abort with the file path and chunk ID on mismatch or absence.
- FR-5.4 After restore, the restored file size must equal the manifest size.

**Acceptance criteria**
- AC-5.1 A recursive comparison of the original folder and the restored folder shows identical relative paths, item types, file bytes; modification times within 1 s.
- AC-5.2 Restoring a version whose chunk has been altered fails loudly, naming the file, and never reports success.
- AC-5.3 Restoring V1 after V2 exists returns V1's exact state (versions are independent snapshots).

### FR-6 Find damaged data

- FR-6.1 `verify` checks every chunk referenced by any completed version: exists, correct size, hash equals its ID.
- FR-6.2 For each missing or changed chunk, list **every completed version and every file path** that depends on it.
- FR-6.3 Verify does not repair anything automatically.
- FR-6.4 Exit status is non-zero when damage is found, zero when everything is healthy.

**Acceptance criteria**
- AC-6.1 Alter one stored chunk byte: verify reports that chunk as CORRUPT and lists each affected version and file.
- AC-6.2 Delete one stored chunk file: verify reports it as MISSING with the same impact listing.
- AC-6.3 A chunk shared by two versions reports both versions.
- AC-6.4 A healthy store reports OK and exits 0.

## 7. Interface requirements

A documented CLI fully meets the requirement. Four actions are required.

| Action | Purpose | Required output |
|---|---|---|
| `backup <folder>` | Create a version, or continue this folder's unfinished upload by its stored upload ID (`--resume <id>` to name it explicitly) | Version ID (or upload ID while unfinished), state, total folder bytes, uploaded chunk bytes, reused bytes, progress |
| `list` | Show completed versions only | Version ID, state, creation time, file count, total folder bytes, uploaded chunk bytes, reused bytes |
| `restore <version> <dest>` | Restore exactly | Version ID, files/bytes restored, result |
| `verify` | Check all chunks of all completed versions | Counts checked; for each damaged chunk: reason, affected versions and file paths |
| `status <upload-id>` (optional) | Show an unfinished upload | Upload ID, current state (UNFINISHED or COMPLETE), chunks stored / needed, uploaded chunk bytes |

Interface rules:
- IR-1 Every command prints useful errors (what failed, on which path/chunk/version, and what to do next). No raw stack traces for expected failures.
- IR-2 Exit codes: `0` success, `1` usage/input error, `2` damage found (verify) or integrity failure, `3` network/server unavailable (resumable).
- IR-3 `backup` shows progress (chunks or bytes sent / needed) and, on interruption, prints the resume hint.
- IR-4 The summary line shows: version/upload ID, state, total folder bytes, uploaded chunk bytes, reused bytes.
- IR-5 A JSON output flag (`--json`) is recommended so tests and judges can parse numbers.

## 8. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 Correctness | Correctness of restore and completion outranks speed and features. |
| NFR-2 Durability | A completed version and all its chunks survive a server crash/restart after completion is acknowledged (fsync of chunk data and metadata commit). |
| NFR-3 Atomicity | Chunk visibility and version completion are atomic; no partially written chunk is ever readable as a chunk. |
| NFR-4 Idempotency | Every write request (create upload, put chunk, commit) is safe to repeat. |
| NFR-5 Scale | Handles datasets of 250 MB–1 GB on a laptop with bounded memory (streaming; no whole-file buffering). |
| NFR-6 Safety | Path traversal is impossible on both server (manifest validation) and client (restore path check). Server never trusts client-declared hashes or sizes. |
| NFR-7 Portability | Runs on one documented OS or in Docker; core demo needs no internet or paid service after setup. |
| NFR-8 Repeatability | Tests and demo are runnable by judges from README commands. |
| NFR-9 Maintainability | Small modules with clear responsibilities; documented design note. |

## 9. Constraints and compliance

**Allowed:** standard libraries for hashing, networking, databases, compression; frameworks; SQLite or other DB; containers; test tools; any language judges can run from the README; AI tools **if listed in README**.

**Not allowed:** Git, restic, Borg, Kopia or another backup engine as the storage system; hard-coding the example or judge dataset; code the team lacks permission to use; hiding required behavior behind an unavailable external service; a shared database file as the only client–server link.

**Compression** gives no scoring shortcut (uploaded chunk bytes are measured on original bytes).

## 10. Submission requirements

| Item | Requirement |
|---|---|
| Repository | Public GitHub repository, opens without approval |
| Tag | `submission-final` on the exact submitted commit |
| Form | Repo URL + full commit hash before the deadline on the official form |
| ZIP | `<team-name>-brokenvault.zip` made from the same commit |
| Contents | Source for client and server; automated tests (backup, reuse, restart, restore, damage); `README.md`; dependency file + lock file where supported; `docs/architecture.md` (or equivalent) |
| README must include | Team name and members; one supported OS or Docker setup; prerequisites, install steps, one command to start the system; example commands for backup, list, restore, verify; one command to run tests; known limits; external libraries/services and AI-assisted work used |
| Must not include | Secrets, API keys, `node_modules`/`.venv`, generated backup data, unrelated binaries |
| Valid submission | Repo public, tag and commit match, ZIP extracts, README works from a clean setup |
| Freeze | Judges use only the tagged commit; later changes are ignored. The ZIP is a safety copy of exactly that commit. |
| Optional | A license. Large demo datasets are not required. |

## 11. Evaluation mapping

| Criterion | Weight | Requirements | Evidence to prepare |
|---|---|---|---|
| Correct backup and exact restore | 25% | FR-1, FR-5 | Restore-compare test; demo diff of folders |
| Reuse and transfer efficiency | 20% | FR-2 | Byte counters V1 vs V2; store chunk count |
| Continue and safe completion | 20% | FR-3, FR-4 | Kill/restart test; clean `list` during upload |
| Damage detection | 12% | FR-6 | Corrupt/delete chunk test; impact report |
| Design and code quality | 8% | NFR-3, NFR-6, NFR-9 | Architecture note; small modules |
| Interface and error messages | 7% | Section 7 | Clear output and errors |
| Tests, README and demo | 8% | Sections 10, 12 | One-command tests; repeatable demo |

**Tie-break order:** safe continuation → correct restore → uploaded chunk bytes → code quality. This means the resume path and its accounting deserve extra testing, and chunk-level reuse should be tight (avoid needlessly large chunks).

## 12. Demo script (4–6 minutes)

Flow (the brief's order): **backup → change → interrupt → restart → restore → damage + verify.** Use a judge-provided dataset or a small sample of your own; do not rely on the example folder names.

1. Start the server. Show the dataset: nested folders, an empty file, an empty folder. Keep an untouched copy of it for the later comparison.
2. **Backup:** `backup` completes V1. Show the numbers (uploaded bytes equal the total on a first backup, reused bytes shown) and `list`.
3. **Change:** modify a small part of a large file and add a new file. Keep a copy of this second state too.
4. **Interrupt:** start `backup` for V2 with a deliberate stop part-way (test hook). Show that `list` still shows only V1 and that the upload reports state UNFINISHED.
5. **Restart:** restart the server and the client; run `backup` again. It continues the same upload, skips stored chunks, and V2 appears. Show uploaded chunk bytes far below the total folder bytes.
6. **Restore:** restore V1 and V2 into empty folders and compare each with its saved original; they match.
7. **Damage + verify:** alter one stored chunk and delete another; run `verify`; show every affected version and file path. Show that nothing was repaired.

The brief's guided example interrupts the first backup instead of the second. Both variants are covered by automated tests (T5, T8).

## 13. Stretch features (only after all six core features pass)

In rough priority order:
1. Content-defined (insertion-friendly) chunking to keep reuse after inserts in the middle of files.
2. Parallel chunk uploads with bounded concurrency.
3. `--json` machine-readable output on all commands (recommended earlier as IR-5).
4. Docker Compose for one-command start.
5. Simple web status page.
6. Partial restore of a path prefix.
7. Delete version + garbage collection of unreferenced chunks.
8. Optional compression at rest (no scoring benefit).

## 14. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Double counting bytes on resume | Wrong tie-break metric | Server records accepted chunk sizes per upload; counters derive from that record |
| Half-written chunk visible after crash | Silent corruption | Write to temp file, hash, fsync, atomic rename |
| Commit succeeds partially | Broken "complete" version | Single database transaction; versions table only written at commit |
| Path traversal in manifest or restore | Security failure | Central path validator used by client and server; restore re-checks resolved path |
| Resume lost after restart | Fails core criterion | Upload ID persisted server-side (database) and client-side (state file); client resumes by ID |
| Timezone/mtime rounding | Restore mismatch | Store integer nanoseconds; compare with 1 s tolerance |
| Scope creep | Core incomplete | Stretch work gated by core checklist |
| Large dataset slows demo | Demo overruns | Demo on a small dataset; keep 1 GB test for the test suite as optional |
| Judge dataset edits insert bytes mid-file | Fixed-size chunks lose alignment after the insert, so more bytes are uploaded (a tie-break metric) | Content-defined chunking is the first stretch item; decide after the core passes, and test with both in-place and inserted edits |

## 15. Interpretation of the brief

Each row states the brief's wording first, then the choice that follows it.

| # | Brief says | Choice |
|---|---|---|
| A1 | "Any repeatable chunking method is valid. A size of 256 KiB to 1 MiB is a suggestion, not a rule." | Fixed-size chunks of 512 KiB, inside the suggested range; size is configurable and recorded in the file list. |
| A2 | "The server may choose the format of upload and version IDs." | Version IDs are sequential (`V1`, `V2`, …), assigned at completion, so unfinished uploads consume none. |
| A3 | "The upload ID must survive a restart so the client can continue the same upload." / "The client returns to the same unfinished upload and asks what is still missing." | Resume is by upload ID. The server stores it in its database; the client stores it in a local state file keyed by folder and server, prints it, and accepts `--resume <id>`. As a safety net, create-upload with an identical file list while an open upload exists returns that upload, so a crash between the server's reply and the client's save loses nothing. |
| A4 | Reports "uploaded chunk bytes" (original bytes of newly accepted chunks) and "reused bytes" without defining the latter. | `reused bytes = total folder bytes − uploaded chunk bytes` (includes duplicates within the same version). |
| A5 | "Restore any completed version into an empty folder." | Restore targets an empty folder. A missing destination is created; a non-empty one is refused. |
| A6 | "The verify command must name each completed version and file that depends on" a damaged chunk. | `verify` runs on the server side of the connection (chunks are local to the server) and is invoked from the client CLI. |
| A7 | Modification times must match; "a one-second time difference is allowed if the file system needs it". | Modification time is stored as integer nanoseconds and restored with `utime`; directory mtimes are set after their contents are written. |
| A8 | Symlinks, sparse files, devices, ACLs and OS-specific attributes are out of scope. | Symlinks and other special files are skipped with a warning. |
| A9 | Each backup run "starts a version" (V1, V2, …). | Backing up an unchanged folder still creates a new version (a snapshot), sending 0 chunk bytes. |

## 16. Delivery plan (one day)

| Block | Work | Exit check |
|---|---|---|
| 1 | Repo skeleton, shared modules (hashing, path validation, manifest), scanner + chunker | AC-1.x pass |
| 2 | Server: chunk store, SQLite schema, create-upload, missing, put-chunk | AC-2.x pass |
| 3 | Commit, list, atomic completion | AC-3.x pass |
| 4 | Client backup flow with resume and retries | AC-4.x pass |
| 5 | Restore | AC-5.x pass |
| 6 | Verify | AC-6.x pass |
| 7 | Automated tests, README, architecture note, demo rehearsal, tag `submission-final`, ZIP | Clean-setup run from README |

## 17. Traceability matrix

| Brief statement | Requirement | Verified by |
|---|---|---|
| Read and split | FR-1 | Unit tests: scanner, chunker, path validation |
| Check and skip | FR-2 | Integration: V1 then V2, byte counters, store file count |
| Save only when complete | FR-3 | Integration: stop before commit; list clean; commit refused |
| Continue safely | FR-4 | Integration: kill server/client mid-upload, restart, resume, request counts |
| Restore exactly | FR-5 | Integration: tree comparison, tampered chunk |
| Find damaged data | FR-6 | Integration: corrupt/delete chunk, impact report, exit code |
| Minimum usable interface | Section 7 | CLI tests on output fields and exit codes |
| Submission | Section 10 | Clean-clone checklist |

## 18. Definition of done

- All acceptance criteria AC-1.1 … AC-6.4 pass in automated tests run by a single command.
- The demo script runs end-to-end from a clean clone following only the README.
- README, `docs/architecture.md`, dependency and lock files are present; no secrets or generated data in the repo.
- Tag `submission-final` points at the submitted commit; ZIP is built from that commit.

## Appendix A. Coverage of the problem statement

Every requirement in the brief, page by page, and where this document (or the LLD) handles it.

| Brief page and section | Requirement | Covered in |
|---|---|---|
| 1 Mission, Situation | Client–server app; send only data the server lacks; continue after interruption; restore every completed version exactly; half-finished backup never shown as complete | §1, §2.2, FR-1 to FR-6 |
| 1 Reference scenario | Version B reuses A's data, sends only missing parts, shows complete only after all data arrived and passed a check | FR-2, FR-3, §12 |
| 1 Scope contract | One user and one store, no login; separate client and server; files, nested folders, empty files, empty folders; files stable during backup; symlinks/sparse/devices/ACLs/OS attributes and cloud out of scope; CLI enough; 250 MB–1 GB datasets | §3, §4, §2.3, NFR-5 |
| 2 Guided example | Hidden until complete; stop and continue without resending; V2 sends only new data; V1 unchanged; restore V1 and V2 into empty folders and compare; damage a stored part; verify names each version and file; no automatic repair | §12, FR-3 to FR-6, AC-6.x; LLD T5, T8, T11, T13 |
| 3 Definitions | Chunk, file list, unfinished version, completed version, resume, exact restore (1 s mtime tolerance) | §5, FR-5, A7 |
| 3 More rules: paths | Relative, `/` separator; reject absolute, `..`, duplicates | FR-1.5, NFR-6; LLD 4.2 |
| 3 More rules: chunking | Any repeatable method; 256 KiB–1 MiB suggested; empty files have no chunks; same hash stored once; insertion-friendly chunking is a stretch | FR-1.2, FR-1.3, FR-2.2, A1, §13 |
| 3 More rules: transport | HTTP/gRPC/TCP or similar; loopback allowed; shared database alone not allowed | §4, §9; LLD 5.8 |
| 3 More rules: run conditions | One backup at a time; same OS for backup and restore; no case-only name clashes | §3 |
| 3 More rules: IDs and counters | Upload ID survives restart; server chooses ID formats; uploaded chunk bytes count original bytes of newly accepted chunks only; compression gives no shortcut | FR-4.3, A3, §5, §9; LLD 5.5 |
| 4 Six capabilities | Read and split; check and skip; save only when complete; continue safely; restore exactly; find damaged data, each with its "Show" demo | FR-1 to FR-6, AC-1.1 to AC-6.4, §12 |
| 4 Minimum usable interface | Backup, list, restore, verify; show version ID, current state, total folder bytes, uploaded chunk bytes, reused bytes, useful errors; documented CLI suffices | §7, IR-1 to IR-5 |
| 5 Submission | Public repo; tag `submission-final`; repo URL, full commit hash and ZIP named `<team-name>-brokenvault.zip` before the deadline; judges use only that commit | §10 |
| 5 Repository and ZIP contents | Source, automated tests (backup, reuse, restart, restore, damage), README, dependency and lock files, architecture note; license optional; no large datasets needed | §10; LLD §9, §10 |
| 5 README | Team and members; supported OS or Docker; prerequisites, install, one start command; example commands; test command; known limits; libraries, services and AI-assisted work | §10; LLD §10 |
| 5 Do not include | Secrets, dependency folders, generated backup data, unrelated binaries | §10, §18 |
| 5 Runtime rule, valid submission | No paid service or public internet for the core demo; repo opens, tag and commit match, ZIP extracts, README works clean | NFR-7, §10, §18 |
| 5 Live demo | 4–6 minutes: backup, change, interrupt, restart, restore, damage plus verify; judge dataset or own sample | §12 |
| 6 Rubric and tie-break | Seven weighted criteria; tie-break order safe continuation, restore, uploaded bytes, code quality | §11 |
| 6 Allowed, not allowed | Libraries, frameworks, databases, containers, AI tools listed in README; no Git/restic/Borg/Kopia as storage, no hard-coded datasets, no unlicensed code, no hidden external service | §9 |
| 6 Not required | Multiple users, concurrent backups, deletion, GC, partial restore, symlinks, permissions, encryption, cloud, GUI | §2.3, §13 |
