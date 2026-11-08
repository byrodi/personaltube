"""The recommender: interest profile, YouTube discovery, AI scoring and the feed file."""
import collections
import concurrent.futures
import datetime
import os
import random
import threading
import time

from . import ai, store, youtube

SEARCH_RESULTS = 25   # videos per search query
CHANNEL_RESULTS = 12  # latest videos per seed channel
MIN_SCORE = 6         # videos below this AI score never reach the feed
KEEP_DAYS = 45        # forget feed videos found longer ago than this
FIRST_BATCH = 20      # score the first videos early so the feed fills up quickly
LOCK = "update.lock"  # exists while an update runs; holds its progress for the site
STALE_LOCK = 20 * 60  # a lock not written for this long belongs to an update that died

state_lock = threading.Lock()
_running = threading.Lock()  # one update at a time in this process


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


# ---------- YouTube account ----------

def connect(browser=None, takeout=None):
    """Import history, likes and subscriptions. Returns the counts."""
    if takeout:
        history, likes, subs = youtube.import_takeout(takeout)
    else:
        history, likes, subs = youtube.import_from_browser(browser)
        cfg = store.config()
        cfg["youtube"]["browser"] = browser
        store.save_config(cfg)
    store.write("history.json", history)
    store.write("likes.json", likes)
    store.write("subscriptions.json", subs)
    return {"history": len(history), "likes": len(likes), "subs": len(subs)}


# ---------- interest profile ----------

PROFILE_PROMPT = """You build a YouTube interest profile for one viewer: what they like, so a
home feed can find new videos for them.

You get their YouTube watch history, Shorts, liked videos and subscriptions, and their activity in
this feed. Activity in the feed is the newest and most important signal: when it differs from the
older YouTube history, follow the feed activity.
{wish}
Return JSON:
{{"rules": ["hard requirements every video must meet, for example its language. Empty list if none"],
 "interests": [{{
   "id": "short_snake_case",
   "name": "Short label",
   "weight": 1-10 (share of their attention),
   "about": "One or two sentences: exactly what they enjoy here, which creators and formats",
   "queries": ["6 YouTube search queries that find popular, recent videos for this interest"],
   "channels": ["up to 8 YouTube channel handles like @mkbhd that fit best"]
 }}],
 "avoid": ["kinds of videos this viewer would not click"]}}

12 to 20 interests, ordered by weight. Queries must read like real YouTube searches (creator names,
games, shows, formats), not abstract topics. Queries and channels follow the rules, for example
English queries and English-speaking channels for an English feed. Only real channels."""

WISH = """
The viewer also wrote what they want from the feed. Follow it, it is more important than the
history: it can set the language, change the balance of topics or add things to avoid.
Their words: "{text}"
"""


def _profile_input():
    history = store.read("history.json", [])
    likes = store.read("likes.json", [])
    subs = store.read("subscriptions.json", [])
    state = load_state()
    rules = (store.read("profile.json") or {}).get("rules", [])
    rng = random.Random(7)
    videos = list({h["id"]: h for h in reversed(history) if not h.get("short")}.values())[::-1]
    shorts = [h["title"] for h in history if h.get("short") and h["title"]]
    lines = []
    for kind, title in (("watched", "Opened in the feed"), ("liked", "Liked in the feed"),
                        ("hidden", "Rejected in the feed (not interested)")):
        items = sorted(state[kind].values(), key=lambda x: x.get("t", 0))[-80:]
        if items:
            lines.append(f"## {title}, newest last")
            lines += [f"- {x['title']} ({x['channel']})" for x in items]
    if rules:
        lines += ["\n## Rules of the current feed (keep them unless the feed activity shows otherwise)"]
        lines += [f"- {r}" for r in rules]
    channels = collections.Counter(h["channel"] for h in history if h.get("channel") and not h.get("short"))
    if channels:
        lines += ["\n## YouTube: most watched channels (views)",
                  ", ".join(f"{c} ({n})" for c, n in channels.most_common(150))]
    lines.append("\n## YouTube: watched videos, newest first")
    lines += [f"- {h['title'][:90]}" + (f" ({h['channel']})" if h.get("channel") else "") for h in videos[:700]]
    if shorts:
        lines.append("\n## YouTube: Shorts they watched (sample)")
        lines += [f"- {t[:80]}" for t in rng.sample(shorts, min(150, len(shorts)))]
    if likes:
        lines.append("\n## YouTube: liked videos")
        lines += [f"- {h['title'][:90]} ({h['channel']})" for h in likes[:200]]
    if subs:
        lines += ["\n## YouTube: subscriptions", ", ".join(s["name"] for s in subs[:1000])]
    return "\n".join(lines)


def build_profile(wish=""):
    """Build the interest profile. `wish` is the viewer's own words, used for this build only."""
    if not store.read("history.json"):
        raise RuntimeError("No YouTube history yet. Connect your YouTube account first.")
    print(f"Building your interest profile with {ai.settings()['profile_model'] or ai.settings()['provider']}…", flush=True)
    wish = wish.strip()[:500]
    system = PROFILE_PROMPT.format(wish=WISH.format(text=wish.replace('"', "'")) if wish else "")
    profile = ai.ask(system, _profile_input(), big=True)
    profile.setdefault("rules", [])
    profile["discovered"] = (store.read("profile.json") or {}).get("discovered", [])
    store.write("profile.json", profile, pretty=True)
    print("Rules:", "; ".join(profile["rules"]) or "none")
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

Rules every video must meet:
{rules}

For every numbered video, return how likely this viewer clicks it and enjoys it, 0-10.
Give 0 if the video breaks a rule, is made for kids, is a low-effort reupload or
compilation spam, or matches no interest. Return JSON: {{"r": [[number, score, "interest_id"], ...]}}
with one entry per video."""




def _score_prompt(profile, state):
    def fmt(items):
        return "\n".join(f"- {x['title']} ({x['channel']})" for x in items) or "- nothing yet"

    return SCORE_PROMPT.format(
        interests="\n".join(f"- {i['id']} ({i['weight']}): {i['about']}" for i in profile["interests"]),
        avoid="; ".join(profile.get("avoid", [])),
        rules="\n".join(f"- {r}" for r in profile.get("rules", [])) or "- none",
        liked=fmt(list(state["liked"].values())[-25:]), hidden=fmt(list(state["hidden"].values())[-25:]))


def _score(system, chunk):
    """Score one batch of videos. Returns {video_id: [score, interest_id]}."""
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


# ---------- update ----------

def is_updating():
    """True while an update runs in this process, or in another one that still writes its progress."""
    if _running.locked():
        return True
    try:
        return time.time() - (store.HOME / LOCK).stat().st_mtime < STALE_LOCK
    except OSError:  # no lock, or the update just removed it
        return False


def progress():
    """What the running update is doing (requests done, videos found, checked, added), or None."""
    if not is_updating():
        return None
    try:
        data = store.read(LOCK)
    except (OSError, ValueError):  # removed or replaced while reading
        data = None
    return data if isinstance(data, dict) else {}


def update():
    """Search YouTube for new videos, score them with AI and write feed.json as they come."""
    if is_updating() or not _running.acquire(blocking=False):
        print("Another update is already running.")
        return
    try:
        _update()
    finally:
        (store.HOME / LOCK).unlink(missing_ok=True)
        _running.release()


def _update():
    profile = store.read("profile.json") or build_profile()
    state = load_state()

    hidden_channels = collections.Counter(x["handle"] for x in state["hidden"].values())
    seeds = {}
    for interest in profile["interests"]:
        for handle in interest.get("channels", [])[:5]:
            seeds.setdefault(handle, interest["id"])
    for handle in profile.get("discovered", []) + [x["handle"] for x in state["liked"].values()]:
        seeds.setdefault(handle, "")
    seeds = [h for h in seeds if h.startswith("@") and hidden_channels[h] < 2]
    jobs = [(youtube.search_url(q), SEARCH_RESULTS, None, f'Searching "{q}"')
            for i in profile["interests"] for q in i["queries"]]
    jobs += [(youtube.channel_url(h), CHANNEL_RESULTS, h, f"Checking {h}") for h in seeds]
    random.shuffle(jobs)

    skip = set(state["watched"]) | set(state["hidden"])
    scores = store.read("scores.json", {})
    now = time.time()
    feed = {v["id"]: v for v in (store.read("feed.json") or {}).get("videos", [])  # keep earlier finds a while
            if v["id"] not in skip and now - v["found"] < KEEP_DAYS * 86400}
    interests = [{"id": i["id"], "name": i["name"], "weight": i["weight"]} for i in profile["interests"]]
    report = {"total": len(jobs), "done": 0, "found": 0, "checked": 0, "added": 0, "feed": len(feed), "doing": ""}
    found, pending, broken = {}, [], set()
    lock = threading.Lock()  # found, scores, feed and report are shared with the scoring threads
    dirty = True

    def keep(v, score):
        nonlocal dirty
        if score[0] >= MIN_SCORE:
            report["added"] += v["id"] not in feed
            feed[v["id"]] = {**v, "score": score[0], "interest": score[1],
                             "found": feed.get(v["id"], {}).get("found", time.time())}
            dirty = True

    def save():
        nonlocal dirty
        if dirty:
            store.write("feed.json", {"generated": datetime.datetime.now().isoformat(timespec="minutes"),
                                      "interests": interests, "videos": list(feed.values())})
            dirty = False
        report["feed"] = len(feed)
        store.write(LOCK, report)  # the site reads progress from here

    def scored(chunk):
        result = _score(system, chunk)
        with lock:
            scores.update(result)
            store.write("scores.json", scores)
            report["checked"] += len(chunk)
            for v in chunk:
                if v["id"] in result:
                    keep(v, result[v["id"]])
            save()
            print(f"  AI checked {report['checked']} of {report['found']}, feed: {len(feed)} videos", flush=True)

    system = _score_prompt(profile, state)
    workers, size = ai.parallel()
    print(f"Searching YouTube: {len(jobs)} requests, about {len(jobs) * 7 // 60} minutes", flush=True)
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        tasks = []
        for n, (url, limit, handle, doing) in enumerate(jobs, 1):
            with lock:
                report["doing"] = doing
                save()
            result = youtube.videos(url, limit)
            if result is None and handle:
                broken.add(handle)  # wrong handle or no videos tab: stop asking for it
            with lock:
                for v in result or []:
                    if v["id"] in found or v["id"] in skip:
                        continue
                    found[v["id"]] = v
                    if v["id"] in scores:  # scored in an earlier update, no AI call needed
                        keep(v, scores[v["id"]])
                        report["checked"] += 1
                    else:
                        pending.append(v)
                report.update(done=n, found=len(found), doing="" if n == len(jobs) else doing)
                save()
            # The AI scores in the background while the search goes on; the first batch is small
            # so the first videos show up within a minute.
            if pending and (len(pending) >= (size if tasks else FIRST_BATCH) or n == len(jobs)):
                tasks.append(pool.submit(scored, pending))
                pending = []
            if n < len(jobs):
                time.sleep(random.uniform(1.5, 4))  # pace requests like a person browsing
    for task in tasks:
        task.result()  # raise what failed in a scoring thread
    dirty = True
    save()  # a fresh "generated" time even when nothing new turned up

    # Channels whose videos the AI rates high become seed channels next time.
    per_channel = collections.defaultdict(list)
    for v in found.values():
        if v["id"] in scores and v["handle"].startswith("@"):
            per_channel[v["handle"]].append(scores[v["id"]][0])
    good = sorted((h for h, s in per_channel.items() if len(s) >= 2 and sum(s) / len(s) >= 8),
                  key=lambda h: -sum(per_channel[h]) / len(per_channel[h]))
    profile["discovered"] = [h for h in dict.fromkeys(good + profile.get("discovered", [])) if h not in broken][:60]
    for interest in profile["interests"]:
        interest["channels"] = [h for h in interest.get("channels", []) if h not in broken]
    store.write("profile.json", profile, pretty=True)
    print(f"Update done: {report['added']} new videos, feed: {len(feed)}.", flush=True)
