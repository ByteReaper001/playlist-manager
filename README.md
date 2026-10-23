# Loopback: Music Playlist and Playback Management System

**Project LL01 · Data Structures**

A music player whose playlists are **hand-written linked lists**. You can add and delete songs, step to the next or previous track, repeat a playlist, search, and switch between several playlists. Navigation stays correct while songs are inserted or removed.

The Python backend implements the data structures and a small JSON API. The frontend is plain HTML, CSS and JavaScript. There is no framework, no build step and **no third-party package to install**.

---

## Run it

Requires Python 3.8 or newer.

```bash
python3 backend/server.py
# open http://127.0.0.1:8000
```

| Option | Meaning |
| --- | --- |
| `--port 9000` | use another port |
| `--open` | open the browser automatically |
| `--reset` | throw away saved data and restore the demo library |
| `--data path.json` | save the library somewhere else (default `data/library.json`) |

Changes are saved automatically to `data/library.json` after every action.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

There are 42 tests:

* **Data structures:** insert, delete, move, next, previous and repeat at the head, middle and tail. It also checks the ring invariants after every operation. A fuzz test runs thousands of random operations and compares the lists against a plain Python list.
* **API:** a real HTTP server, validation errors, saving and loading, corrupt-file recovery, path-traversal protection and cross-site request protection.

---

## The data structures (`backend/structures.py`)

```
Library: circular SINGLY linked list of playlists (tail.next = head)
 ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
 │ Late Night   │ ──▶ │ Morning Run  │ ──▶ │ Rainy Eve    │ ──┐
 └──────────────┘     └──────────────┘     └──────────────┘   │
        ▲                                                      │
        └──────────────────────────────────────────────────────┘

Playlist: DOUBLY linked list of songs

 null ⇄ [HEAD A] ⇄ [ B ] ⇄ [CURRENT C] ⇄ [TAIL D] ⇄ null        repeat off / one

 ┌─▶ [HEAD A] ⇄ [ B ] ⇄ [CURRENT C] ⇄ [TAIL D] ◀─┐                repeat all
 └────────────────────────────────────────────────┘                (circular)
```

| Structure | Where | Why this one |
| --- | --- | --- |
| **Doubly linked list** | `Playlist` (`SongNode` has `prev` and `next`) | Next and previous are O(1). A node can be unlinked without searching for its predecessor. Indexed access walks from whichever end is nearer. |
| **Circular doubly linked list** | The same `Playlist` when repeat is **all**. `tail.next = head` and `head.prev = tail`. | "Repeat playlist" is a wrap-around, so there is no special case in `next()` or `prev()`. Turning repeat off opens the ring again. |
| **Circular singly linked list** | `Library` (`PlaylistNode` has only `next`, and the list keeps a `tail` pointer) | Switching to the next playlist is O(1) and wraps by itself. Appending at the tail is O(1). Going to the *previous* playlist needs an O(n) walk, which shows the trade-off of a singly linked list. |

No Python `list`, `deque` or `dict` stores songs or playlists. Python lists appear only when a snapshot is exported to JSON for the browser.

### Keeping navigation correct when songs are inserted or removed

"Now playing" is stored as a **node pointer** (`Playlist.current`), never as an index. Inserting, deleting or reordering other songs cannot make it point at the wrong track. The only dangerous case is deleting the current node itself, and `Playlist.remove()` handles it:

1. Pick a replacement before unlinking: the next song (wrapping to the head in a ring), otherwise the previous song, otherwise `None` if the playlist is now empty.
2. Unlink the node and re-join its neighbours.
3. Point `current` at the replacement.

Other rules:

* When the first song is added to an empty list, it becomes the cued track.
* In a linear list, `next` at the tail stops playback and cues the head. This is the same behaviour as most players.
* With repeat **one**, a track that *ends* replays itself, but pressing **Next** still advances. That is why `next(auto=True)` and `next(auto=False)` differ.
* `prev` restarts the song if it has been playing for more than 3 seconds, as in most players. Otherwise it goes to the previous node.

### Complexity

| Operation | Time | Note |
| --- | --- | --- |
| Next / previous song | O(1) | follow one pointer |
| Add at head or tail | O(1) | |
| Insert at index *i* | O(min(i, n−i)) | walks from the nearer end |
| Delete a song (given its node) | O(1) | finding it by id is O(n) |
| Delete by id / play by id | O(n) | linear search |
| Move a song | O(n) | unlink, then link at the new index |
| Search | O(n) | case-insensitive substring match on title, artist and album |
| Toggle repeat | O(1) | only the two end pointers change |
| Next playlist | O(1) | |
| Previous playlist | O(n) | singly linked, so walk the ring |
| Add playlist | O(1) | thanks to the tail pointer |

---

## Features

* **Playlists:** create, delete, switch by clicking, or use the up and down arrows to walk the ring.
* **Songs:** add with title, artist, album and length (`m:ss`). Insert at the end, at the start, right after the song now playing, or at any position. Delete, reorder with the arrows or by drag and drop.
* **Playback:** play, pause, next, previous, seek, and a progress bar. Playback is *simulated*: each song "plays" for its length on a clock, then the next node is chosen. No audio files are involved.
* **Repeat:** off → all (circular) → one.
* **Search:** in this playlist or across all playlists, with highlighted matches. Pick a result to jump to it.
* **Under the hood panel:** draws the real `prev` and `next` pointers of every node, plus the HEAD, TAIL and CURRENT markers. It shows the ring closing when you press repeat. An operation log explains what each action did to the pointers, which is useful for a demo or viva.
* **Keyboard:** `Space` play or pause, `N` next, `P` previous, `R` repeat, `/` search.
* The data is saved between runs.

## API

All requests and responses are JSON. Every change returns `{ state, message, stopped }`. `state` is the complete library, `message` describes what happened to the pointers, and `stopped` is true when a linear playlist ran off its tail.

| Method | Path | Body |
| --- | --- | --- |
| GET | `/api/state` | |
| GET | `/api/search?q=…&scope=current\|all` | |
| POST | `/api/playlists` | `{name}` |
| DELETE | `/api/playlists/:id` | |
| POST | `/api/playlists/:id/select` | |
| POST | `/api/library/next` and `/api/library/prev` | |
| POST | `/api/songs` | `{title, artist?, album?, duration?, index?}` |
| DELETE | `/api/songs/:id` | |
| POST | `/api/songs/:id/move` | `{index}` |
| POST | `/api/player/play` | `{song_id, playlist_id?}` |
| POST | `/api/player/next` | `{auto?}` |
| POST | `/api/player/prev` | |
| POST | `/api/player/repeat` | `{mode: "off"\|"all"\|"one"}` |
| POST | `/api/reset` | |

Song operations always act on the active playlist. POST and DELETE requests must send `Content-Type: application/json`, which stops other websites from posting to your local server.

## Project layout

```
backend/
  structures.py   Song, SongNode, Playlist (doubly / circular), Library (circular singly)
  service.py      validation, pointer-level messages, JSON persistence, demo data
  server.py       standard-library HTTP server and router, serves the frontend
frontend/
  index.html      structure
  style.css       charcoal and ivory themes, Geist type
  app.js          rendering, playback clock, search, drag and drop, shortcuts
  fonts/          Geist and Geist Mono (SIL Open Font License, included)
tests/
  test_structures.py   data-structure tests and fuzz test
  test_api.py          end-to-end HTTP tests
```

## Design

* **Type:** Geist for the interface and Geist Mono for labels, times and the data-structure diagrams. Both are bundled in `frontend/fonts`, so the app works offline.
* **Themes:** charcoal and ivory, each warm-tinted so neither theme is pure black or pure white. The accent colour is ember orange in both themes. The theme follows your system setting on first visit, and the toggle is saved. Body text and accent colour meet WCAG AA contrast in both themes.

| Token | Dark (charcoal) | Light (ivory) |
| --- | --- | --- |
| Background | `#1c1c1a` | `#f3eee0` |
| Surface | `#252523` | `#faf6ea` |
| Text | `#ede8d8` | `#25241f` |
| Muted text | `#9c9885` | `#6a675a` |
| Accent | `#ef7d4f` | `#b9461f` |

## Possible extensions

Shuffle (a Fisher–Yates pass that relinks the nodes), an undo stack, a "play next" queue, importing real audio files, and a hash map from song id to node to make lookups O(1).
