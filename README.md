# BrokenVault

A client-server backup system for one folder tree. The client splits files into SHA-256 chunks, asks the server which chunks it lacks, and sends only those. A backup becomes a completed version only after every chunk exists on the server and passes a hash check. An interrupted upload continues later without resending stored data. Any completed version restores byte for byte, and `verify` finds damaged chunks and names every version and file they affect.

## Team

- **Team name:** _TODO: fill in before submission_
- Member 1: _TODO_
- Member 2: _TODO_
- Member 3: _TODO_
- Member 4: _TODO_

## Supported setup

- **Operating system:** Windows 11 (tested).
- **Language:** Python 3.11 or newer.
- **Required tools:** Python with `pip`; `pytest` for the tests. No Docker needed.
- The code uses only portable standard-library calls and POSIX-specific steps (directory fsync) are guarded, so Linux and macOS are expected to work, but they were not tested.
- Client and server are separate programs that talk over HTTP (loopback by default). No internet or paid service is needed after setup.

## Install

The client and server need nothing beyond the standard library; `pytest` is only for the tests.

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -e .                  # registers the package and the `bv` / `bv-server` commands
pip install -r requirements-dev.lock   # pytest, pinned (only needed for the tests)
```

Without `pip install -e .`, set `PYTHONPATH=src` and use the `python -m ...` forms below.

## Start the complete system

```bash
python -m brokenvault.server --data-dir ./vault --port 8765
```

The server is the only long-running part. Run the client commands below in a second terminal.

## Commands

Back up a folder, list completed versions, restore a version, verify stored data:

```
python -m brokenvault.server --data-dir ./vault --port 8765   # start the system   (make server)
python -m brokenvault.client backup  ./sample                 # or: bv backup ./sample
python -m brokenvault.client list
python -m brokenvault.client restore V1 ./restored-v1
python -m brokenvault.client verify
pytest -q                                                      # run all tests      (make test)
```

| Command | What it does |
|---|---|
| `bv-server --data-dir DIR [--host H] [--port P] [--fast-commit] [--verbose]` | Start the server. Data lives in `DIR` (`meta.db`, `chunks/`, `tmp/`). |
| `bv backup <folder> [--chunk-size BYTES] [--resume UPLOAD_ID]` | Create a version, or continue this folder's unfinished upload. |
| `bv list` | Show completed versions only. |
| `bv restore <version> <dest>` | Restore a version into an empty or not yet existing folder. |
| `bv verify` | Check every chunk of every completed version. Repairs nothing. |
| `bv status <upload-id>` | Show an upload: UNFINISHED or COMPLETE, chunks stored / needed. |

Every client command accepts `--server URL` (default `http://127.0.0.1:8765`, or the `BV_SERVER` variable) and `--json` for machine-readable output.

### Example

```
$ bv backup ./event-project
Upload   : 8fd14ec2a5d04c85813a5df600f3934a  (new)
Sending  : 5/5 chunks  [##############]  2.0 MiB / 2.0 MiB
Version  : V2   State: COMPLETE
Upload   : 8fd14ec2a5d04c85813a5df600f3934a  (new; sent 5 of 21 distinct chunks in this run)
Files    : 6 files, 4 folders
Total folder bytes    :       9,975,620
Uploaded chunk bytes  :       2,097,929
Reused bytes          :       7,877,691

$ bv list
VERSION  STATE     CREATED               FILES     TOTAL BYTES  UPLOADED BYTES    REUSED BYTES
V1       COMPLETE  2026-09-30 12:38:55       5       8,401,979       7,352,903       1,049,076
V2       COMPLETE  2026-09-30 12:39:00       6       9,975,620       2,097,929       7,877,691

$ bv verify
Checked 2 versions, 22 chunks.
DAMAGED  chunk 08ab718b…  CORRUPT (hash mismatch)
  V2  art/logo.bin
DAMAGED  chunk fe3feb06…  MISSING (chunk file is absent)
  V1  video/intro.bin
V1: DAMAGED (1 file)   V2: DAMAGED (1 file)
2 damaged chunk(s) found. No repairs were made.
```

- **Uploaded chunk bytes**: original bytes of chunks the server newly accepted for this version, counted once, across all runs of the same upload.
- **Reused bytes**: total folder bytes minus uploaded chunk bytes (includes duplicates inside the same version).

### Interrupt and resume

If a backup stops (Ctrl-C, server down, network error), the client prints the upload ID and state `UNFINISHED`. Nothing appears in `bv list`. Run the same `bv backup <folder>` command again: the client reads the upload ID from its state file (`~/.brokenvault/uploads/`, or `BV_STATE_DIR`), asks the server what is still missing, and sends only that. `--resume <upload-id>` names the upload explicitly. If the state file is lost, the server returns the same open upload for an identical file list.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Usage or input error (unknown version, non-empty restore destination, unsafe path, unknown upload) |
| 2 | Damage found by `verify`, or an integrity failure (damaged chunk during restore or commit, source changed) |
| 3 | Server or network unavailable after retries; the upload can be resumed |
| 130 | Interrupted; the upload can be resumed |

Errors say what failed, on which object, and what to do next:

```
error [CORRUPT_CHUNK]: backup of './b' cannot complete: stored chunk(s) 3fa9… on the server are damaged. Upload 9c2e… stays UNFINISHED and no version was added
  next: run `bv verify` to see every version and file affected; nothing was repaired or deleted
```

## Run tests

```bash
pytest -q
```

One command runs the unit tests and the integration scenarios T1 to T19 from `LLD.md` section 9.3. Integration tests start the real server as a subprocess on a random loopback port with a temporary data directory and drive the real CLI. They cover backup, reuse, atomic completion, restart, interrupt and resume, four server crash points, exact restore, and damage detection. The run takes about 2.5 minutes on Windows.

The 1 GB scenario (T20) is excluded by default: `pytest -q -m slow` (set `BV_SLOW_MB=256` for a smaller dataset).

## Demo steps

Automated, on a small generated dataset, with a check after each step (`--keep` leaves `./demo-work` for inspection):

```bash
python scripts/demo.py
```

By hand, with the organisers' sample (`brokenvault_sample_v1.zip` and `brokenvault_sample_v2.zip`, each extracted into its own folder) or any two states of a folder:

1. Start the server. Back up version 1: `bv backup ./brokenvault_sample_v1`. Uploaded bytes equal the total.
2. Back up version 2 and show reused and uploaded bytes: `bv backup ./brokenvault_sample_v2`, then `bv list`. On the sample, V2 has 36,194,728 total bytes and uploads 524,347.
3. Interrupt another upload: `bv backup <changed folder> --stop-after-chunks 2` (or Ctrl-C). `bv list` shows no new version; `bv status <upload-id>` shows UNFINISHED. Stop the server and start it again.
4. Continue, complete and restore: run the same `bv backup` command again; it continues the same upload ID. Then `bv restore V1 ./out1`, `bv restore V2 ./out2`, and compare each folder with its source.
5. Change one byte of a file under `vault/chunks/`, delete another chunk file, and run `bv verify`. It lists each damaged chunk with every version and file path, repairs nothing, and exits with code 2.

## Design

See [`docs/architecture.md`](docs/architecture.md) for the design note, [`LLD.md`](LLD.md) for the full low-level design and [`PRD_1.md`](PRD_1.md) for the requirements. In short:

- Fixed-size 512 KiB chunks; chunk ID = SHA-256 of the original bytes; empty files have zero chunks.
- Chunk files are content-addressed on disk (`chunks/ab/<hash>`). A chunk exists when its file exists. Writes go to `tmp/`, are hash-checked and fsynced, then atomically renamed. An existing chunk file is never overwritten.
- Metadata is in SQLite. Uploads and versions are separate tables; a version exists only as rows written inside one commit transaction, so unfinished work cannot appear in `list` or `restore`.
- Commit re-hashes every chunk the version needs before that transaction.
- Every write request (create upload, put chunk, commit) is safe to repeat.

## Known limits

- Fixed-size chunking: inserting bytes in the middle of a file shifts every later chunk, so those chunks are uploaded again. In-place edits and appended data reuse well.
- One backup at a time; one user; no authentication. Bind the server to loopback or a trusted network only.
- A failed restore stops at the first bad chunk and leaves the partial destination in place. Do not trust a failed restore.
- `verify` reports damage and never repairs. The server never deletes or replaces a chunk. A backup that needs a damaged stored chunk is refused (`CORRUPT_CHUNK`) until the operator deals with that chunk file.
- Symlinks, junctions and special files are skipped with a warning. Permissions, ACLs and other OS attributes are not saved. Only modification times are restored.
- Source files must not change during a backup; the client stops with `SOURCE_CHANGED` if a chunk differs when it is re-read for upload.
- File names that differ only by letter case are not checked.
- Directory fsync is skipped on Windows (the OS does not support it).
- No deletion of versions and no garbage collection; chunks of abandoned uploads stay in the store.
- If the server crashes after a commit but before the client gets the answer, the version is complete, and rerunning the backup creates one more snapshot of the same content (0 uploaded bytes).
- Chunk size limit 4 MiB; manifest size limit 64 MiB.
- The stretch features in the PRD (content-defined chunking, parallel uploads, Docker, web page, partial restore, GC, compression) are not implemented. `--json` is implemented.

## Test hooks (off by default)

- Server variable `BV_FAULT`: `crash_after_chunks=N`, `crash_before_rename`, `crash_before_commit_tx`, `crash_after_commit_tx`. The server process exits at that point.
- Client flag `bv backup --stop-after-chunks N`: stop with exit code 130 after N chunks.
- Client variables `BV_RETRY_DELAYS` (comma-separated seconds) and `BV_STATE_DIR`.

## External libraries, services and AI-assisted work

- **Runtime:** Python standard library only (`http.server`, `http.client`, `sqlite3`, `hashlib`, `argparse`). No external service.
- **Tests:** `pytest` (versions pinned in `requirements-dev.lock`).
- **Not used:** Git, restic, Borg, Kopia or any other backup engine as storage.
- **AI-assisted work:** Claude Code (Anthropic) was used to write the source code, the tests, the demo script and the documentation in this repository, working from the team's `PRD_1.md` and `LLD.md`.
