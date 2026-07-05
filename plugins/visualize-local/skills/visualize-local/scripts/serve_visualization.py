#!/usr/bin/env python3
"""Serve a generated visualization directory on a local-only HTTP server."""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

from privacy import ensure_private_run_directory


BIND_HOST = "127.0.0.1"
BROWSER_HOST = "127.0.0.1"
OUTPUT_ROOT = Path("/private/tmp/codex-visualizations")


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve a visualize-local output directory.")
    parser.add_argument("directory", help="Visualization run directory containing index.html.")
    parser.add_argument("--port", type=int, default=0, help="Local port. Use 0 for any free port.")
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=3600,
        help="Automatically stop after this many seconds. Use 0 to disable.",
    )
    return parser.parse_args()


def validate_directory(raw_directory: str) -> Path:
    return ensure_private_run_directory(OUTPUT_ROOT, Path(raw_directory))


def main() -> int:
    args = parse_args()
    if args.ttl_seconds < 0:
        print("error: --ttl-seconds must be greater than or equal to 0", file=sys.stderr)
        return 2

    try:
        directory = validate_directory(args.directory)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    handler = functools.partial(QuietHandler, directory=str(directory))
    with ThreadedTCPServer((BIND_HOST, args.port), handler) as server:
        port = server.server_address[1]
        if args.ttl_seconds > 0:
            timer = threading.Timer(args.ttl_seconds, server.shutdown)
            timer.daemon = True
            timer.start()
        print(
            json.dumps(
                {
                    "directory": str(directory),
                    "local_url": f"http://{BROWSER_HOST}:{port}/index.html",
                    "ttl_seconds": args.ttl_seconds,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        server.serve_forever()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
