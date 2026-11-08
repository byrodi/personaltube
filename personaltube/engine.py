"""The recommender: interest profile, YouTube discovery, AI scoring and the feed file."""
import collections
import concurrent.futures
import datetime
import os
import random
import re
import threading
import time

from . import ai, store, youtube

SEARCH_RESULTS = 25   # videos per search query
CHANNEL_RESULTS = 12  # latest videos per seed channel
MIN_SCORE = 6         # videos below this AI score never reach the feed
KEEP_DAYS = 45        # forget feed videos found longer ago than this

# For an English feed: titles in non-Latin scripts or with Spanish/Portuguese markers are skipped
# before they cost an AI call. The AI also rejects other languages.
NOT_ENGLISH = re.compile(
    r"[Ѐ-ӿ؀-ۿऀ-ॿ฀-๿぀-ヿ一-鿿가-힯]"
    r"|\b(letra|significado|oficial|subtitulado|legendado|en español|ao vivo)\b", re.I)

state_lock = threading.Lock()
status = {"updating": False}


# ---------- what the site reports back ----------

def load_state():
    state = store.read("state.json", {})
    for key in ("watched", "liked", "hidden", "seen"):
        state.setdefault(key, {})
    return state


def apply_event(event):
    kind, vid = event.get("type"), event.get("id")
    meta = event.get("video") or {}
    info = {k: str(meta.get(k, ""))[:300] for k in ("title", "channel", "handle", "interest")}
    with state_lock:
        state = load_state()
        if kind == "seen":
            for i in event.get("ids", [])[:200]:
                state["seen"][i] = state["seen"].get(i, 0) + 1
            if len(state["seen"]) > 30000:
                state["seen"] = dict(list(state["seen"].items())[-20000:])
        elif kind == "watch":
            state["watched"][vid] = {**info, "t": time.time()}
        elif kind in ("like", "hide"):
            state["liked" if kind == "like" else "hidden"][vid] = {**info, "t": time.time()}
        elif kind in ("unlike", "unhide"):
            state["liked" if kind == "unlike" else "hidden"].pop(vid, None)
        else:
            return False
        store.write("state.json", state)
    return True


# ---------- interest profile ----------

PROFILE_PROMPT = """You build a YouTube interest profile for one viewer.
Their history can be in any language. They want a home feed of {language}-language videos
that match the same tastes: same topics, same kind of creators, same energy (for example,
{language} streamer clips if they watch streamer clips in another language).

You get their watch history, Shorts they watched, liked videos, subscriptions and feedback
from the feed (liked and rejected videos).

Return JSON:
{{"interests": [{{
   "id": "short_snake_case",
   "name": "Short label",
   "weight": 1-10 (share of their attention),
   "about": "One or two sentences: exactly what they enjoy here, which creators and formats",
   "queries": ["6 YouTube search queries in {language} that find popular, recent videos for this interest"],
   "channels": ["up to 8 {language}-language YouTube channel handles like @mkbhd that fit best"]
 }}],
 "avoid": ["kinds of videos this viewer would not click"]}}

Rules: 12 to 20 interests, ordered by weight. Queries must read like real YouTube searches
(creator names, games, shows, formats), not abstract topics. Only real channels."""


def _profile_input():
    history = store.read("history.json", [])
    likes = store.read("likes.json", [])
    subs = store.read("subscriptions.json", [])
    state = load_state()
    rng = random.Random(7)
    videos = list({h["id"]: h for h in reversed(history) if not h.get("short")}.values())[::-1]
    shorts = [h["title"] for h in history if h.get("short") and h["title"]]
    lines = []
    channels = collections.Counter(h["channel"] for h in history if h.get("channel") and not h.get("short"))
    if channels:
        lines += ["## Most watched channels (views)",
                  ", ".join(f"{c} ({n})" for c, n in channels.most_common(150))]
    lines.append("\n## Watched videos, newest first")
    lines += [f"- {h['title'][:90]}" + (f" ({h['channel']})" if h.get("channel") else "") for h in videos[:700]]
    if shorts:
        lines.append("\n## Shorts they watched (sample)")
        lines += [f"- {t[:80]}" for t in rng.sample(shorts, min(150, len(shorts)))]
    if likes:
        lines.append("\n## Liked videos")
        lines += [f"- {h['title'][:90]} ({h['channel']})" for h in likes[:200]]
    if subs:
        lines += ["\n## Subscriptions", ", ".join(s["name"] for s in subs[:1000])]
    for kind in ("liked", "hidden"):
        items = list(state[kind].values())[-40:]
        if items:
            lines.append(f"\n## Feed feedback: {kind}")
            lines += [f"- {x['title']} ({x['channel']})" for x in items]
    return "\n".join(lines)


def build_profile():
    if not store.read("history.json"):
        raise RuntimeError("No YouTube history yet. Run `personaltube connect` first.")
    language = store.config()["language"]
    print(f"Building your interest profile with {ai.settings()['profile_model'] or ai.settings()['provider']}…", flush=True)
    profile = ai.ask(PROFILE_PROMPT.format(language=language), _profile_input(), big=True)
    profile["discovered"] = (store.read("profile.json") or {}).get("discovered", [])
    store.write("profile.json", profile, pretty=True)
    print("Interests:", ", ".join(f"{i['name']} ({i['weight']})" for i in profile["interests"]))
    return profile


# ---------- AI scoring ----------

SCORE_PROMPT = """You pick videos for one viewer's YouTube home feed.

Viewer interests (id, weight 1-10, what they like):
{interests}

They avoid: {avoid}

Recently liked in the feed:
{liked}

Recently rejected in the feed ("not interested"):
{hidden}

For every numbered video, return how likely this viewer clicks it and enjoys it, 0-10.
Give 0 if the video is not in {language}, is made for kids, is a low-effort reupload or
compilation spam, or matches no interest. Return JSON: {{"r": [[number, score, "interest_id"], ...]}}
with one entry per video."""


def score_videos(videos, profile, state):
    """Return {video_id: [score, interest_id]}."""
    def fmt(items):
        return "\n".join(f"- {x['title']} ({x['channel']})" for x in items) or "- nothing yet"

    system = SCORE_PROMPT.format(
        interests="\n".join(f"- {i['id']} ({i['weight']}): {i['about']}" for i in profile["interests"]),
        avoid="; ".join(profile.get("avoid", [])), language=store.config()["language"],
        liked=fmt(list(state["liked"].values())[-25:]), hidden=fmt(list(state["hidden"].values())[-25:]))

    def batch(chunk):
        lines = [f"{n}. {v['title']} | {v['channel']} | {v['dur'] // 60} min | {v['views']} views"
                 for n, v in enumerate(chunk)]
        try:
            rows = ai.ask(system, "\n".join(lines)).get("r", [])
        except RuntimeError as e:
            print(f"  skipped a batch: {e}", flush=True)
            return {}
        result = {}
        for row in rows:
            try:
                result[chunk[int(row[0])]["id"]] = [float(row[1]), str(row[2])]
            except (ValueError, IndexError, TypeError):
                continue
        return result

    workers, size = ai.parallel()
    chunks = [videos[i:i + size] for i in range(0, len(videos), size)]
    scores = {}
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        for n, part in enumerate(pool.map(batch, chunks), 1):
            scores.update(part)
            print(f"  scored {n}/{len(chunks)} batches", flush=True)
    return scores


# ---------- update ----------

def is_updating():
    """True while an update runs in this process or in another `personaltube update`."""
    lock = store.HOME / "update.lock"
    return status["updating"] or (lock.exists() and time.time() - lock.stat().st_mtime < 3 * 3600)


def update():
    """Search YouTube for new videos, score them with AI and write feed.json."""
    lock = store.HOME / "update.lock"
    if is_updating():
        print("Another update is already running.")
        return
    store.HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()))
    status["updating"] = True
    try:
        _update()
    finally:
        status["updating"] = False
        lock.unlink(missing_ok=True)


def _update():
    profile = store.read("profile.json") or build_profile()
    state = load_state()
    english = store.config()["language"].lower() == "english"

    hidden_channels = collections.Counter(x["handle"] for x in state["hidden"].values())
    seeds = {}
    for interest in profile["interests"]:
        for handle in interest.get("channels", [])[:5]:
            seeds.setdefault(handle, interest["id"])
    for handle in profile.get("discovered", []) + [x["handle"] for x in state["liked"].values()]:
        seeds.setdefault(handle, "")
    seeds = [h for h in seeds if h.startswith("@") and hidden_channels[h] < 2]
    jobs = [(youtube.search_url(q), SEARCH_RESULTS, None) for i in profile["interests"] for q in i["queries"]]
    jobs += [(youtube.channel_url(h), CHANNEL_RESULTS, h) for h in seeds]
    random.shuffle(jobs)

    found = {}
    broken = set()
    print(f"Searching YouTube: {len(jobs)} requests, about {len(jobs) * 6 // 60} minutes", flush=True)
    for n, (url, limit, handle) in enumerate(jobs, 1):
        result = youtube.videos(url, limit)
        if result is None and handle:
            broken.add(handle)  # wrong handle or no videos tab: stop asking for it
        for v in result or []:
            found.setdefault(v["id"], v)
        if n % 20 == 0:
            print(f"  {n}/{len(jobs)} requests, {len(found)} videos", flush=True)
        time.sleep(random.uniform(1.5, 4))  # pace requests like a person browsing

    skip = set(state["watched"]) | set(state["hidden"])
    candidates = [v for v in found.values()
                  if v["id"] not in skip and not (english and NOT_ENGLISH.search(v["title"]))]
    scores = store.read("scores.json", {})
    new = [v for v in candidates if v["id"] not in scores]
    print(f"{len(candidates)} candidates, {len(new)} new to score with {ai.settings()['model'] or ai.settings()['provider']}", flush=True)
    if new:
        scores.update(score_videos(new, profile, state))
        store.write("scores.json", scores)

    # Channels whose videos the AI rates high become seed channels next time.
    per_channel = collections.defaultdict(list)
    for v in candidates:
        if v["id"] in scores and v["handle"].startswith("@"):
            per_channel[v["handle"]].append(scores[v["id"]][0])
    good = sorted((h for h, s in per_channel.items() if len(s) >= 2 and sum(s) / len(s) >= 8),
                  key=lambda h: -sum(per_channel[h]) / len(per_channel[h]))
    profile["discovered"] = [h for h in dict.fromkeys(good + profile.get("discovered", [])) if h not in broken][:60]
    for interest in profile["interests"]:
        interest["channels"] = [h for h in interest.get("channels", []) if h not in broken]
    store.write("profile.json", profile, pretty=True)

    now = time.time()
    old = {v["id"]: v for v in (store.read("feed.json") or {}).get("videos", [])}
    feed = {}
    for v in candidates:
        if v["id"] in scores and scores[v["id"]][0] >= MIN_SCORE:
            feed[v["id"]] = {**v, "score": scores[v["id"]][0], "interest": scores[v["id"]][1],
                             "found": old.get(v["id"], {}).get("found", now)}
    for vid, v in old.items():  # keep good videos from earlier runs for a while
        if vid not in feed and vid not in skip and now - v["found"] < KEEP_DAYS * 86400:
            feed[vid] = v
    store.write("feed.json", {
        "generated": datetime.datetime.now().isoformat(timespec="minutes"),
        "interests": [{"id": i["id"], "name": i["name"], "weight": i["weight"]} for i in profile["interests"]],
        "videos": list(feed.values()),
    })
    print(f"Feed ready: {len(feed)} videos.", flush=True)
