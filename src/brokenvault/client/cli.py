"""bv command line: backup, list, restore, verify, status (LLD 6.1)."""

import argparse
import os
import sys

from ..common.constants import DEFAULT_CHUNK_SIZE, DEFAULT_SERVER, MAX_CHUNK_SIZE
from ..common.errors import EXIT_INPUT, EXIT_INTEGRITY, EXIT_INTERRUPTED, EXIT_NETWORK, EXIT_OK, BVError
from . import output
from .backup import BackupRunner, BackupStopped
from .restore import restore
from .transport import RETRY_DELAYS, Transport


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # usage errors exit 1 (IR-2), not argparse's default 2
        self.print_usage(sys.stderr)
        output.err(f"error: {message}\n  next: run `{self.prog} --help`")
        raise SystemExit(EXIT_INPUT)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--server", default=os.environ.get("BV_SERVER", DEFAULT_SERVER),
                        help=f"server URL (default {DEFAULT_SERVER}, or the BV_SERVER variable)")
    common.add_argument("--json", action="store_true", help="print machine-readable JSON")

    parser = _Parser(prog="bv", description="BrokenVault client: deduplicating, resumable, verifiable backup")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    p = sub.add_parser("backup", parents=[common], help="back up a folder, or continue its unfinished upload")
    p.add_argument("folder")
    p.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, metavar="BYTES",
                   help=f"fixed chunk size (default {DEFAULT_CHUNK_SIZE}, max {MAX_CHUNK_SIZE})")
    p.add_argument("--resume", metavar="UPLOAD_ID", help="continue this unfinished upload explicitly")
    p.add_argument("--stop-after-chunks", type=int, metavar="N",
                   help="test hook: stop with exit code 130 after sending N chunks")

    sub.add_parser("list", parents=[common], help="list completed versions")

    p = sub.add_parser("restore", parents=[common], help="restore a completed version into an empty folder")
    p.add_argument("version", help="version ID, for example V1")
    p.add_argument("dest", help="destination folder (must be empty or not exist)")

    sub.add_parser("verify", parents=[common], help="check every stored chunk of every completed version")

    p = sub.add_parser("status", parents=[common], help="show the state of an upload")
    p.add_argument("upload_id")
    return parser


def _retry_delays():
    raw = os.environ.get("BV_RETRY_DELAYS")  # test hook: shorter waits
    if not raw:
        return RETRY_DELAYS
    return tuple(float(x) for x in raw.split(",") if x.strip())


def _cmd_backup(args, transport):
    if not 0 < args.chunk_size <= MAX_CHUNK_SIZE:
        raise BVError("BAD_REQUEST", f"--chunk-size must be 1..{MAX_CHUNK_SIZE} bytes")
    runner = BackupRunner(transport, args.folder, args.chunk_size, resume_id=args.resume,
                          stop_after_chunks=args.stop_after_chunks,
                          progress=output.Progress(), notice=output.err)
    try:
        result = runner.run()
    except KeyboardInterrupt:  # Ctrl-C before an upload was opened
        output.err("Backup interrupted before an upload was opened. Nothing was stored. "
                   "Run the same command again.")
        return EXIT_INTERRUPTED
    except BackupStopped as stop:
        why = "interrupted" if stop.reason == "interrupt" else f"server problem: {stop.cause.message}"
        info = {"state": "UNFINISHED", "upload_id": stop.upload_id, "reason": stop.reason,
                "chunks_stored": stop.chunks_stored, "chunks_needed": stop.chunks_needed,
                "hint": "run the same command again to continue"}
        if stop.cause is not None:
            info["error"] = stop.cause.to_dict()["error"]
        if args.json:
            output.emit_json(info)
        output.err(output.unfinished_message(stop.upload_id, stop.chunks_stored, stop.chunks_needed, why))
        return EXIT_INTERRUPTED if stop.reason == "interrupt" else EXIT_NETWORK
    if args.json:
        output.emit_json(result)
    else:
        print(output.backup_summary(result))
    return EXIT_OK


def _cmd_list(args, transport):
    versions = transport.list_versions()
    if args.json:
        output.emit_json(versions)
    else:
        print(output.versions_table(versions))
    return EXIT_OK


def _cmd_restore(args, transport):
    result = restore(transport, args.version, args.dest)
    if args.json:
        output.emit_json(result)
    else:
        print(output.restore_summary(result))
    return EXIT_OK


def _cmd_verify(args, transport):
    report = transport.verify()
    if args.json:
        output.emit_json(report)
    else:
        print(output.verify_report(report))
    return EXIT_OK if report["healthy"] else EXIT_INTEGRITY


def _cmd_status(args, transport):
    status = transport.get_upload(args.upload_id)
    if args.json:
        output.emit_json(status)
    else:
        print(output.status_summary(status))
    return EXIT_OK


COMMANDS = {"backup": _cmd_backup, "list": _cmd_list, "restore": _cmd_restore,
            "verify": _cmd_verify, "status": _cmd_status}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # odd file names must not crash printing
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    args = build_parser().parse_args(argv)
    transport = None
    try:
        transport = Transport(args.server, _retry_delays())
        return COMMANDS[args.command](args, transport)
    except BVError as exc:
        hint = output.hint_for(exc.code)
        if args.json:
            output.emit_json({"error": {"code": exc.code, "message": exc.message, "details": exc.details,
                                        "hint": hint}})
        output.err(f"error [{exc.code}]: {exc.message}\n  next: {hint}")
        return exc.exit_code
    except KeyboardInterrupt:
        output.err("Interrupted.")
        return EXIT_INTERRUPTED
    except OSError as exc:
        output.err(f"error: {args.command} failed on {exc.filename or 'a local file'}: {exc.strerror or exc}\n"
                   f"  next: check the path and its permissions, then run the command again")
        return EXIT_INPUT
    finally:
        if transport is not None:
            transport.close()


if __name__ == "__main__":
    sys.exit(main())
