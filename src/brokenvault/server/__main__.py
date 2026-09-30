"""bv-server entrypoint: python -m brokenvault.server --data-dir ./vault --port 8765"""

import argparse
import os
import sys

from .http_app import App, BVServer


def main(argv=None):
    parser = argparse.ArgumentParser(prog="bv-server", description="BrokenVault backup server")
    parser.add_argument("--data-dir", default="./vault", help="folder for chunks and metadata (default ./vault)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--fast-commit", action="store_true",
                        help="skip re-hashing chunks received by the same upload at commit")
    parser.add_argument("--verbose", action="store_true", help="log every request")
    args = parser.parse_args(argv)

    try:
        app = App(os.path.abspath(args.data_dir), os.environ.get("BV_FAULT"), args.fast_commit)
        server = BVServer((args.host, args.port), app, args.verbose)
    except (OSError, ValueError) as exc:
        print(f"error: cannot start the server: {exc}", file=sys.stderr)
        return 1

    counts = app.startup_counts()
    host, port = server.server_address[:2]
    print(f"BrokenVault server listening on http://{host}:{port}  data-dir={app.data_dir}", flush=True)
    print(f"  versions={counts['versions']} open_uploads={counts['open_uploads']} "
          f"chunk_files={counts['chunk_files']} tmp_files_removed={counts['tmp_removed']}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("server stopped", flush=True)
    finally:
        server.server_close()
        app.db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
