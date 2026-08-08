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
    python ops.py smtp show
    python ops.py smtp set smtp.gmail.com 587 you@gmail.com app-password
    python ops.py email status
    python ops.py email enable
    python ops.py email enable --weekly
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
    print(f"ADMIN_ALERT_EMAIL:      {data.get('admin_alert_email') or '(not set on server)'}")
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


def cmd_smtp_show(args):
    host      = database.get_setting(None, "smtp_host", "")
    port      = database.get_setting(None, "smtp_port", "587")
    user      = database.get_setting(None, "smtp_user", "")
    password  = database.get_setting(None, "smtp_password", "")
    from_name = database.get_setting(None, "smtp_from_name", "Daily AI Digest")
    enabled   = database.get_setting(None, "email_enabled", "false").lower() == "true"
    admin_alert_email = os.environ.get("ADMIN_ALERT_EMAIL", "")

    if not host or not user:
        print("SMTP is not configured - no digest emails, subscribe confirmations, or ops alerts can be sent.")
        print("Set it with: python ops.py smtp set <host> <port> <user> <password> [--from-name NAME]")
        return

    print(f"SMTP host:              {host}:{port}")
    print(f"SMTP user:               {user}")
    print(f"SMTP password:           {'(set)' if password else '(not set)'}")
    print(f"From name:               {from_name}")
    print(f"Daily digest email:      {'enabled' if enabled else 'disabled'}")
    print(f"ADMIN_ALERT_EMAIL:       {admin_alert_email or '(not set in environment)'}")
    if not admin_alert_email:
        print("\nSMTP is configured, but ADMIN_ALERT_EMAIL isn't set, so sync-failure alerts won't be sent.")


def cmd_smtp_set(args):
    database.save_setting(None, "smtp_host", args.host.strip())
    database.save_setting(None, "smtp_port", str(args.port))
    database.save_setting(None, "smtp_user", args.user.strip())
    database.save_setting(None, "smtp_password", args.password.strip())
    database.save_setting(None, "smtp_from_name", args.from_name)
    print(f"SMTP settings saved ({args.host}:{args.port}, user={args.user}).")


def cmd_email_status(args):
    daily_enabled  = database.get_setting(None, "email_enabled", "false").lower() == "true"
    weekly_enabled = database.get_setting(None, "weekly_email_enabled", "false").lower() == "true"
    last_daily  = database.get_setting(None, "last_email_sent_date", "")
    last_weekly = database.get_setting(None, "last_weekly_email_sent", "")
    print(f"Daily digest email:      {'enabled' if daily_enabled else 'disabled'}  (last sent: {last_daily or 'never'})")
    print(f"Weekly roundup email:    {'enabled' if weekly_enabled else 'disabled'}  (last sent: {last_weekly or 'never'})")
    if (daily_enabled or weekly_enabled):
        host = database.get_setting(None, "smtp_host", "")
        user = database.get_setting(None, "smtp_user", "")
        if not host or not user:
            print("\nWarning: an email is enabled but SMTP isn't configured (see: python ops.py smtp show).")


def cmd_email_enable(args):
    key = "weekly_email_enabled" if args.weekly else "email_enabled"
    label = "Weekly roundup" if args.weekly else "Daily digest"
    database.save_setting(None, key, "true")
    print(f"{label} email enabled.")
    host = database.get_setting(None, "smtp_host", "")
    user = database.get_setting(None, "smtp_user", "")
    if not host or not user:
        print("Warning: SMTP isn't configured yet, so nothing will actually send (see: python ops.py smtp set).")


def cmd_email_disable(args):
    key = "weekly_email_enabled" if args.weekly else "email_enabled"
    label = "Weekly roundup" if args.weekly else "Daily digest"
    database.save_setting(None, key, "false")
    print(f"{label} email disabled.")


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

    smtp = sub.add_parser("smtp", help="View or set SMTP settings (digest emails, ops alerts).").add_subparsers(dest="smtp_command", required=True)
    smtp.add_parser("show", help="Show current SMTP configuration (password masked).").set_defaults(func=cmd_smtp_show)
    p = smtp.add_parser("set", help="Set SMTP settings.")
    p.add_argument("host")
    p.add_argument("port", type=int)
    p.add_argument("user")
    p.add_argument("password")
    p.add_argument("--from-name", default="Daily AI Digest", dest="from_name")
    p.set_defaults(func=cmd_smtp_set)

    email = sub.add_parser("email", help="View or toggle daily/weekly subscriber emails.").add_subparsers(dest="email_command", required=True)
    email.add_parser("status", help="Show whether daily/weekly emails are enabled.").set_defaults(func=cmd_email_status)
    p = email.add_parser("enable", help="Enable the daily digest email (or --weekly for the weekly roundup).")
    p.add_argument("--weekly", action="store_true", help="Target the weekly roundup instead of the daily digest.")
    p.set_defaults(func=cmd_email_enable)
    p = email.add_parser("disable", help="Disable the daily digest email (or --weekly for the weekly roundup).")
    p.add_argument("--weekly", action="store_true", help="Target the weekly roundup instead of the daily digest.")
    p.set_defaults(func=cmd_email_disable)

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
