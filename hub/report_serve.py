#!/usr/bin/env python3
"""
hub/report_serve.py — serve the generated HTML brain report to the tailnet.

    python3 hub/report_serve.py --file <path/to/report.html> [--port 8446]

Why this exists at all: `tailscale serve <path>` needs root to serve a filesystem
path, so the established pattern on this host (Coach, Portia) is a tiny loopback
server with `tailscale serve` proxying to it. This is that server, kept as small as
it can be because it hands out a file containing every private entry.

Security posture:
  - Binds 127.0.0.1 ONLY, never the wildcard. The tailnet reaches it exclusively
    through `tailscale serve`, which does the authentication.
  - Serves exactly ONE file, chosen at startup. There is no path routing, so
    directory traversal is not merely blocked, it is not expressible: every GET
    returns that one file or 404.
  - Read-only. GET and HEAD only; everything else is 405.
  - Re-reads the file per request, so the daily rebuild is picked up without a
    restart.
"""

import argparse
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPORT_FILE = None


class ReportHandler(BaseHTTPRequestHandler):
    server_version = "loreport-report/1.0"

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # This page is every private entry the user has. Never let a browser or an
        # intermediary retain it, and never let another origin frame it.
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read(self):
        try:
            with open(REPORT_FILE, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def do_GET(self):
        body = self._read()
        if body is None:
            self._send(404, b"report not generated yet - run hub/report_build.py\n")
            return
        self._send(200, body, "text/html; charset=utf-8")

    def do_HEAD(self):
        self.do_GET()

    def _reject(self):
        self._send(405, b"read-only\n")

    do_POST = do_PUT = do_DELETE = do_PATCH = _reject

    def log_message(self, fmt, *args):
        # Default logging writes the request line to stderr on every hit, which
        # fills the journal with nothing useful for a single-file server.
        pass


def main():
    global REPORT_FILE
    ap = argparse.ArgumentParser(description="Serve the Loreport HTML report on loopback.")
    ap.add_argument("--file", required=True, help="path to the generated report.html")
    ap.add_argument("--port", type=int, default=8446)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    if args.host != "127.0.0.1":
        # Refuse in code, not just in docs: this file is every private entry, and
        # its only access control is that tailscale fronts it.
        sys.exit("report_serve: refusing to bind anything but 127.0.0.1")

    REPORT_FILE = os.path.abspath(args.file)
    print(f"report_serve: {args.host}:{args.port} -> {REPORT_FILE}", flush=True)
    ThreadingHTTPServer((args.host, args.port), ReportHandler).serve_forever()


if __name__ == "__main__":
    main()
