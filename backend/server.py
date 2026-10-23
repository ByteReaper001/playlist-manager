"""
server.py - zero-dependency HTTP server (Python standard library only).

  python3 backend/server.py            -> http://127.0.0.1:8000
  python3 backend/server.py --port 9000 --open

Serves the frontend from ../frontend and a small JSON API under /api.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from service import AppError, BadRequest, MusicApp, NotFound  # noqa: E402

ROOT = os.path.dirname(HERE)
FRONTEND_DIR = os.path.join(ROOT, "frontend")
DEFAULT_DATA = os.path.join(ROOT, "data", "library.json")
MAX_BODY = 64 * 1024

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

# (method, pattern, handler(app, match, body, query) -> dict)
ROUTES = [
    ("GET", r"/api/state", lambda a, m, b, q: a.state()),
    (
        "GET",
        r"/api/search",
        lambda a, m, b, q: a.search(q.get("q", [""])[0], q.get("scope", ["current"])[0]),
    ),
    ("POST", r"/api/playlists", lambda a, m, b, q: a.create_playlist(b.get("name"))),
    ("DELETE", r"/api/playlists/(\d+)", lambda a, m, b, q: a.delete_playlist(int(m[1]))),
    ("POST", r"/api/playlists/(\d+)/select", lambda a, m, b, q: a.select_playlist(int(m[1]))),
    ("POST", r"/api/library/(next|prev)", lambda a, m, b, q: a.switch_playlist(m[1])),
    (
        "POST",
        r"/api/songs",
        lambda a, m, b, q: a.add_song(
            b.get("title"), b.get("artist"), b.get("album"), b.get("duration"), b.get("index")
        ),
    ),
    ("DELETE", r"/api/songs/(\d+)", lambda a, m, b, q: a.delete_song(int(m[1]))),
    ("POST", r"/api/songs/(\d+)/move", lambda a, m, b, q: a.move_song(int(m[1]), b.get("index"))),
    ("POST", r"/api/player/play", lambda a, m, b, q: a.play(b.get("song_id"), b.get("playlist_id"))),
    ("POST", r"/api/player/next", lambda a, m, b, q: a.next(b.get("auto", False))),
    ("POST", r"/api/player/prev", lambda a, m, b, q: a.prev()),
    ("POST", r"/api/player/repeat", lambda a, m, b, q: a.set_repeat(b.get("mode"))),
    ("POST", r"/api/reset", lambda a, m, b, q: a.reset()),
]
ROUTES = [(method, re.compile("^" + pattern + "$"), fn) for method, pattern, fn in ROUTES]


def make_handler(app, quiet=False):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Loopback/1.0"
        protocol_version = "HTTP/1.1"

        # ---- plumbing ----------------------------------------------------
        def log_message(self, fmt, *args):
            if quiet:
                return
            path = self.path.split("?")[0]
            if path.startswith("/api/"):
                sys.stderr.write("  %s %s -> %s\n" % (self.command, path, args[1] if len(args) > 1 else ""))

        def _send(self, status, body, content_type, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            extra = None
            if int(status) >= 400:
                # An error may leave an unread request body on the socket, which
                # would corrupt the next keep-alive request. Close instead.
                self.close_connection = True
                extra = {"Connection": "close"}
            self._send(status, body, "application/json; charset=utf-8", extra)

        def _read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise BadRequest("request body too large")
            if length == 0:
                return {}
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                # Blocks cross-site form posts: browsers only allow JSON from
                # our own pages (other origins need a CORS preflight we refuse).
                raise AppError("Content-Type must be application/json")
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise BadRequest("body is not valid JSON")
            if not isinstance(data, dict):
                raise BadRequest("body must be a JSON object")
            return data

        # ---- dispatch ----------------------------------------------------
        def _handle(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path.startswith("/api/"):
                return self._api(parsed, path)
            if self.command not in ("GET", "HEAD"):
                return self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})
            return self._static(path)

        def _api(self, parsed, path):
            try:
                path_known = False
                for method, pattern, fn in ROUTES:
                    match = pattern.match(path)
                    if not match:
                        continue
                    path_known = True
                    if method != self.command and not (method == "GET" and self.command == "HEAD"):
                        continue
                    # Non-GET requests must declare JSON even when the body is empty.
                    if self.command in ("POST", "DELETE"):
                        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                        if ctype != "application/json":
                            raise AppError("Content-Type must be application/json")
                    body = self._read_body() if self.command in ("POST", "DELETE") else {}
                    return self._json(HTTPStatus.OK, fn(app, match, body, parse_qs(parsed.query)))
                if path_known:
                    return self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})
                raise NotFound("Unknown endpoint")
            except AppError as exc:
                self._json(exc.status, {"error": str(exc)})
            except Exception as exc:  # pragma: no cover - last-resort guard
                sys.stderr.write("internal error: %r\n" % (exc,))
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal server error"})

        def _static(self, path):
            rel = unquote(path).lstrip("/") or "index.html"
            full = os.path.normpath(os.path.join(FRONTEND_DIR, rel))
            inside = os.path.commonpath([FRONTEND_DIR, full]) == FRONTEND_DIR if full else False
            if not inside or not os.path.isfile(full):
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
                ctype += "; charset=utf-8"
            with open(full, "rb") as fh:
                data = fh.read()
            self._send(HTTPStatus.OK, data, ctype)

        do_GET = do_HEAD = do_POST = do_DELETE = do_PUT = do_PATCH = _handle

    return Handler


def create_server(app, host="127.0.0.1", port=8000, quiet=False):
    server = ThreadingHTTPServer((host, port), make_handler(app, quiet))
    server.daemon_threads = True
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Loopback - music playlist manager (LL01)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--data", default=DEFAULT_DATA, help="JSON file used to save the library")
    parser.add_argument("--open", action="store_true", help="open the app in your browser")
    parser.add_argument("--reset", action="store_true", help="discard saved data and restore the demo library")
    args = parser.parse_args(argv)

    if args.reset and os.path.exists(args.data):
        os.remove(args.data)
    app = MusicApp(args.data)
    try:
        server = create_server(app, args.host, args.port)
    except OSError as exc:
        sys.exit("Could not start on %s:%d (%s). Try --port 8001." % (args.host, args.port, exc.strerror or exc))

    url = "http://%s:%d" % (args.host, server.server_address[1])
    print("Loopback is running at %s  (Ctrl+C to stop)" % url)
    print("Saved data: %s" % args.data)
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye!")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
