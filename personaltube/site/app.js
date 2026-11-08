'use strict';

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

const ICON = {
  hide: '<circle cx="12" cy="12" r="8"/><path d="m7 17 10-10"/>',
  like: '<path d="M7 11v9H4v-9zM7 11l4-7c1.5 0 2.5 1 2.2 2.6L12.6 10H19a2 2 0 0 1 2 2.3l-1.2 6A2 2 0 0 1 17.8 20H7"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;

let FEED;          // site/feed.json, written by personaltube.py
let ST;            // watched / liked / hidden / seen, kept by the local server
const byId = new Map();
const interestName = new Map();
let feedList = []; // ranked once per page load; a reload gives a new mix
let rendered = 0;
let savedY = 0;
let moreObserver;

// ---------- talking to the local server ----------
const post = (event) => fetch('/api/event', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(event), keepalive: true,
}).catch(() => {});
const info = (v) => ({ title: v.title, channel: v.channel, handle: v.handle, interest: v.interest });

// Cards that stayed on screen count as "seen"; ignored videos sink on the next reload.
let seenQueue = [];
const seenObserver = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    seenQueue.push(e.target.dataset.id);
    seenObserver.unobserve(e.target);
  }
}, { threshold: 0.6 });
const flushSeen = () => { if (seenQueue.length) { post({ type: 'seen', ids: seenQueue }); seenQueue = []; } };
setInterval(flushSeen, 4000);
addEventListener('pagehide', flushSeen);

// ---------- ranking ----------
function ageDays(v) {
  const t = v.date ? new Date(+v.date.slice(0, 4), +v.date.slice(4, 6) - 1, +v.date.slice(6, 8)).getTime() : v.found * 1000;
  return Math.max(0, (Date.now() - t) / 864e5);
}

function affinity() {
  const ch = {};
  const it = {};
  const add = (x, wc, wi) => {
    if (x.handle) ch[x.handle] = (ch[x.handle] || 0) + wc;
    if (x.interest) it[x.interest] = (it[x.interest] || 0) + wi;
  };
  for (const x of Object.values(ST.watched)) add(x, 1, 0.5);
  for (const x of Object.values(ST.liked)) add(x, 3, 1.5);
  for (const x of Object.values(ST.hidden)) add(x, -2.5, -1);
  return { ch, it };
}

function rank() {
  const aff = affinity();
  const weight = Object.fromEntries(FEED.interests.map((i) => [i.id, Math.sqrt(i.weight)]));
  const scored = FEED.videos
    .filter((v) => !ST.watched[v.id] && !ST.hidden[v.id])
    .map((v) => [
      ((v.score - 4) / 6) ** 2                                   // AI score: 6 -> 0.11, 10 -> 1
      * (weight[v.interest] || 1)                                // how big this interest is in your history
      * clamp(Math.exp(0.2 * (aff.ch[v.handle] || 0)), 0.2, 4)   // channels you watch, like or hide
      * clamp(Math.exp(0.15 * (aff.it[v.interest] || 0)), 0.3, 3)
      * (0.3 + 0.7 * Math.exp(-ageDays(v) / 30))                 // fresh videos first
      * 0.5 ** (ST.seen[v.id] || 0)                              // shown before and not clicked
      * (0.6 + Math.random() * 0.8),                             // a new mix on every reload
      v,
    ]);
  return diversify(scored, 500);
}

// Best first, but the same channel or interest loses weight each time it appears.
function diversify(scored, n) {
  const groups = new Map();
  for (const [s, v] of scored) (groups.get(v.handle) || groups.set(v.handle, []).get(v.handle)).push([s, v]);
  for (const g of groups.values()) g.sort((a, b) => b[0] - a[0]);
  const used = new Map();
  const recent = [];
  const out = [];
  while (out.length < n && groups.size) {
    let best = null;
    let bestScore = -1;
    for (const [h, g] of groups) {
      const sameTopic = recent.filter((i) => i === g[0][1].interest).length;
      const s = g[0][0] * 0.5 ** (used.get(h) || 0) * 0.8 ** sameTopic;
      if (s > bestScore) { bestScore = s; best = h; }
    }
    const v = groups.get(best).shift()[1];
    out.push(v);
    used.set(best, (used.get(best) || 0) + 1);
    recent.push(v.interest);
    if (recent.length > 6) recent.shift();
    if (!groups.get(best).length) groups.delete(best);
  }
  return out;
}

// ---------- formatting ----------
function num(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 1e4) return Math.round(n / 1e3) + 'K';
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
  return String(n);
}
function dur(s) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}
function ago(days) {
  const unit = (n, w) => `${n} ${w}${n === 1 ? '' : 's'} ago`;
  if (days < 1) return 'today';
  if (days < 7) return unit(Math.floor(days), 'day');
  if (days < 30) return unit(Math.floor(days / 7), 'week');
  if (days < 365) return unit(Math.floor(days / 30), 'month');
  return unit(Math.floor(days / 365), 'year');
}
const stats = (v) => [v.views ? num(v.views) + ' views' : '', v.date ? ago(ageDays(v)) : ''].filter(Boolean).join(' · ');
function avatar(v) {
  let h = 0;
  for (const c of v.handle || v.channel) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `<span class="av" style="--h:${h}" aria-hidden="true">${esc((v.channel || '?').trim()[0] || '?')}</span>`;
}
const thumb = (v) => `<a class="thumb" href="#/watch/${v.id}"><img src="https://i.ytimg.com/vi/${v.id}/mqdefault.jpg" alt="" loading="lazy"><span class="dur">${dur(v.dur)}</span></a>`;

const card = (v) => `<article class="card${ST.watched[v.id] ? ' seen' : ''}" data-id="${v.id}">
  ${thumb(v)}
  <div class="meta">${avatar(v)}
    <div class="txt">
      <a class="title" href="#/watch/${v.id}" title="${esc(v.title)}">${esc(v.title)}</a>
      <span class="sub">${esc(v.channel)}</span>
      <span class="sub">${stats(v)}</span>
    </div>
    <button class="icon-btn" data-act="hide" title="Not interested">${icon('hide')}</button>
  </div>
</article>`;

const mini = (v) => `<div class="mini">${thumb(v)}<div class="txt">
  <a class="title" href="#/watch/${v.id}" title="${esc(v.title)}">${esc(v.title)}</a>
  <span class="sub">${esc(v.channel)}</span><span class="sub">${stats(v)}</span></div></div>`;

// ---------- feed ----------
const view = $('#view');

function showFeed() {
  if (!feedList.length) {
    view.innerHTML = '<p class="empty">No videos yet. The first search for videos takes about 15-20 minutes. This page reloads by itself when it is done.</p>';
    if (ST.updating) {
      const poll = setInterval(() => fetch('/api/state').then((r) => r.json()).then((s) => {
        if (!s.updating) { clearInterval(poll); location.reload(); }
      }).catch(() => {}), 20000);
    }
    return;
  }
  view.innerHTML = '<div class="grid" id="grid"></div><div id="more"></div>';
  const target = Math.max(30, rendered);
  rendered = 0;
  appendCards(target);
  moreObserver = new IntersectionObserver((e) => e[0].isIntersecting && appendCards(30), { rootMargin: '1200px' });
  moreObserver.observe($('#more'));
  requestAnimationFrame(() => window.scrollTo(0, savedY));
}

function appendCards(n) {
  const grid = $('#grid');
  if (!grid) return;
  const part = feedList.slice(rendered, rendered + n);
  grid.insertAdjacentHTML('beforeend', part.map(card).join(''));
  for (const el of [...grid.children].slice(rendered)) if (!ST.watched[el.dataset.id]) seenObserver.observe(el);
  rendered += part.length;
  if (rendered >= feedList.length) {
    moreObserver?.disconnect();
    if (!$('.end')) view.insertAdjacentHTML('beforeend', '<div class="end">That is everything for now.<br><button class="pill" onclick="location.reload()">Show a new mix</button></div>');
  }
}

// ---------- watch page ----------
let ytApi;
const loadYt = () => (ytApi ||= new Promise((resolve) => {
  window.onYouTubeIframeAPIReady = resolve;
  const s = document.createElement('script');
  s.src = 'https://www.youtube.com/iframe_api';
  document.head.append(s);
}));

function watch(id) {
  const v = byId.get(id);
  if (!v) {
    view.innerHTML = `<p class="empty">This video is not in your feed anymore. <a href="https://www.youtube.com/watch?v=${esc(id)}" target="_blank" rel="noopener"><u>Open it on YouTube</u></a></p>`;
    return;
  }
  if (!ST.watched[id]) {
    ST.watched[id] = info(v);
    post({ type: 'watch', id, video: info(v) });
  }
  const others = feedList.filter((x) => x.id !== id && !ST.watched[x.id] && !ST.hidden[x.id]);
  const next = [...new Set([
    ...others.filter((x) => x.handle === v.handle).slice(0, 3),
    ...others.filter((x) => x.interest === v.interest).slice(0, 6),
    ...others,
  ])].slice(0, 20);
  const liked = !!ST.liked[id];
  view.innerHTML = `<div class="watch" data-id="${id}">
    <div>
      <div class="player"><div id="player"></div></div>
      <h1 class="w-title">${esc(v.title)}</h1>
      <div class="w-row">
        <span class="w-ch">${avatar(v)}${esc(v.channel)}</span>
        <div class="w-actions">
          <button class="pill${liked ? ' on' : ''}" data-act="like" aria-pressed="${liked}">${icon('like')}Like</button>
          <button class="pill" data-act="hide">${icon('hide')}Not interested</button>
          <a class="pill" href="https://www.youtube.com/watch?v=${id}" target="_blank" rel="noopener">${icon('external')}YouTube</a>
        </div>
      </div>
      <div class="why"><b>${stats(v)}</b><p>Picked for you: ${esc(interestName.get(v.interest) || v.interest)} · AI match ${v.score}/10</p></div>
    </div>
    <aside class="next"><h2>Up next</h2>${next.map(mini).join('')}</aside>
  </div>`;
  window.scrollTo(0, 0);

  loadYt().then(() => {
    if (!$('#player')) return; // already left the page
    new YT.Player('player', {
      videoId: id,
      playerVars: { autoplay: 1, rel: 0, playsinline: 1, hl: 'en', cc_lang_pref: 'en' },
      events: { onError: (e) => blocked(id, e.data) },
    });
  });
}

function blocked(id, code) {
  const why = code === 101 || code === 150 ? 'The owner does not allow this video on other sites.' : 'This video cannot play here.';
  $('.player')?.insertAdjacentHTML('beforeend', `<div class="fallback"><div>${why}</div>
    <a class="pill" href="https://www.youtube.com/watch?v=${id}" target="_blank" rel="noopener">${icon('external')}Watch on YouTube</a></div>`);
}

// ---------- actions ----------
function toast(text, undo) {
  $('.toast')?.remove();
  const el = document.createElement('div');
  el.className = 'toast';
  el.innerHTML = `<span>${esc(text)}</span>${undo ? '<button>Undo</button>' : ''}`;
  if (undo) el.querySelector('button').onclick = () => { undo(); el.remove(); };
  document.body.append(el);
  setTimeout(() => el.remove(), 5000);
}

document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const root = btn.closest('[data-id]');
  const v = byId.get(root.dataset.id);
  if (btn.dataset.act === 'like') {
    const on = !ST.liked[v.id];
    if (on) ST.liked[v.id] = info(v); else delete ST.liked[v.id];
    post({ type: on ? 'like' : 'unlike', id: v.id, video: info(v) });
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-pressed', on);
  } else if (btn.dataset.act === 'hide') {
    ST.hidden[v.id] = info(v);
    post({ type: 'hide', id: v.id, video: info(v) });
    root.classList.add('gone');
    const undo = () => {
      delete ST.hidden[v.id];
      post({ type: 'unhide', id: v.id });
      root.classList.remove('gone');
    };
    if (root.classList.contains('watch')) history.back();
    toast('Got it, fewer videos like this', root.classList.contains('watch') ? null : undo);
  }
});

// ---------- navigation ----------
function route() {
  $('.toast')?.remove();
  const m = location.hash.match(/^#\/watch\/([\w-]{11})$/);
  if (m) {
    if (!$('.watch')) savedY = window.scrollY;
    moreObserver?.disconnect();
    watch(m[1]);
  } else {
    showFeed();
  }
}

function showStatus() {
  if (!FEED.generated) { $('#status').textContent = ST.updating ? 'Looking for videos…' : ''; return; }
  const hours = (Date.now() - new Date(FEED.generated).getTime()) / 36e5;
  const when = hours < 1 ? 'less than an hour ago' : hours < 48 ? `${Math.round(hours)} h ago` : `${Math.round(hours / 24)} days ago`;
  $('#status').textContent = ST.updating ? 'Looking for new videos…' : `${FEED.videos.length.toLocaleString('en')} videos · updated ${when}`;
}

window.addEventListener('hashchange', route);

Promise.all([
  fetch('/feed.json').then((r) => r.json()),
  fetch('/api/state').then((r) => r.json()),
]).then(([feed, state]) => {
  FEED = feed;
  ST = state;
  for (const v of FEED.videos) byId.set(v.id, v);
  for (const i of FEED.interests) interestName.set(i.id, i.name);
  feedList = rank();
  showStatus();
  route();
}).catch(() => {
  view.innerHTML = '<p class="empty">Start PersonalTube with <code>personaltube serve</code> and open this page again.</p>';
});
