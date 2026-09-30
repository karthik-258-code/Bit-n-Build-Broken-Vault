"""Human and JSON formatting for the CLI."""

import json
import sys
import time

# What to do next, per error code (IR-1).
HINTS = {
    "INVALID_MANIFEST": "check the folder and the --chunk-size value, then run the command again",
    "INVALID_PATH": "rename or remove the named path, or pass an existing folder",
    "UNSAFE_PATH": "the version holds an unsafe path; do not trust this restore",
    "UNKNOWN_UPLOAD": "check the upload ID, or run `bv backup <folder>` without --resume",
    "UPLOAD_CLOSED": "the upload is finished; run `bv list`, or `bv backup <folder>` to start a new version",
    "CHUNK_HASH_MISMATCH": "the source file changed during the backup; run the same backup command again",
    "CHUNK_SIZE_MISMATCH": "the source file changed during the backup; run the same backup command again",
    "CHUNK_NOT_IN_MANIFEST": "run the same backup command again",
    "INCOMPLETE_UPLOAD": "run the same backup command again to send the missing chunks",
    "CORRUPT_CHUNK": "run `bv verify` to see every version and file affected; nothing was repaired or deleted",
    "VERSION_NOT_FOUND": "run `bv list` to see completed versions (unfinished uploads cannot be restored)",
    "CHUNK_MISSING": "run `bv verify` to see the damage; do not trust the partly restored folder",
    "RESTORE_CHUNK_INVALID": "run `bv verify` to see the damage; do not trust the partly restored folder",
    "RESTORE_SIZE_MISMATCH": "run `bv verify`; do not trust the partly restored folder",
    "DEST_NOT_EMPTY": "choose an empty or not yet existing destination folder",
    "SOURCE_CHANGED": "keep the folder unchanged during a backup, then run `bv backup <folder>` again",
    "SERVER_UNAVAILABLE": "start the server (python -m brokenvault.server) or check --server, then run the "
                          "same command again",
    "INTERNAL": "look at the server log, then run the same command again",
    "BAD_REQUEST": "check the command arguments (see --help)",
}


def hint_for(code):
    return HINTS.get(code, "see --help")


def emit_json(obj):
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def err(text):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def n(value):
    return f"{value:,}"


def mib(value):
    return f"{value / (1024 * 1024):.1f} MiB"


def when(unix_seconds):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(unix_seconds))


class Progress:
    """Progress lines on stderr: in place on a terminal, about every 10% otherwise."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.tty = sys.stderr.isatty()
        self._last_step = -1

    def __call__(self, done, total, done_bytes, total_bytes):
        if not self.enabled:
            return
        if total == 0:
            err("Sending  : 0 chunks (the server already has everything)")
            return
        filled = 14 * done // total
        line = f"Sending  : {done}/{total} chunks  [{'#' * filled}{'-' * (14 - filled)}]  " \
               f"{mib(done_bytes)} / {mib(total_bytes)}"
        if self.tty:
            sys.stderr.write("\r" + line + ("\n" if done == total else ""))
            sys.stderr.flush()
        else:
            step = 10 * done // total
            if step != self._last_step or done == total:
                self._last_step = step
                err(line)


def backup_summary(r):
    return "\n".join([
        f"Version  : {r['version_id']}   State: {r['state']}",
        f"Upload   : {r['upload_id']}  ({'resumed' if r['resumed'] else 'new'}; "
        f"sent {r['chunks_sent']} of {r['unique_chunks']} distinct chunks in this run)",
        f"Files    : {n(r['file_count'])} files, {n(r['dir_count'])} folders",
        f"Total folder bytes    : {n(r['total_bytes']):>15}",
        f"Uploaded chunk bytes  : {n(r['uploaded_bytes']):>15}",
        f"Reused bytes          : {n(r['reused_bytes']):>15}",
    ])


def unfinished_message(upload_id, stored, needed, why):
    return "\n".join([
        f"Backup not complete ({why}). State: UNFINISHED (upload {upload_id}). "
        f"Nothing was added to the version list.",
        f"Run the same command again to continue (or add --resume {upload_id}); "
        f"{stored} of {needed} chunks already stored.",
    ])


def versions_table(versions):
    if not versions:
        return "No completed versions."
    head = f"{'VERSION':<8} {'STATE':<9} {'CREATED':<19} {'FILES':>7} {'TOTAL BYTES':>15} " \
           f"{'UPLOADED BYTES':>15} {'REUSED BYTES':>15}"
    rows = [head]
    for v in versions:
        rows.append(f"{v['version_id']:<8} {v['state']:<9} {when(v['created_at']):<19} {n(v['file_count']):>7} "
                    f"{n(v['total_bytes']):>15} {n(v['uploaded_bytes']):>15} {n(v['reused_bytes']):>15}")
    return "\n".join(rows)


def restore_summary(r):
    return "\n".join([
        f"Version  : {r['version_id']}   Result: {r['result']}",
        f"Restored : {n(r['files_restored'])} files, {n(r['dirs_restored'])} folders, "
        f"{n(r['bytes_restored'])} bytes",
        f"Into     : {r['dest']}",
        "Every chunk hash was checked while reading.",
    ])


def verify_report(r):
    lines = [f"Checked {r['checked_versions']} versions, {r['checked_chunks']} chunks."]
    for chunk in r["damaged_chunks"]:
        lines.append(f"DAMAGED  chunk {chunk['id']}  {chunk['reason']} ({chunk['detail']})")
        for hit in chunk["affected"]:
            lines.append(f"  {hit['version']}  {hit['path']}")
    if r["versions"]:
        parts = []
        for v in r["versions"]:
            count = len(v["files"])
            parts.append(f"{v['version']}: {v['status']}" + (f" ({count} file{'s' if count != 1 else ''})"
                                                             if count else ""))
        lines.append("   ".join(parts))
    lines.append("All chunks are healthy." if r["healthy"]
                 else f"{len(r['damaged_chunks'])} damaged chunk(s) found. No repairs were made.")
    return "\n".join(lines)


def status_summary(s):
    state = {"OPEN": "UNFINISHED", "COMMITTED": "COMPLETE"}.get(s["state"], s["state"])
    lines = [f"Upload   : {s['upload_id']}   State: {state}"]
    if s.get("version_id"):
        lines.append(f"Version  : {s['version_id']}")
    lines += [
        f"Chunks stored / needed: {n(s['chunks_stored'])} / {n(s['chunks_needed'])}",
        f"Total folder bytes    : {n(s['total_bytes']):>15}",
        f"Uploaded chunk bytes  : {n(s['uploaded_bytes']):>15}",
    ]
    if state == "UNFINISHED":
        lines.append("Run `bv backup <folder>` on the same folder to continue this upload.")
    return "\n".join(lines)
