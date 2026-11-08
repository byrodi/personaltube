"""Command line. `personaltube` alone opens the site; setup happens on the page."""
import argparse
import sys

from . import __version__, engine, server, youtube


def main():
    parser = argparse.ArgumentParser(
        prog="personaltube", description="Your own YouTube home feed, picked by AI. "
        "Run without a command to open it in the browser.")
    parser.add_argument("--version", action="version", version=f"personaltube {__version__}")
    sub = parser.add_subparsers(dest="command")
    s = sub.add_parser("serve", help="open your feed in the browser (the default)")
    s.add_argument("--no-open", action="store_true", help="do not open a browser tab")
    s.add_argument("--port", type=int, help="port of the site (default: 8777)")
    sub.add_parser("update", help="find new videos now")
    c = sub.add_parser("connect", help="import your YouTube history again")
    c.add_argument("--browser", choices=youtube.BROWSERS, default="chrome",
                   help="read the YouTube login from this browser (default: chrome)")
    c.add_argument("--takeout", metavar="PATH", help="read a Google Takeout export instead")
    pr = sub.add_parser("profile", help="rebuild your interest profile with AI")
    pr.add_argument("--wish", default="", help='what the AI should know, for this build only, e.g. "videos in English"')
    args = parser.parse_args()
    try:
        if args.command in (None, "serve"):
            server.serve(open_browser=not getattr(args, "no_open", False), port=getattr(args, "port", None))
        elif args.command == "update":
            engine.update()
        elif args.command == "connect":
            counts = engine.connect(args.browser, args.takeout)
            print(f"Imported {counts['history']} watched videos, {counts['likes']} likes, "
                  f"{counts['subs']} subscriptions.")
        elif args.command == "profile":
            engine.build_profile(args.wish)
    except (RuntimeError, KeyboardInterrupt) as e:
        sys.exit(str(e) or "Stopped.")
