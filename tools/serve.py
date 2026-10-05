#!/usr/bin/env python3
"""Serve web/dist for local play and for the headless test.

    python3 tools/serve.py [port]

Port 0 (the default) picks a free one and prints it, because ten agents share
this box and a hardcoded port is how a harness ends up talking to someone else's
server. Threaded, because a single-threaded handler deadlocks the moment the
page asks for the 700 KB core while another request is still open.

Nothing outside web/dist is reachable. In particular there is no route that
serves a ROM: the ROM reaches the page through its own <input type=file>, which
is the path a real user takes and the one the test therefore exercises.
"""
import argparse
import functools
import http.server
import pathlib
import socketserver
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "web" / "dist"


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *a):
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", type=int, default=0)
    a = ap.parse_args()
    if not ROOT.is_dir():
        print(f"no {ROOT} -- run `make` in web/ first")
        return 1
    h = functools.partial(Handler, directory=str(ROOT))
    with Server(("127.0.0.1", a.port), h) as srv:
        print(f"PORT {srv.server_address[1]}", flush=True)
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
