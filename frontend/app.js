/* Loopback - frontend. Plain JavaScript, no build step.
 *
 * The server owns the data structures (see backend/structures.py). This file
 * only draws what the server reports and sends the user's actions back.
 * Playback itself is simulated with a clock: each song just "plays" for its
 * duration, then the client asks the server to advance the linked list.
 */
(() => {
  'use strict';

  /* ------------------------------------------------------------ helpers */
  const $ = (sel, root = document) => root.querySelector(sel);

  function el(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [key, val] of Object.entries(attrs)) {
        if (val == null || val === false) continue;
        if (key === 'class') node.className = val;
        else if (key.startsWith('on')) node.addEventListener(key.slice(2), val);
        else if (val === true) node.setAttribute(key, '');
        else node.setAttribute(key, val);
      }
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return node;
  }

  const fmt = (seconds) => {
    const s = Math.max(0, Math.floor(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = String(s % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
  };
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const ptr = (id) => (id == null ? 'null' : `#${id}`);

  const SVG_NS = 'http://www.w3.org/2000/svg';
  const ICONS = {
    play: ['fill', 'M8 5v14l11-7z'],
    pause: ['fill', 'M6 5h4v14H6zM14 5h4v14h-4z'],
    prev: ['fill', 'M6 5h2v14H6zM20 5v14L9 12z'],
    next: ['fill', 'M16 5h2v14h-2zM4 5v14l11-7z'],
    repeat: ['stroke', 'M17 2l4 4-4 4', 'M3 11V9a3 3 0 0 1 3-3h15', 'M7 22l-4-4 4-4', 'M21 13v2a3 3 0 0 1-3 3H3'],
    repeat1: ['stroke', 'M17 2l4 4-4 4', 'M3 11V9a3 3 0 0 1 3-3h15', 'M7 22l-4-4 4-4', 'M21 13v2a3 3 0 0 1-3 3H3', 'M11 10h1v4'],
    sun: ['stroke', 'M16 12a4 4 0 1 0-8 0a4 4 0 1 0 8 0', 'M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4'],
    moon: ['stroke', 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z'],
    search: ['stroke', 'M18 11a7 7 0 1 0-14 0a7 7 0 1 0 14 0', 'M21 21l-4.3-4.3'],
    plus: ['stroke', 'M12 5v14M5 12h14'],
    x: ['stroke', 'M18 6L6 18M6 6l12 12'],
    up: ['stroke', 'M12 19V5M5 12l7-7 7 7'],
    down: ['stroke', 'M12 5v14M19 12l-7 7-7-7'],
    trash: ['stroke', 'M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v6M14 11v6'],
  };

  function icon(name, size = 18) {
    const [kind, ...paths] = ICONS[name];
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', size);
    svg.setAttribute('height', size);
    svg.setAttribute('aria-hidden', 'true');
    if (kind === 'fill') {
      svg.setAttribute('fill', 'currentColor');
    } else {
      svg.setAttribute('fill', 'none');
      svg.setAttribute('stroke', 'currentColor');
      svg.setAttribute('stroke-width', '2');
      svg.setAttribute('stroke-linecap', 'round');
      svg.setAttribute('stroke-linejoin', 'round');
    }
    for (const d of paths) {
      const p = document.createElementNS(SVG_NS, 'path');
      p.setAttribute('d', d);
      svg.append(p);
    }
    return svg;
  }
  const setIcon = (node, name, size) => node.replaceChildren(icon(name, size));

  /* -------------------------------------------------------------- state */
  const S = {
    data: null, // last snapshot from the server: { library, playlist }
    playing: false,
    elapsed: 0, // seconds into the current song (client clock)
    last: performance.now(),
    seeking: false,
    advancing: false,
    log: [],
    scope: 'current',
  };

  const currentSong = () => {
    const p = S.data && S.data.playlist;
    if (!p || p.current_id == null) return null;
    return p.songs.find((s) => s.id === p.current_id) || null;
  };
  const currentKey = () => {
    const p = S.data && S.data.playlist;
    return p ? `${p.id}:${p.current_id}` : '';
  };

  /* ---------------------------------------------------------------- api */
  async function api(method, path, body) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: method === 'GET' ? undefined : JSON.stringify(body || {}),
      });
    } catch (e) {
      throw new Error('Cannot reach the server. Is it still running?');
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON error page */ }
    if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
    return data;
  }

  function applyReply(reply, opts = {}) {
    const before = currentKey();
    S.data = reply.state;
    if (opts.play) S.playing = true;
    if (opts.pause || reply.stopped) S.playing = false;
    if (opts.reset || currentKey() !== before) S.elapsed = 0;
    S.last = performance.now();
    if (reply.message) pushLog(reply.message);
    render();
  }

  async function act(method, path, body, opts) {
    try {
      const reply = await api(method, path, body);
      applyReply(reply, opts);
      return reply;
    } catch (e) {
      toast(e.message);
      return null;
    }
  }

  let toastTimer = 0;
  function toast(message) {
    const t = $('#toast');
    t.textContent = message;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 3600);
  }

  function pushLog(message) {
    const time = new Date().toLocaleTimeString([], { hour12: false });
    S.log.unshift({ time, message });
    if (S.log.length > 60) S.log.length = 60;
  }

  /* ------------------------------------------------------------ actions */
  function togglePlay() {
    const song = currentSong();
    if (!song) return;
    S.playing = !S.playing;
    if (S.playing && S.elapsed >= song.duration) S.elapsed = 0;
    S.last = performance.now();
    render();
  }

  const nextTrack = () => (currentSong() ? act('POST', '/api/player/next', { auto: false }, { reset: true }) : null);

  function prevTrack() {
    if (!currentSong()) return null;
    if (S.elapsed > 3) { // like most players: first press restarts the song
      S.elapsed = 0;
      S.last = performance.now();
      paintProgress();
      return null;
    }
    return act('POST', '/api/player/prev', {}, { reset: true });
  }

  function playSong(id) {
    return act('POST', '/api/player/play', { song_id: id }, { reset: true, play: true });
  }

  function cycleRepeat() {
    const p = S.data && S.data.playlist;
    if (!p) return null;
    const order = ['off', 'all', 'one'];
    const mode = order[(order.indexOf(p.repeat) + 1) % order.length];
    return act('POST', '/api/player/repeat', { mode });
  }

  async function onTrackEnd() {
    if (S.advancing) return;
    S.advancing = true;
    try {
      const reply = await act('POST', '/api/player/next', { auto: true }, { reset: true });
      if (!reply) { S.playing = false; render(); }
    } finally {
      S.advancing = false;
      S.last = performance.now();
    }
  }

  function selectPlaylist(p) {
    if (p.is_current) return null;
    return act('POST', `/api/playlists/${p.id}/select`, {}, { reset: true });
  }

  function deletePlaylist(p) {
    if (!window.confirm(`Delete “${p.name}” and its ${plural(p.count, 'song')}?`)) return null;
    return act('DELETE', `/api/playlists/${p.id}`, {});
  }

  function revealRow(id) {
    requestAnimationFrame(() => {
      const row = document.querySelector(`.row[data-id="${id}"]`);
      if (row) row.scrollIntoView({ block: 'nearest' });
    });
  }

  /* ---------------------------------------------------------- rendering */
  function render() {
    if (!S.data) return;
    if (!currentSong()) S.playing = false;
    renderLibrary();
    renderStage();
    renderPlayer();
  }

  function renderLibrary() {
    const lib = S.data.library;
    $('#lib-meta').textContent = `circular list · ${plural(lib.size, 'node')}`;
    $('#lib-prev').disabled = lib.size < 2;
    $('#lib-next').disabled = lib.size < 2;
    const list = $('#lib-list');
    list.replaceChildren();
    if (!lib.playlists.length) {
      list.append(el('li', { class: 'empty small' }, 'No playlists yet. Create one below.'));
      return;
    }
    for (const p of lib.playlists) {
      list.append(
        el('li', { class: 'lib-item' },
          el('button', {
            class: 'pl' + (p.is_current ? ' is-current' : ''),
            type: 'button',
            'aria-current': p.is_current ? 'true' : null,
            onclick: () => selectPlaylist(p),
          },
            el('span', { class: 'pl-name' }, p.name),
            el('span', { class: 'pl-meta' },
              el('span', null, plural(p.count, 'song')),
              p.repeat !== 'off' ? el('span', null, `↻ ${p.repeat}`) : null)),
          el('button', {
            class: 'icon-btn lib-del', type: 'button',
            title: `Delete ${p.name}`, 'aria-label': `Delete playlist ${p.name}`,
            onclick: () => deletePlaylist(p),
          }, icon('trash', 15))));
    }
  }

  function renderStage() {
    const p = S.data.playlist;
    $('#pl-name').textContent = p ? p.name : 'No playlist yet';
    $('#pl-meta').textContent = p
      ? `${plural(p.size, 'song')} · ${fmt(p.duration)} · repeat ${p.repeat}`
      : 'Create a playlist in the library to get started.';
    $('#add-btn').disabled = !p;
    renderQueue(p);
    renderViz(p);
    renderLog();
  }

  let dragId = null;
  const clearDropMarks = () => document.querySelectorAll('.drop-target').forEach((n) => n.classList.remove('drop-target'));

  function renderQueue(p) {
    const list = $('#queue');
    list.replaceChildren();
    if (!p) {
      list.append(el('li', { class: 'empty' }, 'No playlist selected.'));
      return;
    }
    if (!p.songs.length) {
      list.append(el('li', { class: 'empty' }, 'This playlist is empty. Add a song above.'));
      return;
    }
    for (const s of p.songs) {
      const isCur = s.id === p.current_id;
      const row = el('li', {
        class: 'row' + (isCur ? ' is-current' : ''),
        draggable: 'true',
        'data-id': s.id,
        onclick: () => (isCur ? togglePlay() : playSong(s.id)),
      },
        el('span', { class: 'idx mono' + (isCur ? ' idx-cur' : '') },
          isCur && S.playing ? el('span', { class: 'eq', 'aria-label': 'Playing' }, el('i'), el('i'), el('i')) : String(s.index + 1)),
        el('div', { class: 'row-main' },
          el('button', { class: 'row-title', type: 'button', title: s.title, 'aria-label': `Play ${s.title}` }, s.title),
          el('div', { class: 'row-artist' }, s.artist || 'Unknown artist')),
        el('span', { class: 'row-album' }, s.album || '—'),
        el('span', { class: 'row-time mono' }, fmt(s.duration)),
        el('div', { class: 'row-acts' },
          el('button', {
            class: 'icon-btn', type: 'button', title: 'Move up', 'aria-label': `Move ${s.title} up`,
            disabled: s.index === 0,
            onclick: (e) => { e.stopPropagation(); act('POST', `/api/songs/${s.id}/move`, { index: s.index - 1 }); },
          }, icon('up', 15)),
          el('button', {
            class: 'icon-btn', type: 'button', title: 'Move down', 'aria-label': `Move ${s.title} down`,
            disabled: s.index === p.size - 1,
            onclick: (e) => { e.stopPropagation(); act('POST', `/api/songs/${s.id}/move`, { index: s.index + 1 }); },
          }, icon('down', 15)),
          el('button', {
            class: 'icon-btn danger', type: 'button', title: 'Delete song', 'aria-label': `Delete ${s.title}`,
            onclick: (e) => { e.stopPropagation(); act('DELETE', `/api/songs/${s.id}`, {}); },
          }, icon('x', 15))));

      row.addEventListener('dragstart', (e) => {
        dragId = s.id;
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', String(s.id));
        row.classList.add('dragging');
      });
      row.addEventListener('dragend', () => { dragId = null; row.classList.remove('dragging'); clearDropMarks(); });
      row.addEventListener('dragover', (e) => {
        if (dragId == null) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        row.classList.add('drop-target');
      });
      row.addEventListener('dragleave', () => row.classList.remove('drop-target'));
      row.addEventListener('drop', (e) => {
        e.preventDefault();
        const from = dragId;
        dragId = null;
        clearDropMarks();
        if (from != null && from !== s.id) act('POST', `/api/songs/${from}/move`, { index: s.index });
      });
      list.append(row);
    }
  }

  function tagsFor(pairs) {
    return el('div', { class: 'tags' },
      pairs.filter(([on]) => on).map(([, name]) => el('span', { class: 'tag tag-' + name.toLowerCase() }, name)));
  }

  function renderViz(p) {
    // ---- playlist: doubly linked list (circular when repeat = all)
    const box = $('#viz-songs');
    box.replaceChildren();
    if (!p) {
      box.append(el('p', { class: 'dim' }, 'No playlist.'));
    } else {
      $('#viz-kind').textContent = p.circular ? 'circular doubly linked list' : 'doubly linked list';
      if (!p.songs.length) {
        box.append(el('p', { class: 'mono dim small' }, 'head = null, tail = null, size = 0'));
      } else {
        const chain = el('div', { class: 'chain', role: 'img', 'aria-label': `Linked list of ${plural(p.size, 'node')}` });
        chain.append(el('span', { class: 'end mono' + (p.circular ? ' ring' : '') }, p.circular ? '↻' : 'null'));
        p.songs.forEach((s, i) => {
          if (i > 0) chain.append(el('span', { class: 'link mono' }, '⇄'));
          chain.append(
            el('div', { class: 'node' + (s.id === p.current_id ? ' is-current' : '') },
              tagsFor([[s.id === p.head_id, 'HEAD'], [s.id === p.tail_id, 'TAIL'], [s.id === p.current_id, 'CURRENT']]),
              el('div', { class: 'node-id mono' }, `node #${s.id}`),
              el('div', { class: 'node-title' }, s.title),
              el('div', { class: 'ptrs mono' }, el('span', null, `prev ${ptr(s.prev_id)}`), el('span', null, `next ${ptr(s.next_id)}`))));
        });
        chain.append(el('span', { class: 'end mono' + (p.circular ? ' ring' : '') }, p.circular ? '↻' : 'null'));
        box.append(chain);
        const first = p.songs[0];
        const last = p.songs[p.songs.length - 1];
        box.append(el('p', { class: 'caption mono' },
          p.circular
            ? [el('b', null, 'ring closed'), ` · tail.next → ${ptr(first.id)} (head) · head.prev → ${ptr(last.id)} (tail)`]
            : `tail.next → null · head.prev → null · repeat ${p.repeat}`));
      }
    }

    // ---- library: circular singly linked list
    const lib = S.data.library;
    const libBox = $('#viz-library');
    libBox.replaceChildren();
    if (!lib.playlists.length) {
      libBox.append(el('p', { class: 'mono dim small' }, 'tail = null, size = 0'));
      return;
    }
    const chain = el('div', { class: 'chain', role: 'img', 'aria-label': `Ring of ${plural(lib.size, 'playlist')}` });
    lib.playlists.forEach((pl, i) => {
      if (i > 0) chain.append(el('span', { class: 'link mono' }, '→'));
      chain.append(
        el('div', { class: 'node' + (pl.is_current ? ' is-current' : '') },
          tagsFor([[pl.id === lib.head_id, 'HEAD'], [pl.id === lib.tail_id, 'TAIL'], [pl.is_current, 'CURRENT']]),
          el('div', { class: 'node-id mono' }, `playlist #${pl.id}`),
          el('div', { class: 'node-title' }, pl.name),
          el('div', { class: 'ptrs mono' }, el('span', null, `next ${ptr(pl.next_id)}`))));
    });
    chain.append(el('span', { class: 'end mono ring', title: 'tail.next points back to the head' }, '↻ head'));
    libBox.append(chain);
    libBox.append(el('p', { class: 'caption mono' },
      [el('b', null, 'circular'), ` · tail.next → ${ptr(lib.head_id)} (head). No null pointers anywhere in the ring.`]));
  }

  function renderLog() {
    const log = $('#log');
    log.replaceChildren();
    if (!S.log.length) {
      log.append(el('li', null, el('time', null, '--:--:--'), el('span', null, 'Nothing yet. Add, delete or move a song.')));
      return;
    }
    for (const entry of S.log) {
      log.append(el('li', null, el('time', { class: 'mono' }, entry.time), el('span', null, entry.message)));
    }
  }

  function renderPlayer() {
    const p = S.data.playlist;
    const song = currentSong();
    $('#player').classList.toggle('playing', S.playing && !!song);
    $('#np-title').textContent = song ? song.title : p ? 'Playlist is empty' : 'No playlist';
    $('#np-artist').textContent = song ? song.artist || 'Unknown artist' : '';
    $('#np-pos').textContent = song ? `track ${p.current_index + 1} of ${p.size}` : '';

    const mode = p ? p.repeat : 'off';
    const repeatLabels = { off: 'Repeat off', all: 'Repeat playlist', one: 'Repeat one song' };
    const rb = $('#btn-repeat');
    setIcon(rb, mode === 'one' ? 'repeat1' : 'repeat', 18);
    rb.classList.toggle('on', mode !== 'off');
    rb.disabled = !p;
    rb.title = `${repeatLabels[mode]} (R)`;
    rb.setAttribute('aria-label', `${repeatLabels[mode]}. Press to change.`);
    rb.setAttribute('aria-pressed', mode !== 'off' ? 'true' : 'false');
    const info = $('#np-repeat');
    info.textContent = p ? `repeat ${mode}` : '';
    info.classList.toggle('active', mode !== 'off');

    const pb = $('#btn-play');
    setIcon(pb, S.playing ? 'pause' : 'play', 20);
    pb.disabled = !song;
    pb.setAttribute('aria-label', S.playing ? 'Pause' : 'Play');
    $('#btn-prev').disabled = !song;
    $('#btn-next').disabled = !song;
    $('#seek').disabled = !song;
    $('#t-total').textContent = song ? fmt(song.duration) : '0:00';
    document.title = song && S.playing ? `${song.title} · Loopback` : 'Loopback · Music Playlist Manager';
    paintProgress();
  }

  function paintProgress() {
    const song = currentSong();
    const total = song ? song.duration : 0;
    const shown = Math.min(S.elapsed, total);
    $('#t-elapsed').textContent = fmt(shown);
    if (S.seeking) return;
    const ratio = total ? shown / total : 0;
    const seek = $('#seek');
    seek.value = String(Math.round(ratio * 1000));
    seek.style.setProperty('--pct', `${(ratio * 100).toFixed(2)}%`);
  }

  /* -------------------------------------------------------------- clock */
  setInterval(() => {
    const now = performance.now();
    const dt = (now - S.last) / 1000;
    S.last = now;
    const song = currentSong();
    if (S.playing && song && !S.seeking) {
      S.elapsed = Math.min(S.elapsed + dt, song.duration);
      if (S.elapsed >= song.duration) onTrackEnd();
    }
    if (S.data) paintProgress();
  }, 200);

  /* ------------------------------------------------------------- search */
  const searchInput = $('#search');
  const resultsBox = $('#results');
  let searchTimer = 0;
  let searchSeq = 0;
  let lastResults = [];

  function closeResults() {
    searchSeq++; // drop any in-flight request
    resultsBox.hidden = true;
    resultsBox.replaceChildren();
    lastResults = [];
  }

  function highlight(text, needle) {
    if (!needle) return [text];
    const out = [];
    const lower = text.toLowerCase();
    const n = needle.toLowerCase();
    let i = 0;
    for (;;) {
      const j = lower.indexOf(n, i);
      if (j < 0) { out.push(text.slice(i)); break; }
      if (j > i) out.push(text.slice(i, j));
      out.push(el('mark', null, text.slice(j, j + n.length)));
      i = j + n.length;
    }
    return out;
  }

  async function runSearch() {
    const query = searchInput.value.trim();
    if (!query) { closeResults(); return; }
    const seq = ++searchSeq;
    try {
      const data = await api('GET', `/api/search?q=${encodeURIComponent(query)}&scope=${S.scope}`);
      if (seq === searchSeq) renderResults(data, query);
    } catch (e) {
      if (seq === searchSeq) toast(e.message);
    }
  }

  function renderResults(data, query) {
    lastResults = data.results;
    resultsBox.replaceChildren();
    const scopeBtn = (value, label) => el('button', {
      type: 'button', 'aria-pressed': S.scope === value ? 'true' : 'false',
      onclick: () => { S.scope = value; runSearch(); searchInput.focus(); },
    }, label);
    resultsBox.append(
      el('div', { class: 'res-head' },
        el('div', { class: 'seg', role: 'group', 'aria-label': 'Search scope' },
          scopeBtn('current', 'This playlist'), scopeBtn('all', 'All playlists')),
        el('span', { class: 'mono dim small' }, plural(data.results.length, 'match').replace('matchs', 'matches'))));
    if (!data.results.length) {
      resultsBox.append(el('p', { class: 'res-empty' }, `Nothing matches “${query}”.`));
    }
    for (const r of data.results) {
      resultsBox.append(
        el('button', { class: 'res', type: 'button', onclick: () => playResult(r) },
          el('span', { class: 'mono dim small' }, `#${r.index + 1}`),
          el('span', { class: 'res-main' },
            el('b', null, highlight(r.title, query)),
            el('span', null, [...highlight(r.artist || 'Unknown artist', query), r.album ? ' · ' : '', ...(r.album ? highlight(r.album, query) : [])])),
          S.scope === 'all' ? el('span', { class: 'badge', title: r.playlist_name }, r.playlist_name) : null));
    }
    resultsBox.hidden = false;
  }

  async function playResult(r) {
    closeResults();
    searchInput.value = '';
    const reply = await act('POST', '/api/player/play',
      { song_id: r.song_id, playlist_id: r.playlist_id }, { reset: true, play: true });
    if (reply) revealRow(r.song_id);
  }

  searchInput.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 120); });
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (searchInput.value) { searchInput.value = ''; closeResults(); } else searchInput.blur();
    } else if (e.key === 'ArrowDown') {
      const first = $('.res', resultsBox);
      if (first) { e.preventDefault(); first.focus(); }
    } else if (e.key === 'Enter' && lastResults.length) {
      e.preventDefault();
      playResult(lastResults[0]);
    }
  });
  resultsBox.addEventListener('keydown', (e) => {
    const items = [...resultsBox.querySelectorAll('.res')];
    const i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' && i < items.length - 1) { e.preventDefault(); items[i + 1].focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); (i > 0 ? items[i - 1] : searchInput).focus(); }
    else if (e.key === 'Escape') { searchInput.focus(); closeResults(); searchInput.value = ''; }
  });
  document.addEventListener('click', (e) => {
    if (!$('#search-wrap').contains(e.target)) closeResults();
  });

  /* ------------------------------------------------------------ wiring */
  function paintThemeButton() {
    const dark = document.documentElement.dataset.theme === 'dark';
    const btn = $('#theme');
    setIcon(btn, dark ? 'sun' : 'moon', 18);
    const label = dark ? 'Switch to light theme' : 'Switch to dark theme';
    btn.title = label;
    btn.setAttribute('aria-label', label);
  }

  function setupStatic() {
    setIcon($('#search-icon'), 'search', 16);
    setIcon($('#lib-prev'), 'up', 16);
    setIcon($('#lib-next'), 'down', 16);
    setIcon($('#new-pl-btn'), 'plus', 16);
    setIcon($('#btn-prev'), 'prev', 18);
    setIcon($('#btn-next'), 'next', 18);
    setIcon($('#btn-play'), 'play', 20);
    setIcon($('#btn-repeat'), 'repeat', 18);
    paintThemeButton();

    $('#theme').addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem('loopback-theme', next); } catch (e) { /* private mode */ }
      paintThemeButton();
    });

    $('#btn-play').addEventListener('click', togglePlay);
    $('#btn-next').addEventListener('click', nextTrack);
    $('#btn-prev').addEventListener('click', prevTrack);
    $('#btn-repeat').addEventListener('click', cycleRepeat);
    $('#lib-next').addEventListener('click', () => act('POST', '/api/library/next', {}, { reset: true }));
    $('#lib-prev').addEventListener('click', () => act('POST', '/api/library/prev', {}, { reset: true }));

    const seek = $('#seek');
    seek.addEventListener('input', () => {
      const song = currentSong();
      if (!song) return;
      S.seeking = true;
      const ratio = Number(seek.value) / 1000;
      S.elapsed = ratio * song.duration;
      seek.style.setProperty('--pct', `${(ratio * 100).toFixed(2)}%`);
      $('#t-elapsed').textContent = fmt(S.elapsed);
    });
    seek.addEventListener('change', () => { S.seeking = false; S.last = performance.now(); });

    $('#new-pl').addEventListener('submit', async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      const reply = await act('POST', '/api/playlists', { name: new FormData(form).get('name') });
      if (reply) form.reset();
    });

    const addForm = $('#add-form');
    addForm.elements.where.addEventListener('change', (e) => {
      $('#at-wrap').hidden = e.target.value !== 'at';
    });
    addForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const p = S.data && S.data.playlist;
      if (!p) { toast('Create a playlist first.'); return; }
      const fd = new FormData(addForm);
      let index = null;
      switch (fd.get('where')) {
        case 'start': index = 0; break;
        case 'after': index = p.current_index == null ? null : p.current_index + 1; break;
        case 'at': {
          const n = parseInt(fd.get('at'), 10);
          if (!Number.isFinite(n) || n < 1) { toast('Position must be 1 or more.'); return; }
          index = n - 1;
          break;
        }
        default: index = null;
      }
      const reply = await act('POST', '/api/songs', {
        title: fd.get('title'), artist: fd.get('artist'), album: fd.get('album'), duration: fd.get('duration'), index,
      });
      if (reply) {
        for (const name of ['title', 'artist', 'album', 'duration']) addForm.elements[name].value = '';
        addForm.elements.title.focus();
      }
    });

    $('#reset-demo').addEventListener('click', () => {
      if (window.confirm('Replace everything with the demo library?')) {
        act('POST', '/api/reset', {}, { reset: true, pause: true });
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = (e.target.tagName || '').toLowerCase();
      const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable;
      if (typing) return;
      switch (e.key) {
        case '/': e.preventDefault(); searchInput.focus(); searchInput.select(); break;
        case ' ':
          if (tag === 'button' || tag === 'summary' || tag === 'a') return; // let the focused control handle Space
          e.preventDefault(); togglePlay(); break;
        case 'n': case 'N': nextTrack(); break;
        case 'p': case 'P': prevTrack(); break;
        case 'r': case 'R': cycleRepeat(); break;
        default:
      }
    });
  }

  async function init() {
    setupStatic();
    try {
      const reply = await api('GET', '/api/state');
      S.data = reply.state;
      pushLog(`loaded ${plural(S.data.library.size, 'playlist')} from the server`);
      render();
    } catch (e) {
      $('#pl-name').textContent = 'Server unreachable';
      $('#pl-meta').textContent = e.message;
      toast(e.message);
    }
  }
  init();
})();
