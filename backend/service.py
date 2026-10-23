"""
service.py - application layer between the HTTP API and the data structures.

MusicApp owns one Library (circular list of Playlists, each a doubly linked
list of songs), validates input, builds human-readable messages describing
what the pointers just did, and saves everything to a JSON file.
"""

from __future__ import annotations

import json
import os
import threading
import time

from structures import ALL, OFF, ONE, REPEAT_MODES, Library, Playlist, Song  # noqa: F401


# ------------------------------------------------------------------ errors
class AppError(Exception):
    status = 400


class BadRequest(AppError):
    status = 400


class NotFound(AppError):
    status = 404


# -------------------------------------------------------------- validation
def clean_text(value, field, max_len, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise BadRequest("%s must be text" % field)
    value = " ".join(value.split())  # trim + collapse whitespace
    if required and not value:
        raise BadRequest("%s is required" % field)
    if len(value) > max_len:
        raise BadRequest("%s is too long (max %d characters)" % (field, max_len))
    return value


def parse_duration(value, default=180):
    """Accept seconds (180), "180", "3:30" or "1:02:03". Returns seconds."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise BadRequest("length must be a number of seconds or m:ss")
    try:
        if isinstance(value, (int, float)):
            seconds = int(value)
        else:
            parts = value.strip().split(":")
            if not 1 <= len(parts) <= 3 or not all(p.strip().isdigit() for p in parts):
                raise ValueError
            seconds = 0
            for part in parts:
                seconds = seconds * 60 + int(part)
    except (ValueError, AttributeError):
        raise BadRequest("length must look like 3:30 (minutes:seconds)")
    if not 1 <= seconds <= 6 * 3600:
        raise BadRequest("length must be between 0:01 and 6:00:00")
    return seconds


def parse_index(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise BadRequest("index must be a whole number")
    return value


def parse_id(value, field):
    if isinstance(value, bool) or not isinstance(value, int):
        raise BadRequest("%s must be a whole number" % field)
    return value


def q(song):
    return "“%s”" % song.title


# ---------------------------------------------------------------- seed data
SEED = [
    (
        "Late Night Coding",
        [
            ("Static Bloom", "Hollow Circuit", "Null Terminator", "3:42"),
            ("Paper Lanterns", "Nova Harbor", "", "4:05"),
            ("Low Tide Radio", "Lowtide", "Salt & Static", "2:58"),
            ("Midnight Compile", "Hollow Circuit", "Null Terminator", "3:21"),
            ("Cold Brew Sunrise", "Marlow & Vale", "", "3:47"),
            ("Segfault Waltz", "Nova Harbor", "", "4:30"),
            ("Pointers in the Rain", "Lowtide", "Salt & Static", "3:12"),
        ],
    ),
    (
        "Morning Run",
        [
            ("Tailwind", "Kite Season", "First Light", "3:15"),
            ("Seven Kilometres", "Marlow & Vale", "", "3:50"),
            ("Sunday Pace", "Kite Season", "First Light", "2:45"),
            ("Breathless Hills", "Nova Harbor", "", "4:12"),
            ("Cooldown Letters", "Lowtide", "Salt & Static", "3:33"),
        ],
    ),
    (
        "Rainy Evenings",
        [
            ("Window Seat", "Marlow & Vale", "", "4:20"),
            ("Kettle Song", "Paper Moths", "Small Hours", "2:51"),
            ("Slow Trains", "Paper Moths", "Small Hours", "3:39"),
            ("Umbrella Weather", "Lowtide", "Salt & Static", "3:05"),
            ("Lamp Glow", "Hollow Circuit", "", "5:02"),
        ],
    ),
]


# ---------------------------------------------------------------- storage
class Store:
    """Tiny JSON file store with atomic writes."""

    def __init__(self, path):
        self.path = path

    def load(self):
        if not os.path.exists(self.path):
            return None
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, data):
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)  # atomic: never leaves a half-written file

    def quarantine(self):
        """Move an unreadable file out of the way so it is not lost."""
        if os.path.exists(self.path):
            os.replace(self.path, "%s.corrupt-%d" % (self.path, int(time.time())))


# ----------------------------------------------------------------- the app
class MusicApp:
    def __init__(self, data_path=None, seed=True):
        self.lock = threading.RLock()
        self.store = Store(data_path) if data_path else None
        self.seed_on_empty = seed
        self._reset_memory()

        loaded = None
        if self.store:
            try:
                loaded = self.store.load()
                if loaded is not None:
                    self._restore(loaded)
            except Exception as exc:  # unreadable / invalid file
                print("warning: could not read saved data (%s); starting fresh" % exc)
                self.store.quarantine()
                self._reset_memory()
                loaded = None
        if loaded is None and seed:
            self._seed()
            self._save()

    # ---- setup ----------------------------------------------------------
    def _reset_memory(self):
        self.library = Library()
        self._next_song_id = 1
        self._next_playlist_id = 1

    def _seed(self):
        for name, songs in SEED:
            playlist = self._new_playlist(name)
            for title, artist, album, length in songs:
                playlist.insert(self._new_song(title, artist, album, parse_duration(length)))
        self.library.select(self.library.head.playlist.id)

    def _new_song(self, title, artist, album, duration):
        song = Song(self._next_song_id, title, artist, album, duration)
        self._next_song_id += 1
        return song

    def _new_playlist(self, name):
        playlist = Playlist(self._next_playlist_id, name)
        self._next_playlist_id += 1
        self.library.add(playlist)
        return playlist

    # ---- persistence ----------------------------------------------------
    def _dump(self):
        playlists = []
        for node in self.library:
            p = node.playlist
            playlists.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "repeat": p.repeat,
                    "current_song": p.current.song.id if p.current else None,
                    "songs": [n.song.to_dict() for n in p],
                }
            )
        return {
            "next_song_id": self._next_song_id,
            "next_playlist_id": self._next_playlist_id,
            "current_playlist": self.library.current.playlist.id if self.library.current else None,
            "playlists": playlists,
        }

    def _restore(self, data):
        max_song, max_playlist = 0, 0
        for entry in data["playlists"]:
            playlist = Playlist(int(entry["id"]), clean_text(entry["name"], "name", 60, True))
            max_playlist = max(max_playlist, playlist.id)
            for s in entry["songs"]:
                song = Song(
                    int(s["id"]),
                    clean_text(s["title"], "title", 100, True),
                    clean_text(s.get("artist", ""), "artist", 100),
                    clean_text(s.get("album", ""), "album", 100),
                    parse_duration(s.get("duration")),
                )
                max_song = max(max_song, song.id)
                playlist.insert(song)
            playlist.set_repeat(entry.get("repeat", OFF))
            wanted = entry.get("current_song")
            if wanted is not None and playlist.find_node(wanted) is not None:
                playlist.play(wanted)
            self.library.add(playlist)
        self._next_song_id = max(int(data.get("next_song_id", 1)), max_song + 1)
        self._next_playlist_id = max(int(data.get("next_playlist_id", 1)), max_playlist + 1)
        wanted = data.get("current_playlist")
        if wanted is not None and self.library.find_node(wanted) is not None:
            self.library.select(wanted)

    def _save(self):
        if not self.store:
            return
        try:
            self.store.save(self._dump())
        except OSError as exc:
            print("warning: could not save data: %s" % exc)

    # ---- snapshots ------------------------------------------------------
    def _library_snapshot(self):
        lib = self.library
        playlists = []
        for node in lib:
            p = node.playlist
            playlists.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "count": p.size,
                    "duration": p.total_duration(),
                    "repeat": p.repeat,
                    "is_current": node is lib.current,
                    "next_id": node.next.playlist.id,
                }
            )
        return {
            "size": lib.size,
            "head_id": lib.head.playlist.id if lib.head else None,
            "tail_id": lib.tail.playlist.id if lib.tail else None,
            "current_id": lib.current.playlist.id if lib.current else None,
            "playlists": playlists,
        }

    def _reply(self, message=None, stopped=False, save=True, **extra):
        if save:
            self._save()
        # NB: compare with `is None` - an empty Playlist has len() == 0 and is falsy.
        current = self.library.current.playlist if self.library.current is not None else None
        reply = {
            "state": {
                "library": self._library_snapshot(),
                "playlist": current.snapshot() if current is not None else None,
            },
            "message": message,
            "stopped": stopped,
        }
        reply.update(extra)
        return reply

    def _current_playlist(self):
        if self.library.current is None:
            raise BadRequest("Create a playlist first")
        return self.library.current.playlist

    # ---- read -----------------------------------------------------------
    def state(self):
        with self.lock:
            return self._reply(save=False)

    def search(self, query, scope="current"):
        with self.lock:
            query = clean_text(query, "query", 100)
            if scope not in ("current", "all"):
                raise BadRequest("scope must be 'current' or 'all'")
            results = []
            if query and self.library.current is not None:
                if scope == "current":
                    targets = [self.library.current]
                else:
                    targets = list(self.library)
                for node in targets:
                    p = node.playlist
                    for index, song_node in p.search(query):
                        item = song_node.song.to_dict()
                        item["song_id"] = item.pop("id")
                        item["index"] = index
                        item["playlist_id"] = p.id
                        item["playlist_name"] = p.name
                        results.append(item)
            return {"query": query, "scope": scope, "results": results}

    # ---- library (playlists) --------------------------------------------
    def create_playlist(self, name):
        with self.lock:
            name = clean_text(name, "name", 60, required=True)
            playlist = self._new_playlist(name)
            return self._reply(
                "append_playlist: linked “%s” after the tail, tail.next → head (O(1))" % playlist.name
            )

    def delete_playlist(self, playlist_id):
        with self.lock:
            playlist_id = parse_id(playlist_id, "playlist id")
            try:
                playlist = self.library.remove(playlist_id)
            except KeyError:
                raise NotFound("Playlist not found")
            return self._reply(
                "delete_playlist: unlinked “%s”, ring closed over the gap (O(n) to find it)" % playlist.name
            )

    def select_playlist(self, playlist_id):
        with self.lock:
            playlist_id = parse_id(playlist_id, "playlist id")
            try:
                node = self.library.select(playlist_id)
            except KeyError:
                raise NotFound("Playlist not found")
            return self._reply("switch: active playlist is now “%s”" % node.playlist.name)

    def switch_playlist(self, direction):
        with self.lock:
            if self.library.current is None:
                raise BadRequest("Create a playlist first")
            if direction == "next":
                node = self.library.next_playlist()
                note = "current = current.next (O(1))"
            else:
                node = self.library.prev_playlist()
                note = "walked the ring to find the node before current (O(n), singly linked)"
            return self._reply("%s_playlist: “%s” — %s" % (direction, node.playlist.name, note))

    # ---- songs (always in the active playlist) --------------------------
    def add_song(self, title, artist="", album="", duration=None, index=None):
        with self.lock:
            playlist = self._current_playlist()
            title = clean_text(title, "title", 100, required=True)
            artist = clean_text(artist, "artist", 100)
            album = clean_text(album, "album", 100)
            seconds = parse_duration(duration)
            index = parse_index(index)
            before = playlist.size
            node = playlist.insert(self._new_song(title, artist, album, seconds), index)
            pos = playlist.index_of(node)
            if before == 0:
                note = "list was empty: head = tail = new node"
            elif pos == 0:
                note = "new head: new.next = old head, old head.prev = new"
            elif pos == playlist.size - 1:
                note = "new tail: old tail.next = new, new.prev = old tail"
            else:
                note = "spliced between %s and %s" % (q(node.prev.song), q(node.next.song))
            return self._reply("insert(%d): %s — %s" % (pos, q(node.song), note))

    def delete_song(self, song_id):
        with self.lock:
            playlist = self._current_playlist()
            song_id = parse_id(song_id, "song id")
            try:
                song, was_current = playlist.remove(song_id)
            except KeyError:
                raise NotFound("Song not found")
            message = "delete: unlinked %s, neighbours re-joined" % q(song)
            if was_current:
                if playlist.current is not None:
                    message += "; now playing moved to %s" % q(playlist.current.song)
                else:
                    message += "; playlist is empty"
            return self._reply(message)

    def move_song(self, song_id, index):
        with self.lock:
            playlist = self._current_playlist()
            song_id = parse_id(song_id, "song id")
            index = parse_index(index)
            if index is None:
                raise BadRequest("index is required")
            try:
                node = playlist.move(song_id, index)
            except KeyError:
                raise NotFound("Song not found")
            return self._reply("move: %s relinked at position %d" % (q(node.song), playlist.index_of(node) + 1))

    # ---- player ---------------------------------------------------------
    def play(self, song_id, playlist_id=None):
        with self.lock:
            if playlist_id is not None:
                playlist_id = parse_id(playlist_id, "playlist id")
                if self.library.current is None or self.library.current.playlist.id != playlist_id:
                    try:
                        self.library.select(playlist_id)
                    except KeyError:
                        raise NotFound("Playlist not found")
            playlist = self._current_playlist()
            song_id = parse_id(song_id, "song id")
            try:
                node = playlist.play(song_id)
            except KeyError:
                raise NotFound("Song not found")
            return self._reply("play: current → %s (found by walking the list, O(n))" % q(node.song))

    def next(self, auto=False):
        with self.lock:
            playlist = self._current_playlist()
            if playlist.current is None:
                raise BadRequest("This playlist is empty")
            node, stopped = playlist.next(auto=bool(auto))
            if stopped:
                message = "next: ran off the tail (repeat off) — stopped, cued back on head %s" % q(node.song)
            elif auto and playlist.repeat == ONE:
                message = "auto-next: repeat one — current stays on %s" % q(node.song)
            else:
                message = "next: current = current.next → %s" % q(node.song)
            return self._reply(message, stopped=stopped)

    def prev(self):
        with self.lock:
            playlist = self._current_playlist()
            if playlist.current is None:
                raise BadRequest("This playlist is empty")
            node = playlist.prev()
            return self._reply("prev: current = current.prev → %s" % q(node.song))

    def set_repeat(self, mode):
        with self.lock:
            playlist = self._current_playlist()
            if mode not in REPEAT_MODES:
                raise BadRequest("mode must be one of: " + ", ".join(REPEAT_MODES))
            playlist.set_repeat(mode)
            if mode == ALL:
                note = "ring closed: tail.next → head, head.prev → tail (circular doubly linked list)"
            elif mode == ONE:
                note = "ring open (null at both ends); finished track replays itself"
            else:
                note = "ring open: tail.next = null, head.prev = null"
            return self._reply("repeat %s: %s" % (mode, note))

    def reset(self):
        with self.lock:
            self._reset_memory()
            self._seed()
            return self._reply("reset: demo library restored")
