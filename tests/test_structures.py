"""Unit tests for the linked lists. Run:  python3 -m unittest discover -s tests -v"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from structures import ALL, OFF, ONE, Library, Playlist, Song  # noqa: E402


def song(n):
    return Song(n, "Song %d" % n, "Artist", "Album", 100 + n)


def make(ids=(), repeat=OFF):
    pl = Playlist(1, "test")
    for i in ids:
        pl.insert(song(i))
    pl.set_repeat(repeat)
    return pl


def order(pl):
    return [n.song.id for n in pl]


def check_invariants(tc, pl):
    """Structural health check used by every test and by the fuzz test."""
    if pl.size == 0:
        tc.assertIsNone(pl.head)
        tc.assertIsNone(pl.tail)
        tc.assertIsNone(pl.current)
        return
    forward = list(pl)
    tc.assertEqual(len(forward), pl.size)
    tc.assertIs(forward[0], pl.head)
    tc.assertIs(forward[-1], pl.tail)
    # every pair of neighbours must point at each other
    for a, b in zip(forward, forward[1:]):
        tc.assertIs(a.next, b)
        tc.assertIs(b.prev, a)
    if pl.circular:
        tc.assertIs(pl.tail.next, pl.head)
        tc.assertIs(pl.head.prev, pl.tail)
    else:
        tc.assertIsNone(pl.tail.next)
        tc.assertIsNone(pl.head.prev)
    # walking backwards from the tail gives the reverse order
    backward = []
    node = pl.tail
    for _ in range(pl.size):
        backward.append(node)
        node = node.prev
    tc.assertEqual(backward, forward[::-1])
    if pl.current is not None:
        tc.assertTrue(any(n is pl.current for n in forward))


class InsertTests(unittest.TestCase):
    def test_append_and_empty(self):
        pl = make()
        check_invariants(self, pl)
        for i in (1, 2, 3):
            pl.insert(song(i))
            check_invariants(self, pl)
        self.assertEqual(order(pl), [1, 2, 3])

    def test_insert_head_middle_tail(self):
        pl = make([1, 2, 3])
        pl.insert(song(10), 0)
        pl.insert(song(11), 2)
        pl.insert(song(12), 99)  # clamped to the end
        pl.insert(song(13), -5)  # clamped to the start
        check_invariants(self, pl)
        self.assertEqual(order(pl), [13, 10, 1, 11, 2, 3, 12])

    def test_first_song_becomes_current(self):
        pl = make()
        node = pl.insert(song(1))
        self.assertIs(pl.current, node)
        pl.insert(song(2))
        self.assertIs(pl.current, node)

    def test_insert_before_current_keeps_current(self):
        pl = make([1, 2, 3])
        pl.play(2)
        pl.insert(song(9), 0)
        pl.insert(song(8), 1)
        self.assertEqual(pl.current.song.id, 2)
        self.assertEqual(pl.next()[0].song.id, 3)
        check_invariants(self, pl)


class RemoveTests(unittest.TestCase):
    def test_remove_each_position(self):
        for target, expected in ((1, [2, 3, 4]), (3, [1, 2, 4]), (4, [1, 2, 3])):
            pl = make([1, 2, 3, 4])
            pl.remove(target)
            check_invariants(self, pl)
            self.assertEqual(order(pl), expected)

    def test_remove_only_song(self):
        pl = make([1])
        pl.remove(1)
        check_invariants(self, pl)
        self.assertEqual(len(pl), 0)
        self.assertIsNone(pl.current)

    def test_remove_missing_raises(self):
        with self.assertRaises(KeyError):
            make([1]).remove(99)

    def test_remove_non_current_keeps_current(self):
        pl = make([1, 2, 3, 4])
        pl.play(3)
        pl.remove(1)
        pl.remove(4)
        self.assertEqual(pl.current.song.id, 3)

    def test_remove_current_moves_to_next_then_previous(self):
        pl = make([1, 2, 3])
        pl.play(2)
        _, was_current = pl.remove(2)
        self.assertTrue(was_current)
        self.assertEqual(pl.current.song.id, 3)  # next song
        pl.remove(3)
        self.assertEqual(pl.current.song.id, 1)  # tail removed -> previous

    def test_remove_current_in_ring_wraps_to_head(self):
        pl = make([1, 2, 3], repeat=ALL)
        pl.play(3)
        pl.remove(3)
        self.assertEqual(pl.current.song.id, 1)
        check_invariants(self, pl)


class NavigationTests(unittest.TestCase):
    def test_linear_next_prev(self):
        pl = make([1, 2, 3])
        self.assertEqual(pl.current.song.id, 1)
        self.assertEqual(pl.next()[0].song.id, 2)
        self.assertEqual(pl.next()[0].song.id, 3)
        node, stopped = pl.next()  # ran off the tail
        self.assertTrue(stopped)
        self.assertEqual(node.song.id, 1)
        self.assertEqual(pl.prev().song.id, 1)  # prev on the head stays put

    def test_repeat_all_wraps_both_ways(self):
        pl = make([1, 2, 3], repeat=ALL)
        pl.play(3)
        node, stopped = pl.next()
        self.assertEqual((node.song.id, stopped), (1, False))
        self.assertEqual(pl.prev().song.id, 3)

    def test_repeat_all_single_song(self):
        pl = make([1], repeat=ALL)
        check_invariants(self, pl)
        node, stopped = pl.next(auto=True)
        self.assertEqual((node.song.id, stopped), (1, False))
        self.assertEqual(pl.prev().song.id, 1)

    def test_repeat_one(self):
        pl = make([1, 2, 3], repeat=ONE)
        self.assertEqual(pl.next(auto=True)[0].song.id, 1)  # track ended: replay
        self.assertEqual(pl.next(auto=False)[0].song.id, 2)  # user skip: advance

    def test_toggle_repeat_opens_and_closes_ring(self):
        pl = make([1, 2, 3])
        check_invariants(self, pl)
        pl.set_repeat(ALL)
        check_invariants(self, pl)
        pl.set_repeat(OFF)
        check_invariants(self, pl)
        with self.assertRaises(ValueError):
            pl.set_repeat("bogus")

    def test_empty_playlist_navigation(self):
        pl = make()
        self.assertEqual(pl.next(), (None, False))
        self.assertIsNone(pl.prev())


class MoveAndSearchTests(unittest.TestCase):
    def test_move_keeps_current(self):
        pl = make([1, 2, 3, 4])
        pl.play(2)
        pl.move(2, 3)
        self.assertEqual(order(pl), [1, 3, 4, 2])
        self.assertEqual(pl.current.song.id, 2)
        pl.move(4, 0)
        self.assertEqual(order(pl), [4, 1, 3, 2])
        check_invariants(self, pl)

    def test_move_in_ring(self):
        pl = make([1, 2, 3], repeat=ALL)
        pl.move(1, 2)
        self.assertEqual(order(pl), [2, 3, 1])
        check_invariants(self, pl)

    def test_search(self):
        pl = Playlist(1, "s")
        pl.insert(Song(1, "Blue Monday", "Orbit", "Night"))
        pl.insert(Song(2, "Red", "Blue Harbor", ""))
        pl.insert(Song(3, "Green", "Moss", "Blue Hour"))
        pl.insert(Song(4, "Yellow", "Sun", ""))
        hits = [(i, n.song.id) for i, n in pl.search("  BLUE ")]
        self.assertEqual(hits, [(0, 1), (1, 2), (2, 3)])
        self.assertEqual(pl.search("zzz"), [])
        self.assertEqual(pl.search("   "), [])


class FuzzTests(unittest.TestCase):
    """Random operations checked against a plain Python list as the model."""

    def test_random_operations(self):
        rng = random.Random(20261024)
        for _ in range(60):
            pl = Playlist(1, "fuzz")
            model = []  # song ids in order
            cur = None  # id of the current song, or None
            mode = OFF
            next_id = 1
            for _ in range(250):
                op = rng.choice(
                    ["insert", "insert", "remove", "move", "next", "prev", "play", "repeat"]
                )
                n = len(model)
                if op == "insert":
                    idx = rng.choice([None, rng.randint(-1, n + 1)])
                    pl.insert(song(next_id), idx)
                    pos = n if idx is None else max(0, min(idx, n))
                    model.insert(pos, next_id)
                    if cur is None:
                        cur = next_id
                    next_id += 1
                elif op == "remove" and n:
                    sid = rng.choice(model)
                    i = model.index(sid)
                    if sid == cur:
                        if n == 1:
                            cur = None
                        elif mode == ALL:
                            cur = model[(i + 1) % n]
                        elif i + 1 < n:
                            cur = model[i + 1]
                        else:
                            cur = model[i - 1]
                    pl.remove(sid)
                    model.remove(sid)
                elif op == "move" and n:
                    sid = rng.choice(model)
                    to = rng.randint(0, n - 1)
                    pl.move(sid, to)
                    model.remove(sid)
                    model.insert(to, sid)
                elif op == "next" and n:
                    auto = rng.random() < 0.5
                    node, stopped = pl.next(auto=auto)
                    i = model.index(cur)
                    if auto and mode == ONE:
                        pass
                    elif mode == ALL:
                        cur = model[(i + 1) % n]
                    elif i + 1 < n:
                        cur = model[i + 1]
                    else:
                        cur = model[0]
                        self.assertTrue(stopped)
                elif op == "prev" and n:
                    pl.prev()
                    i = model.index(cur)
                    cur = model[(i - 1) % n] if mode == ALL else model[max(i - 1, 0)]
                elif op == "play" and n:
                    cur = rng.choice(model)
                    pl.play(cur)
                elif op == "repeat":
                    mode = rng.choice([OFF, ALL, ONE])
                    pl.set_repeat(mode)
                check_invariants(self, pl)
                self.assertEqual(order(pl), model)
                self.assertEqual(pl.current.song.id if pl.current else None, cur)


class LibraryTests(unittest.TestCase):
    def build(self, n):
        lib = Library()
        for i in range(1, n + 1):
            lib.add(Playlist(i, "P%d" % i))
        return lib

    def ids(self, lib):
        return [node.playlist.id for node in lib]

    def check(self, lib):
        if lib.size == 0:
            self.assertIsNone(lib.tail)
            self.assertIsNone(lib.current)
            return
        node = lib.head
        for _ in range(lib.size):
            node = node.next
        self.assertIs(node, lib.head)  # walking `size` steps closes the ring
        self.assertIs(lib.tail.next, lib.head)
        self.assertTrue(any(n is lib.current for n in lib))

    def test_add_and_order(self):
        lib = self.build(4)
        self.assertEqual(self.ids(lib), [1, 2, 3, 4])
        self.assertEqual(lib.current.playlist.id, 1)
        self.check(lib)

    def test_next_prev_wrap(self):
        lib = self.build(3)
        lib.prev_playlist()
        self.assertEqual(lib.current.playlist.id, 3)
        lib.next_playlist()
        self.assertEqual(lib.current.playlist.id, 1)
        lib.next_playlist()
        lib.next_playlist()
        lib.next_playlist()
        self.assertEqual(lib.current.playlist.id, 1)

    def test_single_playlist_ring(self):
        lib = self.build(1)
        lib.next_playlist()
        lib.prev_playlist()
        self.assertEqual(lib.current.playlist.id, 1)
        self.check(lib)

    def test_remove_variants(self):
        for target, expected in ((1, [2, 3, 4]), (2, [1, 3, 4]), (4, [1, 2, 3])):
            lib = self.build(4)
            lib.remove(target)
            self.assertEqual(self.ids(lib), expected)
            self.check(lib)

    def test_remove_current_moves_forward(self):
        lib = self.build(3)
        lib.select(3)
        lib.remove(3)
        self.assertEqual(lib.current.playlist.id, 1)  # wraps to head
        self.check(lib)

    def test_remove_all(self):
        lib = self.build(2)
        lib.remove(1)
        lib.remove(2)
        self.check(lib)
        self.assertIsNone(lib.next_playlist())
        with self.assertRaises(KeyError):
            lib.remove(1)

    def test_add_after_remove_all(self):
        lib = self.build(1)
        lib.remove(1)
        lib.add(Playlist(9, "new"))
        self.assertEqual(self.ids(lib), [9])
        self.check(lib)


if __name__ == "__main__":
    unittest.main()
