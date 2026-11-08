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

// "..." at the end of a message about work in progress, cycling ... .. . ..
const DOTS = ['...', '..', '.', '..'];
let dotFrame = 0;
const dots = () => `<span class="dots">${DOTS[dotFrame]}</span>`;
setInterval(() => {
  dotFrame = (dotFrame + 1) % DOTS.length;
  for (const el of document.querySelectorAll('.dots')) el.textContent = DOTS[dotFrame];
}, 400);

let FEED;          // site/feed.json, written by personaltube.py
let ST;            // watched / liked / hidden / seen, kept by the local server
let PROG = {};     // progress of a running update: requests done, videos found, checked, added
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
const flushSeen = () => {
  if (!seenQueue.length) return Promise.resolve();
  const ids = seenQueue;
  seenQueue = [];
  return post({ type: 'seen', ids });
};
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

function rank(videos = FEED.videos) {
  const aff = affinity();
  const weight = Object.fromEntries(FEED.interests.map((i) => [i.id, Math.sqrt(i.weight)]));
  const scored = videos
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
const thumb = (v) => `<a class="thumb" href="#/watch/${v.id}"><img src="https://i.ytimg.com/vi/${v.id}/mqdefault.jpg" alt="" loading="lazy" decoding="async"><span class="dur">${dur(v.dur)}</span></a>`;

// `d` >= 0 fades the card in, d steps later than the first card of its batch.
const card = (v, d = -1) => `<article class="card${ST.watched[v.id] ? ' seen' : ''}${d >= 0 ? ' new' : ''}"${d >= 0 ? ` style="--d:${d}"` : ''} data-id="${v.id}">
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
// The feed stays in the page, hidden, while a video or the setup is open, so going back is instant
// even after hundreds of cards. Everything else is drawn in `page`.
const feedEl = $('#feed');
const page = $('#page');
// `off` keeps the cards' layout in memory (content-visibility), so showing them again is cheap.
const feedOff = () => feedEl.classList.contains('off');
const showPage = (html) => { feedEl.classList.add('off'); page.innerHTML = html; };

const onFeed = () => !/^#\/(watch|setup)/.test(location.hash);

function showFeed() {
  page.innerHTML = '';
  feedEl.classList.remove('off');
  if (!feedList.length) {
    feedEl.innerHTML = ST.updating
      ? `<div class="wait"><div class="spinner"></div><p>Looking for your first videos${dots()}</p><p class="live-line"></p></div>
         <div class="grid">${'<div class="skel"><i></i><b></b><b></b></div>'.repeat(8)}</div>`
      : '<p class="empty">No videos yet. <a href="#/setup"><u>Open the setup</u></a> and press "Open my feed".</p>';
    showStatus();
    return;
  }
  feedEl.innerHTML = '<div class="grid" id="grid"></div><div id="more"></div>';
  rendered = 0;
  appendCards(30, true);
  moreObserver?.disconnect();
  // A slow computer can get several changes at once; only the newest one counts.
  moreObserver = new IntersectionObserver((e) => e[e.length - 1].isIntersecting && fillDown(), { rootMargin: '1200px' });
  moreObserver.observe($('#more'));
  window.scrollTo(0, 0);
}

// Adds cards until the end of the list is well below the screen. The observer only reports changes,
// and on a slow computer a fast scroll can bring the end back into range before it notices it left,
// so it is armed again each time: observe() always reports the current state once.
function fillDown() {
  const more = $('#more');
  if (!more || feedOff() || rendered >= feedList.length) return;
  if (more.getBoundingClientRect().top > innerHeight + 1200) {
    moreObserver.unobserve(more);
    moreObserver.observe(more);
    return;
  }
  appendCards(30);
  requestAnimationFrame(fillDown);
}

// Only the first screen fades in: cards added by scrolling appear below the screen anyway.
function appendCards(n, animate = false) {
  const grid = $('#grid');
  if (!grid) return;
  const part = feedList.slice(rendered, rendered + n);
  grid.insertAdjacentHTML('beforeend', part.map((v, i) => card(v, animate ? Math.min(i, 12) : -1)).join(''));
  for (const el of [...grid.children].slice(rendered)) if (!ST.watched[el.dataset.id]) seenObserver.observe(el);
  rendered += part.length;
  if (rendered >= feedList.length) {
    moreObserver?.disconnect();
    if (!$('.end')) endBlock();
  }
}

// The bottom of the feed: a live search while an update runs, otherwise a new mix.
function endBlock() {
  $('.end')?.remove();
  feedEl.insertAdjacentHTML('beforeend', ST.updating
    ? `<div class="end"><div class="spinner"></div>Finding more videos${dots()}<br>New ones appear here as soon as the AI picks them.<p class="live-line"></p></div>`
    : '<div class="end">That is everything for now.<br><button class="pill" onclick="refresh()">Show a new mix</button></div>');
  showStatus();
}

function setFeed(feed) {
  FEED = feed;
  for (const v of FEED.videos) byId.set(v.id, v);
  for (const i of FEED.interests) interestName.set(i.id, i.name);
}

// Videos from a running update go below the cards already on the page, so nothing jumps.
async function refreshFeed() {
  const feed = await fetch('/feed.json').then((r) => r.json()).catch(() => null);
  if (!feed) return;
  setFeed(feed);
  const shown = feedList.slice(0, rendered);
  const ids = new Set(shown.map((v) => v.id));
  feedList = [...shown, ...rank(FEED.videos.filter((v) => !ids.has(v.id)))];
  if (!$('#grid')) {
    if (feedList.length && onFeed()) showFeed(); // the first videos replace the waiting screen
  } else if (rendered < feedList.length && $('.end')) {
    $('.end').remove();
    moreObserver.observe($('#more'));
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
    showPage(`<p class="empty">This video is not in your feed anymore. <a href="https://www.youtube.com/watch?v=${esc(id)}" target="_blank" rel="noopener"><u>Open it on YouTube</u></a></p>`);
    return;
  }
  if (!ST.watched[id]) {
    ST.watched[id] = info(v);
    post({ type: 'watch', id, video: info(v) });
    feedEl.querySelector(`.card[data-id="${id}"]`)?.classList.add('seen');
  }
  const others = feedList.filter((x) => x.id !== id && !ST.watched[x.id] && !ST.hidden[x.id]);
  const next = [...new Set([
    ...others.filter((x) => x.handle === v.handle).slice(0, 3),
    ...others.filter((x) => x.interest === v.interest).slice(0, 6),
    ...others,
  ])].slice(0, 20);
  const liked = !!ST.liked[id];
  showPage(`<div class="watch" data-id="${id}">
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
  </div>`);
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
    if (root.classList.contains('watch')) {
      feedEl.querySelector(`.card[data-id="${v.id}"]`)?.classList.add('gone');
      // route() clears toasts when the page changes, so this one waits until the feed is back
      addEventListener('hashchange', () => toast('Got it, fewer videos like this'), { once: true });
      history.back();
    } else {
      toast('Got it, fewer videos like this', undo);
    }
  }
});

// ---------- setup (first visit, and the gear icon later) ----------
let SETUP;

async function callApi(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  return r.json();
}

async function showSetup() {
  SETUP = await fetch('/api/setup').then((r) => r.json());
  renderSetup();
}

function renderSetup() {
  const S = SETUP;
  const step = (done, open) => (done ? 'done' : open ? 'active' : 'locked');
  const providers = S.providers.map((p) => `<label class="opt${p.installed ? '' : ' off'}">
      <input type="radio" name="provider" value="${p.id}"${p.id === S.provider ? ' checked' : ''}${p.installed ? '' : ' disabled'}>
      <span>${esc(p.label)}${p.installed ? '' : '<small>Not installed on this computer</small>'}</span></label>`).join('');
  const yt = S.youtube ? `Imported ${S.youtube.history.toLocaleString("en")} watched videos, ${S.youtube.likes.toLocaleString("en")} likes and ${S.youtube.subs.toLocaleString("en")} subscriptions.` : '';
  showPage(`<div class="setup">
    <h1>Set up PersonalTube</h1>
    <p class="muted">Three steps. Everything stays on this computer.</p>

    <section class="step ${step(S.ai_ok, true)}">
      <h2><span class="num">1</span>AI that picks your videos</h2>
      <div class="opts">${providers}</div>
      <div class="extra" data-for="openrouter">
        <label class="field">OpenRouter API key · <a href="https://openrouter.ai/keys" target="_blank" rel="noopener"><u>get a key</u></a>
        <input id="key-openrouter" type="password" autocomplete="off" placeholder="${S.has_key && S.provider === 'openrouter' ? 'Saved. Leave empty to keep it' : 'sk-or-...'}"></label>
      </div>
      <div class="extra" data-for="claude"><p class="muted">Uses your Claude Code login. If you never signed in, run <code>claude</code> in a terminal once.</p></div>
      <div class="extra" data-for="codex"><p class="muted">Uses your Codex CLI login. If you never signed in, run <code>codex login</code> in a terminal once.</p></div>
      <div class="extra" data-for="openai">
        <label class="field">API base URL<input id="base-url" value="${esc(S.provider === 'openai' ? S.base_url : '')}" placeholder="http://localhost:11434/v1"></label>
        <label class="field">Model<input id="model" value="${esc(S.provider === 'openai' ? S.model : '')}" placeholder="llama3.1"></label>
        <label class="field">API key (if needed)<input id="key-openai" type="password" autocomplete="off"></label>
      </div>
      <div class="row"><button class="pill primary" id="save-ai">Check and save</button><span class="msg" id="msg-ai">${S.ai_ok ? 'AI works.' : ''}</span></div>
    </section>

    <section class="step ${step(S.youtube, S.ai_ok)}">
      <h2><span class="num">2</span>Your YouTube account</h2>
      <p class="muted">PersonalTube reads your history, likes and subscriptions. It changes nothing in your account.</p>
      <div class="row">
        <select id="browser" aria-label="Browser">${S.browsers.map((b) => `<option${b === S.browser ? ' selected' : ''}>${b}</option>`).join('')}</select>
        <button class="pill primary" id="connect">Use my browser login</button>
      </div>
      <p class="muted small">Pick the browser where you are signed in to YouTube. On a Mac, a window may ask to allow access to the browser's safe storage: click Allow.</p>
      <div class="row"><label class="pill" for="takeout">Or upload a Google Takeout file</label>
        <input id="takeout" type="file" accept=".zip,.json,.html" hidden><span class="msg" id="msg-yt">${yt}</span></div>
    </section>

    <section class="step ${step(S.interests.length, S.youtube)}">
      <h2><span class="num">3</span>Your interests</h2>
      <p class="muted">The AI reads your history and writes down what you like. It takes about a minute.</p>
      <label class="field">Anything the AI should know? <span class="muted">Optional, used only for this build</span>
        <textarea id="wish" rows="2" placeholder="For example: I want videos in English. Or: mostly gaming videos, no music."></textarea></label>
      <div class="row"><button class="pill primary" id="build">${S.interests.length ? 'Build again' : 'Build my profile'}</button><span class="msg" id="msg-profile"></span></div>
      ${S.rules.length ? `<p class="rules">Rules: ${S.rules.map(esc).join(' · ')}</p>` : ''}
      <div class="chips">${S.interests.map((i) => `<span class="chip">${esc(i)}</span>`).join('')}</div>
    </section>

    ${S.ready ? '<button class="pill primary big" id="start">Open my feed</button>' : ''}
  </div>`);

  const showExtra = () => {
    const chosen = $('input[name=provider]:checked')?.value;
    for (const el of page.querySelectorAll('.extra')) el.hidden = el.dataset.for !== chosen;
  };
  for (const r of page.querySelectorAll('input[name=provider]')) r.onchange = showExtra;
  showExtra();

  // Runs one setup action: a spinner and a seconds counter on the button, then the new state or the error.
  const run = async (btn, msgId, busyText, call, hint = '') => {
    const label = btn.textContent;
    const t0 = Date.now();
    const tick = () => {
      btn.innerHTML = `<span class="spin"></span>${esc(busyText)}${dots()} <span class="secs">${Math.floor((Date.now() - t0) / 1000)}s</span>`;
    };
    btn.disabled = true;
    tick();
    const timer = setInterval(tick, 1000);
    $(msgId).className = 'msg muted';
    $(msgId).textContent = hint;
    try {
      const res = await call();
      if (!res.ok) throw new Error(res.error || 'Something went wrong.');
      SETUP = res;
      renderSetup();
    } catch (e) {
      btn.disabled = false;
      btn.textContent = label;
      $(msgId).className = 'msg err';
      $(msgId).textContent = e.message;
    } finally {
      clearInterval(timer);
    }
  };

  $('#save-ai').onclick = (e) => {
    const provider = $('input[name=provider]:checked')?.value;
    run(e.currentTarget, '#msg-ai', 'Checking the AI', () => callApi('/api/setup/ai', {
      provider,
      api_key: provider === 'openai' ? $('#key-openai').value : $('#key-openrouter').value,
      base_url: $('#base-url').value,
      model: $('#model').value,
    }));
  };
  $('#connect').onclick = (e) => run(e.currentTarget, '#msg-yt', 'Reading your history',
    () => callApi('/api/setup/youtube', { browser: $('#browser').value }), 'Usually up to a minute.');
  $('#takeout').onchange = (e) => {
    const file = e.target.files[0];
    if (file) run($('#connect'), '#msg-yt', 'Reading the file',
      () => fetch('/api/setup/takeout', { method: 'POST', headers: { 'X-Filename': file.name }, body: file }).then((r) => r.json()));
  };
  $('#build').onclick = (e) => run(e.currentTarget, '#msg-profile', 'Thinking',
    () => callApi('/api/setup/profile', { wish: $('#wish').value }), 'Usually about a minute.');
  $('#start')?.addEventListener('click', async () => {
    await callApi('/api/setup/start');
    location.hash = '#/';
    location.reload();
  });
}

// ---------- navigation ----------
function route() {
  $('.toast')?.remove();
  const m = location.hash.match(/^#\/watch\/([\w-]{11})$/);
  const setup = location.hash === '#/setup';
  const gear = $('#gear'); // a second click on the gear goes back to the feed
  gear.href = setup ? '#/' : '#/setup';
  gear.title = setup ? 'Back to your feed' : 'Settings';
  gear.setAttribute('aria-label', gear.title);
  gear.classList.toggle('on', setup);
  if (!feedOff() && (setup || m)) savedY = window.scrollY; // leaving the feed
  if (setup) {
    showSetup();
  } else if (m) {
    watch(m[1]);
  } else if ($('#grid')) { // back to the feed that is still in the page
    page.innerHTML = '';
    feedEl.classList.remove('off');
    window.scrollTo(0, savedY);
  } else {
    showFeed();
  }
}

// ---------- status: video count and what the update does right now ----------
let shownCount = 0;

function countTo(n) {
  const el = $('#count');
  const from = shownCount;
  shownCount = n;
  if (!from || n <= from) { el.textContent = n.toLocaleString('en'); return; }
  const t0 = performance.now();
  const step = (t) => {
    if (shownCount !== n) return; // a newer count took over
    const k = Math.min(1, (t - t0) / 900);
    el.textContent = Math.round(from + (n - from) * (1 - (1 - k) ** 3)).toLocaleString('en');
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
  const gain = $('#gain');
  gain.textContent = `+${(n - from).toLocaleString('en')}`;
  for (const x of [gain, el]) { x.classList.remove('show'); void x.offsetWidth; x.classList.add('show'); }
}

function showStatus() {
  if (!FEED) return;
  const live = !!ST.updating;
  const n = FEED.videos.length;
  const p = PROG;
  const pct = live && p.total ? Math.round((100 * p.done) / p.total) : 0;
  if (!n && !live) {
    $('#count').textContent = $('#note').textContent = '';
  } else {
    countTo(n);
    let phase = live ? ` · ${p.total ? `${pct}% · ` : ''}finding more${dots()}` : '';
    if (!live && FEED.generated) {
      const hours = (Date.now() - new Date(FEED.generated).getTime()) / 36e5;
      phase = ` · updated ${hours < 1 ? 'less than an hour ago' : hours < 48 ? `${Math.round(hours)} h ago` : `${Math.round(hours / 24)} days ago`}`;
    }
    $('#note').innerHTML = `videos<span class="phase">${phase}</span>`;
  }
  const fmt = (x) => (x || 0).toLocaleString('en');
  const line = !live || !p.total ? '' : [
    p.doing || 'The AI is checking the last videos',
    `searched ${p.done} of ${p.total}`, `${fmt(p.found)} found`, `${fmt(p.checked)} checked by AI`,
  ].join(' · ');
  for (const el of document.querySelectorAll('.live-line')) el.innerHTML = live && !p.total ? `Starting${dots()}` : esc(line);
  $('#status').title = line;
}

// While an update runs, check its progress every few seconds and add new videos as they come.
async function poll() {
  const p = await fetch('/api/progress').then((r) => r.json()).catch(() => null);
  if (p) {
    const was = ST.updating;
    ST.updating = p.updating;
    PROG = p;
    if (p.updating ? p.feed != null && p.feed !== FEED.videos.length : was) await refreshFeed();
    if (was !== p.updating && onFeed()) {
      if (!feedList.length) showFeed();
      else if ($('.end')) endBlock();
    }
    showStatus();
  }
  setTimeout(poll, ST.updating ? 3000 : 30000);
}

// ---------- loading ----------
async function load() {
  await flushSeen(); // the new mix should know what was just on screen
  const [feed, state] = await Promise.all([
    fetch('/feed.json').then((r) => r.json()),
    fetch('/api/state').then((r) => r.json()),
  ]);
  ST = state;
  setFeed(feed);
  feedList = rank();
  rendered = 0;
  savedY = 0;
  feedEl.innerHTML = ''; // a new mix: the cards are drawn again
}

// The logo and "Show a new mix" work like YouTube's logo: a new mix from the top of the feed.
async function refresh() {
  await load().catch(() => {});
  showStatus();
  if (!onFeed()) { location.hash = '#/'; return; }
  showFeed();
}

$('.logo').addEventListener('click', (e) => {
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) return; // opening in a new tab stays as is
  e.preventDefault();
  refresh();
});
window.addEventListener('hashchange', route);

load().then(() => {
  showStatus();
  poll();
  if (!ST.ready && location.hash !== '#/setup') location.hash = '#/setup'; // first visit
  else route();
}).catch(() => {
  showPage('<p class="empty">PersonalTube is not running. Start it with <code>personaltube</code> and open this page again.</p>');
});
