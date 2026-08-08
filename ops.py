"""
Ops CLI - the replacement for the admin dashboard's control surface, now that
it's gone. Run this locally (it reads the same .env as app.py) to:

  * trigger a manual sync / check pipeline status on a running deployment
  * add, remove, or toggle sources
  * view or set the Gemini API key
  * list, add, or remove newsletter subscribers

`sync` and `status` talk to the live server over HTTP (they need the
running process - sync state lives in memory, and the sync job itself must
run inside that process). Everything else talks to Postgres directly, since
it's just reading/writing rows and doesn't need the server at all.

Usage examples:
    python ops.py status
    python ops.py sync
    python ops.py sources list
    python ops.py sources add "Some Blog" https://example.com/feed.xml
    python ops.py sources toggle 5
    python ops.py sources remove 5
    python ops.py apikey show
    python ops.py apikey set AIzaSy...
    python ops.py subscribers list
    python ops.py subscribers add jane@example.com "Jane Doe"
    python ops.py subscribers remove jane@example.com
"""

import argparse
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests

import database

APP_URL     = os.environ.get("APP_URL", "http://localhost:8000").rstrip("/")
CRON_SECRET = os.environ.get("CRON_SECRET", "")


def _ops_headers():
    if not CRON_SECRET:
        print(
            "CRON_SECRET is not set in the environment - 'status' and 'sync' need it "
            "to authenticate to the live server (same secret /api/cron/hourly uses).",
            file=sys.stderr,
        )
        sys.exit(1)
    return {"X-Ops-Secret": CRON_SECRET}


def cmd_status(args):
    resp = requests.get(f"{APP_URL}/api/ops/status", headers=_ops_headers(), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    sync = data["sync"]
    print(f"Pipeline status:        {sync['status']}")
    print(f"Current step:           {sync['current_step']}")
    if sync.get("error_message"):
        print(f"Last error:              {sync['error_message']}")
    print(f"Latest digest date:     {data['latest_digest_date']}")
    print(f"Total digests:          {data['digest_count']}")
    print(f"Active subscribers:     {data['subscriber_count']}")
    print(f"Configured sources:     {data['source_count']}")
    if sync.get("logs"):
        print("\nRecent log lines:")
        for line in sync["logs"][-10:]:
            print(f"  {line}")


def cmd_sync(args):
    params = {"date": args.date} if args.date else {}
    resp = requests.post(f"{APP_URL}/api/ops/sync", headers=_ops_headers(), params=params, timeout=15)
    if resp.status_code == 400:
        print(f"Not started: {resp.json().get('detail')}")
        return
    resp.raise_for_status()
    data = resp.json()
    print(f"Sync triggered for {data['date']}. Check progress with: python ops.py status")


def cmd_sources_list(args):
    sources = database.get_sources(None)
    if not sources:
        print("No sources configured.")
        return
    for s in sources:
        state = "enabled " if s["enabled"] else "disabled"
        print(f"[{s['id']:>4}] {state}  {s['source_type']:<7}  {s['name']}  -  {s['url']}")


def cmd_sources_add(args):
    added = database.add_source(None, args.name, args.url, args.type, 0 if args.disabled else 1)
    if added:
        print(f"Added source '{args.name}'.")
    else:
        print(f"A source with URL {args.url} already exists.", file=sys.stderr)
        sys.exit(1)


def cmd_sources_remove(args):
    removed = database.delete_source(None, args.id)
    print(f"Removed source {args.id}." if removed else f"No source with id {args.id}.")
    if not removed:
        sys.exit(1)


def cmd_sources_toggle(args):
    success, new_state = database.toggle_source(None, args.id)
    if not success:
        print(f"No source with id {args.id}.", file=sys.stderr)
        sys.exit(1)
    print(f"Source {args.id} is now {'enabled' if new_state else 'disabled'}.")


def cmd_apikey_show(args):
    key = database.get_setting(None, "gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("No Gemini API key configured - the pipeline is running in offline fallback mode.")
    else:
        masked = f"...{key[-4:]}" if len(key) > 4 else "(configured)"
        print(f"Gemini API key is set (ends in {masked}).")


def cmd_apikey_set(args):
    key = args.key.strip()
    if not key:
        print("API key cannot be empty.", file=sys.stderr)
        sys.exit(1)
    database.save_setting(None, "gemini_api_key", key)
    print("Gemini API key saved.")


def cmd_subscribers_list(args):
    subs = database.get_all_subscribers(None)
    if not subs:
        print("No subscribers.")
        return
    for s in subs:
        state = "active  " if s["active"] else "inactive"
        name = f" ({s['name']})" if s["name"] else ""
        print(f"[{s['id']:>4}] {state}  {s['email']}{name}")


def cmd_subscribers_add(args):
    added = database.add_subscriber(None, args.email, args.name or "")
    if added:
        print(f"Added subscriber {args.email}.")
    else:
        print(f"{args.email} is already subscribed.", file=sys.stderr)
        sys.exit(1)


def cmd_subscribers_remove(args):
    removed = database.remove_subscriber(None, args.email)
    print(f"Removed {args.email}." if removed else f"{args.email} was not found.")
    if not removed:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Daily AI Digest - ops CLI (dashboard replacement).")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show pipeline status on the live server.").set_defaults(func=cmd_status)

    p = sub.add_parser("sync", help="Trigger a manual sync run on the live server.")
    p.add_argument("--date", help="Target date (YYYY-MM-DD). Defaults to today.")
    p.set_defaults(func=cmd_sync)

    sources = sub.add_parser("sources", help="Manage news/blog sources.").add_subparsers(dest="sources_command", required=True)
    sources.add_parser("list", help="List all sources.").set_defaults(func=cmd_sources_list)
    p = sources.add_parser("add", help="Add a new source.")
    p.add_argument("name")
    p.add_argument("url")
    p.add_argument("--type", choices=["rss", "scrape"], default="rss")
    p.add_argument("--disabled", action="store_true", help="Add it disabled.")
    p.set_defaults(func=cmd_sources_add)
    p = sources.add_parser("remove", help="Remove a source by id.")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_sources_remove)
    p = sources.add_parser("toggle", help="Enable/disable a source by id.")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_sources_toggle)

    apikey = sub.add_parser("apikey", help="View or set the Gemini API key.").add_subparsers(dest="apikey_command", required=True)
    apikey.add_parser("show", help="Show whether a key is configured.").set_defaults(func=cmd_apikey_show)
    p = apikey.add_parser("set", help="Set the Gemini API key.")
    p.add_argument("key")
    p.set_defaults(func=cmd_apikey_set)

    subscribers = sub.add_parser("subscribers", help="Manage newsletter subscribers.").add_subparsers(dest="subscribers_command", required=True)
    subscribers.add_parser("list", help="List all subscribers.").set_defaults(func=cmd_subscribers_list)
    p = subscribers.add_parser("add", help="Add a subscriber.")
    p.add_argument("email")
    p.add_argument("name", nargs="?", default="")
    p.set_defaults(func=cmd_subscribers_add)
    p = subscribers.add_parser("remove", help="Remove a subscriber by email.")
    p.add_argument("email")
    p.set_defaults(func=cmd_subscribers_remove)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
