"""
structures.py - the data structures behind the player.

Two hand-written linked lists live here:

1. Playlist -> DOUBLY linked list of songs (SongNode has prev + next).
   When repeat mode is "all" the list is closed into a ring
   (tail.next = head, head.prev = tail), so it becomes a CIRCULAR
   DOUBLY linked list. Turning repeat off opens the ring again.

2. Library  -> CIRCULAR SINGLY linked list of playlists (PlaylistNode has
   only next). It keeps a tail pointer, so tail.next is always the head and
   adding a playlist is O(1).

No Python list / deque / dict is used to hold songs or playlists. Python lists
only appear when exporting a snapshot to JSON for the frontend.

Key idea for correct navigation
-------------------------------
"Now playing" is stored as a NODE POINTER (Playlist.current), never as an
index. Inserting or deleting other songs therefore cannot make it point at the
wrong song. Only deleting the current node itself needs special handling, and
that is done in Playlist.remove().
"""

from __future__ import annotations

OFF, ALL, ONE = "off", "all", "one"
REPEAT_MODES = (OFF, ALL, ONE)


# --------------------------------------------------------------------------
# Songs and song nodes
# --------------------------------------------------------------------------
class Song:
    """Plain data record for one track."""

    __slots__ = ("id", "title", "artist", "album", "duration")

    def __init__(self, id, title, artist="", album="", duration=180):
        self.id = id
        self.title = title
        self.artist = artist
        self.album = album
        self.duration = duration  # seconds

    def matches(self, needle):
        """Case-insensitive substring match on title, artist or album."""
        return (
            needle in self.title.lower()
            or needle in self.artist.lower()
            or needle in self.album.lower()
        )

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration": self.duration,
        }


class SongNode:
    """One node of the doubly linked list."""

    __slots__ = ("song", "prev", "next")

    def __init__(self, song):
        self.song = song
        self.prev = None
        self.next = None


# --------------------------------------------------------------------------
# Playlist: doubly linked list (optionally circular)
# --------------------------------------------------------------------------
class Playlist:
    def __init__(self, id, name):
        self.id = id
        self.name = name
        self.head = None
        self.tail = None
        self.size = 0
        self.current = None  # SongNode that is cued / playing
        self._repeat = OFF

    # ---- repeat mode / ring handling ------------------------------------
    @property
    def repeat(self):
        return self._repeat

    @property
    def circular(self):
        return self._repeat == ALL

    def set_repeat(self, mode):
        if mode not in REPEAT_MODES:
            raise ValueError("repeat mode must be one of: " + ", ".join(REPEAT_MODES))
        self._repeat = mode
        self._sync_ring()

    def _sync_ring(self):
        """Make the two end pointers match the repeat mode. O(1).

        circular  : tail.next -> head   and   head.prev -> tail
        otherwise : tail.next -> None   and   head.prev -> None
        """
        if self.head is None:
            return
        if self.circular:
            self.tail.next = self.head
            self.head.prev = self.tail
        else:
            self.tail.next = None
            self.head.prev = None

    # ---- basic container protocol ---------------------------------------
    def __len__(self):
        return self.size

    def __iter__(self):
        """Yield nodes from head to tail.

        Counts `size` steps instead of waiting for None, so it works both for
        the linear list and for the ring.
        """
        node = self.head
        for _ in range(self.size):
            yield node
            node = node.next

    def total_duration(self):
        return sum(node.song.duration for node in self)

    # ---- low-level pointer surgery --------------------------------------
    def _node_at(self, index):
        """Return the node at `index`, walking from the nearer end. O(n/2)."""
        if index < 0 or index >= self.size:
            raise IndexError(index)
        if index <= self.size // 2:
            node = self.head
            for _ in range(index):
                node = node.next
        else:
            node = self.tail
            for _ in range(self.size - 1 - index):
                node = node.prev
        return node

    def _link_at(self, node, index):
        """Splice `node` into the list so that it ends up at `index`."""
        index = max(0, min(index, self.size))
        if self.size == 0:
            self.head = self.tail = node
            node.prev = node.next = None
        elif index == 0:  # new head
            node.prev = None
            node.next = self.head
            self.head.prev = node
            self.head = node
        elif index == self.size:  # new tail
            node.next = None
            node.prev = self.tail
            self.tail.next = node
            self.tail = node
        else:  # somewhere in the middle: put it before the node now at `index`
            after = self._node_at(index)
            before = after.prev
            node.prev = before
            node.next = after
            before.next = node
            after.prev = node
        self.size += 1
        self._sync_ring()

    def _unlink(self, node):
        """Cut `node` out of the list and re-join its neighbours."""
        if self.size == 1:
            self.head = self.tail = None
        elif node is self.head:
            self.head = node.next
            self.head.prev = None
        elif node is self.tail:
            self.tail = node.prev
            self.tail.next = None
        else:
            node.prev.next = node.next
            node.next.prev = node.prev
        node.prev = node.next = None
        self.size -= 1
        self._sync_ring()

    @staticmethod
    def _neighbour(node):
        """The node playback should fall back to if `node` disappears.

        Prefer the next song (wrapping to the head in a ring), otherwise the
        previous one, otherwise None (it was the only song).
        """
        if node.next is not None and node.next is not node:
            return node.next
        if node.prev is not None and node.prev is not node:
            return node.prev
        return None

    # ---- public operations ----------------------------------------------
    def insert(self, song, index=None):
        """Insert `song` at `index` (None = append). Returns the new node. O(n)."""
        node = SongNode(song)
        self._link_at(node, self.size if index is None else index)
        if self.current is None:  # first song in the list: cue it
            self.current = node
        return node

    def find_node(self, song_id):
        """Linear search by id. O(n)."""
        for node in self:
            if node.song.id == song_id:
                return node
        return None

    def index_of(self, node):
        i = 0
        for n in self:
            if n is node:
                return i
            i += 1
        return -1

    def remove(self, song_id):
        """Delete a song. Returns (song, was_current).

        If the removed node was the current one, `current` moves to its
        neighbour (see _neighbour) so the player never points at a dead node.
        """
        node = self.find_node(song_id)
        if node is None:
            raise KeyError(song_id)
        was_current = node is self.current
        replacement = self._neighbour(node) if was_current else None
        self._unlink(node)
        if was_current:
            self.current = replacement
        return node.song, was_current

    def move(self, song_id, to_index):
        """Move a song so that it ends up at `to_index`. `current` is untouched."""
        node = self.find_node(song_id)
        if node is None:
            raise KeyError(song_id)
        self._unlink(node)
        self._link_at(node, to_index)
        return node

    def play(self, song_id):
        node = self.find_node(song_id)
        if node is None:
            raise KeyError(song_id)
        self.current = node
        return node

    def next(self, auto=False):
        """Advance to the next track. Returns (node, stopped).

        auto=True means "the track ended by itself" (as opposed to the user
        pressing Next). With repeat == one, only auto-advance replays the same
        track. `stopped` is True when a linear list ran off its tail: the
        player is cued back on the first song and should stop.
        """
        if self.current is None:
            return None, False
        if auto and self._repeat == ONE:
            return self.current, False
        nxt = self.current.next  # in a ring this is never None
        if nxt is None:
            self.current = self.head
            return self.current, True
        self.current = nxt
        return nxt, False

    def prev(self):
        """Step back one track (stays on the first song of a linear list)."""
        if self.current is None:
            return None
        back = self.current.prev  # in a ring this is never None
        if back is None:
            back = self.current
        self.current = back
        return back

    def search(self, query):
        """Return [(index, node), ...] for songs matching `query`. O(n)."""
        needle = query.strip().lower()
        if not needle:
            return []
        hits = []
        index = 0
        for node in self:
            if node.song.matches(needle):
                hits.append((index, node))
            index += 1
        return hits

    # ---- export ----------------------------------------------------------
    def snapshot(self):
        """JSON-friendly view. prev_id / next_id are read from the REAL
        pointers, so the frontend visualiser shows what the list truly holds."""
        songs = []
        current_index = None
        duration = 0
        index = 0
        for node in self:
            if node is self.current:
                current_index = index
            d = node.song.to_dict()
            d["index"] = index
            d["prev_id"] = node.prev.song.id if node.prev is not None else None
            d["next_id"] = node.next.song.id if node.next is not None else None
            songs.append(d)
            duration += node.song.duration
            index += 1
        return {
            "id": self.id,
            "name": self.name,
            "repeat": self._repeat,
            "circular": self.circular,
            "size": self.size,
            "duration": duration,
            "head_id": self.head.song.id if self.head else None,
            "tail_id": self.tail.song.id if self.tail else None,
            "current_id": self.current.song.id if self.current else None,
            "current_index": current_index,
            "songs": songs,
        }


# --------------------------------------------------------------------------
# Library: circular singly linked list of playlists
# --------------------------------------------------------------------------
class PlaylistNode:
    __slots__ = ("playlist", "next")

    def __init__(self, playlist):
        self.playlist = playlist
        self.next = None


class Library:
    """Circular singly linked list. `tail.next` is the head."""

    def __init__(self):
        self.tail = None
        self.size = 0
        self.current = None  # PlaylistNode that is active

    @property
    def head(self):
        return self.tail.next if self.tail is not None else None

    def __len__(self):
        return self.size

    def __iter__(self):
        node = self.head
        for _ in range(self.size):
            yield node
            node = node.next

    def add(self, playlist):
        """Append at the tail in O(1). The first playlist becomes active."""
        node = PlaylistNode(playlist)
        if self.tail is None:
            node.next = node  # a ring of one
            self.tail = node
            self.current = node
        else:
            node.next = self.tail.next  # new node points at the head
            self.tail.next = node
            self.tail = node
        self.size += 1
        return node

    def find_node(self, playlist_id):
        for node in self:
            if node.playlist.id == playlist_id:
                return node
        return None

    def remove(self, playlist_id):
        """Delete a playlist. If it was active, the next one becomes active."""
        if self.tail is None:
            raise KeyError(playlist_id)
        prev = self.tail
        node = self.tail.next  # head
        for _ in range(self.size):
            if node.playlist.id == playlist_id:
                break
            prev, node = node, node.next
        else:
            raise KeyError(playlist_id)

        if self.size == 1:
            self.tail = None
            self.current = None
        else:
            prev.next = node.next
            if node is self.tail:
                self.tail = prev
            if node is self.current:
                self.current = node.next
        node.next = None
        self.size -= 1
        return node.playlist

    def select(self, playlist_id):
        node = self.find_node(playlist_id)
        if node is None:
            raise KeyError(playlist_id)
        self.current = node
        return node

    def next_playlist(self):
        """Move to the next playlist, wrapping around. O(1)."""
        if self.current is None:
            return None
        self.current = self.current.next
        return self.current

    def prev_playlist(self):
        """Move to the previous playlist, wrapping around.

        A singly linked list has no prev pointer, so we walk the ring until we
        find the node whose next is the current one. O(n).
        """
        if self.current is None:
            return None
        node = self.current
        while node.next is not self.current:
            node = node.next
        self.current = node
        return node
