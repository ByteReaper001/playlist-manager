"""End-to-end tests: real HTTP server on a free port, real JSON files."""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from server import create_server  # noqa: E402
from service import MusicApp, parse_duration, BadRequest  # noqa: E402


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "lib.json")
        self.boot()

    def boot(self):
        self.app = MusicApp(self.data)
        self.server = create_server(self.app, "127.0.0.1", 0, quiet=True)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()

    def tearDown(self):
        self.shutdown()
        self.tmp.cleanup()

    def call(self, method, path, body=None, headers=None):
        data = None if method == "GET" else json.dumps(body or {}).encode()
        h = {} if method == "GET" else {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def titles(self, reply):
        return [s["title"] for s in reply["state"]["playlist"]["songs"]]

    # ------------------------------------------------------------------
    def test_seed_state_and_static_files(self):
        status, reply = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        lib = reply["state"]["library"]
        self.assertEqual(lib["size"], 3)
        self.assertEqual(lib["tail_id"], lib["playlists"][-1]["id"])
        self.assertEqual(reply["state"]["playlist"]["size"], 7)
        # frontend + fonts are served with sensible types
        for path, ctype in (("/", "text/html"), ("/style.css", "text/css"),
                            ("/app.js", "text/javascript"), ("/fonts/Geist-Variable.woff2", "font/woff2")):
            with urllib.request.urlopen(self.base + path) as resp:
                self.assertEqual(resp.status, 200, path)
                self.assertTrue(resp.headers["Content-Type"].startswith(ctype), path)

    def test_path_traversal_blocked(self):
        for path in ("/../backend/server.py", "/..%2fbackend%2fserver.py", "/%2e%2e/backend/service.py"):
            req = urllib.request.Request(self.base + path)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code, 404, path)

    def test_add_delete_and_navigation(self):
        _, r = self.call("POST", "/api/songs", {"title": "Alpha", "artist": "A", "duration": "2:30", "index": 0})
        self.assertEqual(self.titles(r)[0], "Alpha")
        self.assertIn("insert(0)", r["message"])
        alpha = r["state"]["playlist"]["songs"][0]["id"]
        # play it, then insert before and delete it: current must move on sensibly
        self.call("POST", "/api/player/play", {"song_id": alpha})
        _, r = self.call("POST", "/api/songs", {"title": "Before", "index": 0})
        self.assertEqual(r["state"]["playlist"]["current_id"], alpha)
        _, r = self.call("DELETE", "/api/songs/%d" % alpha)
        self.assertNotIn("Alpha", self.titles(r))
        pl = r["state"]["playlist"]
        self.assertIsNotNone(pl["current_id"])
        self.assertIn("now playing moved", r["message"])

    def test_repeat_all_closes_ring(self):
        _, r = self.call("POST", "/api/player/repeat", {"mode": "all"})
        pl = r["state"]["playlist"]
        self.assertTrue(pl["circular"])
        songs = pl["songs"]
        self.assertEqual(songs[0]["prev_id"], pl["tail_id"])
        self.assertEqual(songs[-1]["next_id"], pl["head_id"])
        _, r = self.call("POST", "/api/player/repeat", {"mode": "off"})
        songs = r["state"]["playlist"]["songs"]
        self.assertIsNone(songs[0]["prev_id"])
        self.assertIsNone(songs[-1]["next_id"])

    def test_next_wraps_or_stops(self):
        for _ in range(6):
            self.call("POST", "/api/player/next", {})
        _, r = self.call("POST", "/api/player/next", {"auto": True})
        self.assertTrue(r["stopped"])
        self.assertEqual(r["state"]["playlist"]["current_index"], 0)
        self.call("POST", "/api/player/repeat", {"mode": "all"})
        self.call("POST", "/api/player/prev", {})
        _, r = self.call("POST", "/api/player/next", {})
        self.assertFalse(r["stopped"])
        self.assertEqual(r["state"]["playlist"]["current_index"], 0)

    def test_playlists_create_switch_delete(self):
        _, r = self.call("POST", "/api/playlists", {"name": "  Road   Trip "})
        self.assertEqual(r["state"]["library"]["playlists"][-1]["name"], "Road Trip")
        start = r["state"]["library"]["current_id"]
        _, r = self.call("POST", "/api/library/prev", {})  # wraps from head to new tail
        self.assertEqual(r["state"]["playlist"]["name"], "Road Trip")
        self.assertEqual(r["state"]["playlist"]["size"], 0)
        _, r = self.call("POST", "/api/library/next", {})
        self.assertEqual(r["state"]["library"]["current_id"], start)
        _, r = self.call("DELETE", "/api/playlists/%d" % start)
        self.assertEqual(r["state"]["library"]["size"], 3)
        self.assertNotEqual(r["state"]["library"]["current_id"], start)

    def test_empty_library_and_recovery(self):
        _, r = self.call("GET", "/api/state")
        for p in r["state"]["library"]["playlists"]:
            _, r = self.call("DELETE", "/api/playlists/%d" % p["id"])
        self.assertIsNone(r["state"]["playlist"])
        status, r = self.call("POST", "/api/songs", {"title": "x"})
        self.assertEqual(status, 400)
        status, r = self.call("POST", "/api/player/next", {})
        self.assertEqual(status, 400)
        _, r = self.call("POST", "/api/playlists", {"name": "Fresh"})
        self.assertEqual(r["state"]["playlist"]["name"], "Fresh")

    def test_search_scopes(self):
        _, r = self.call("GET", "/api/search?q=lowtide&scope=current")
        self.assertEqual(len(r["results"]), 2)
        _, r = self.call("GET", "/api/search?q=lowtide&scope=all")
        self.assertEqual(len(r["results"]), 4)
        self.assertEqual({x["playlist_name"] for x in r["results"]}, {"Late Night Coding", "Morning Run", "Rainy Evenings"})
        # play a hit that lives in another playlist
        hit = [x for x in r["results"] if x["playlist_name"] == "Rainy Evenings"][0]
        _, r = self.call("POST", "/api/player/play", {"song_id": hit["song_id"], "playlist_id": hit["playlist_id"]})
        self.assertEqual(r["state"]["playlist"]["name"], "Rainy Evenings")
        self.assertEqual(r["state"]["playlist"]["current_id"], hit["song_id"])

    def test_move(self):
        _, r = self.call("GET", "/api/state")
        first = r["state"]["playlist"]["songs"][0]
        _, r = self.call("POST", "/api/songs/%d/move" % first["id"], {"index": 3})
        self.assertEqual(r["state"]["playlist"]["songs"][3]["id"], first["id"])
        self.assertEqual(r["state"]["playlist"]["size"], 7)

    def test_validation_errors(self):
        cases = [
            ("POST", "/api/songs", {"title": ""}),
            ("POST", "/api/songs", {"title": "x" * 101}),
            ("POST", "/api/songs", {"title": "ok", "duration": "abc"}),
            ("POST", "/api/songs", {"title": "ok", "duration": 0}),
            ("POST", "/api/songs", {"title": "ok", "index": "two"}),
            ("POST", "/api/playlists", {"name": "   "}),
            ("POST", "/api/player/repeat", {"mode": "sideways"}),
            ("POST", "/api/player/play", {"song_id": "1"}),
        ]
        for method, path, body in cases:
            status, r = self.call(method, path, body)
            self.assertEqual(status, 400, (path, body))
            self.assertIn("error", r)
        self.assertEqual(self.call("DELETE", "/api/songs/99999")[0], 404)
        self.assertEqual(self.call("DELETE", "/api/playlists/99999")[0], 404)
        self.assertEqual(self.call("POST", "/api/nope", {})[0], 404)
        self.assertEqual(self.call("GET", "/api/songs")[0], 405)

    def test_requires_json_content_type(self):
        req = urllib.request.Request(
            self.base + "/api/player/next", data=b"auto=1", method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

    def test_persistence_roundtrip(self):
        self.call("POST", "/api/songs", {"title": "Persisted", "duration": "1:05", "index": 2})
        self.call("POST", "/api/player/repeat", {"mode": "all"})
        self.call("POST", "/api/library/next", {})
        _, before = self.call("GET", "/api/state")
        self.shutdown()
        self.boot()  # fresh process, same file
        _, after = self.call("GET", "/api/state")
        self.assertEqual(before["state"]["library"], after["state"]["library"])
        # first playlist (not the active one now) kept its edits and repeat mode
        _, r = self.call("POST", "/api/library/prev", {})
        pl = r["state"]["playlist"]
        self.assertEqual(pl["songs"][2]["title"], "Persisted")
        self.assertEqual(pl["songs"][2]["duration"], 65)
        self.assertEqual(pl["repeat"], "all")
        self.assertTrue(pl["circular"])

    def test_corrupt_file_is_quarantined(self):
        self.shutdown()
        with open(self.data, "w") as fh:
            fh.write("{ not json")
        self.boot()
        _, r = self.call("GET", "/api/state")
        self.assertEqual(r["state"]["library"]["size"], 3)
        self.assertTrue(any(".corrupt-" in n for n in os.listdir(self.tmp.name)))

    def test_reset(self):
        self.call("POST", "/api/playlists", {"name": "Extra"})
        _, r = self.call("POST", "/api/reset", {})
        self.assertEqual(r["state"]["library"]["size"], 3)


class DurationTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_duration("3:30"), 210)
        self.assertEqual(parse_duration("1:02:03"), 3723)
        self.assertEqual(parse_duration(95), 95)
        self.assertEqual(parse_duration("  "), 180)
        self.assertEqual(parse_duration(None), 180)
        for bad in ("3:", ":30", "a:b", "1:2:3:4", "-5", 0, 99999, True):
            with self.assertRaises(BadRequest):
                parse_duration(bad)


if __name__ == "__main__":
    unittest.main()
