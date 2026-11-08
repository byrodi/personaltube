"""Command line: personaltube setup | connect | profile | update | serve."""
import argparse
import getpass
import sys

from . import __version__, ai, engine, server, store, youtube


def _choose(question, options, default=0):
    """Numbered menu; returns the index of the chosen option."""
    print(question)
    for n, label in enumerate(options, 1):
        print(f"  {n}) {label}")
    while True:
        answer = input(f"Choose 1-{len(options)} [{default + 1}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1


def _ask(question, default=""):
    answer = input(f"{question} [{default}]: " if default else f"{question}: ").strip()
    return answer or default


def setup():
    print("PersonalTube setup. Your data stays in", store.HOME, "\n")
    cfg = store.config()
    cfg["language"] = _ask("Language of the videos in your feed", cfg["language"])

    names = list(ai.PROVIDERS)
    provider = names[_choose("\nWhich AI should pick your videos?", [ai.PROVIDERS[n][0] for n in names])]
    cfg["ai"] = {**store.DEFAULT_CONFIG["ai"], "provider": provider}
    if provider == "openrouter":
        cfg["ai"]["api_key"] = getpass.getpass("OpenRouter API key (input is hidden, Enter = use $OPENROUTER_API_KEY): ").strip()
    elif provider == "openai":
        cfg["ai"]["base_url"] = _ask("API base URL", "http://localhost:11434/v1")
        cfg["ai"]["model"] = _ask("Model name")
        cfg["ai"]["api_key"] = getpass.getpass("API key (hidden, Enter = none or $OPENAI_API_KEY): ").strip()
    elif provider == "claude":
        print("Uses your Claude Code login (`claude`). Run `claude` once and sign in if you have not yet.")
    elif provider == "codex":
        print("Uses your Codex CLI login (`codex login`).")
    store.save_config(cfg)
    print("Checking the AI…", flush=True)
    error = ai.check(provider)
    if error:
        sys.exit(f"The AI does not answer: {error}\nFix it and run `personaltube setup` again.")
    print("AI works.\n")

    way = _choose("How should PersonalTube read your YouTube account?", [
        "Use the YouTube login saved in my browser (fast, read-only)",
        "Use a Google Takeout export (takeout.google.com, YouTube history in JSON or HTML)",
    ])
    if way == 0:
        default = youtube.BROWSERS.index(cfg["youtube"]["browser"]) if cfg["youtube"]["browser"] in youtube.BROWSERS else 0
        cfg["youtube"]["browser"] = youtube.BROWSERS[_choose("Which browser are you signed in to YouTube with?", youtube.BROWSERS, default)]
        store.save_config(cfg)
        connect(browser=cfg["youtube"]["browser"])
    else:
        connect(takeout=_ask("Path to the Takeout .zip or folder"))

    engine.build_profile()
    print("\nNext: `personaltube serve` opens your feed. The first search for videos starts by itself")
    print("and takes about 15-20 minutes. Reload the page when it is done.")


def connect(browser=None, takeout=None):
    if takeout:
        print("Reading the Takeout export…", flush=True)
        history, likes, subs = youtube.import_takeout(takeout)
    else:
        browser = browser or store.config()["youtube"]["browser"]
        print(f"Reading your YouTube history, likes and subscriptions with the {browser} login…", flush=True)
        print("(macOS may ask to allow access to the browser's saved passwords storage: click Allow.)", flush=True)
        history, likes, subs = youtube.import_from_browser(browser)
    store.write("history.json", history)
    store.write("likes.json", likes)
    store.write("subscriptions.json", subs)
    print(f"Imported {len(history)} watched videos, {len(likes)} likes, {len(subs)} subscriptions.")


def main():
    parser = argparse.ArgumentParser(prog="personaltube", description="Your own YouTube home feed, picked by AI.")
    parser.add_argument("--version", action="version", version=f"personaltube {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="first-time setup: AI, YouTube account, interest profile")
    c = sub.add_parser("connect", help="import your YouTube history again")
    c.add_argument("--browser", choices=youtube.BROWSERS, help="read the YouTube login from this browser")
    c.add_argument("--takeout", metavar="PATH", help="read a Google Takeout export instead")
    sub.add_parser("profile", help="rebuild your interest profile with AI")
    sub.add_parser("update", help="find new videos now")
    s = sub.add_parser("serve", help="open your feed in the browser")
    s.add_argument("--no-open", action="store_true", help="do not open a browser tab")
    args = parser.parse_args()
    try:
        if args.command == "setup":
            setup()
        elif args.command == "connect":
            connect(args.browser, args.takeout)
        elif args.command == "profile":
            engine.build_profile()
        elif args.command == "update":
            engine.update()
        elif args.command == "serve":
            server.serve(open_browser=not args.no_open)
    except (RuntimeError, KeyboardInterrupt) as e:
        sys.exit(str(e) or "Stopped.")
