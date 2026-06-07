"""
Weekly 'Week in AI' email builder.
Imported by emailer.py and app.py.
"""

import os
import smtplib
import ssl
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from datetime import datetime

_APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
logger   = logging.getLogger("emailer")


def build_weekly_html_email(week_digests, week_label):
    """
    Builds a Week-in-AI HTML email.
    week_digests: list of {"date": "YYYY-MM-DD", "content": {...}} (newest first)
    week_label:   e.g. "May 19 – May 25, 2026"
    """
    if not week_digests:
        return "<p>No digest data available for this week.</p>"

    all_headlines, all_tools, all_research = [], [], []

    for day in week_digests:
        c = day.get("content", {})
        try:
            fmt = datetime.strptime(day["date"], "%Y-%m-%d").strftime("%a %b %d")
        except Exception:
            fmt = day["date"]

        for item in (c.get("biggest_news") or [])[:2]:
            if item.get("headline"):
                all_headlines.append({"date": fmt, "item": item})
        for item in (c.get("discovered_tools") or [])[:2]:
            if item.get("tool"):
                all_tools.append(item)
        for item in (c.get("open_source_research") or [])[:2]:
            if item.get("title"):
                all_research.append(item)

    # ── headline rows ──
    h_html = ""
    for h in all_headlines[:8]:
        it       = h["item"]
        headline = it.get("headline", "").replace("<", "&lt;").replace(">", "&gt;")
        summary  = (it.get("summary") or "")[:160].replace("<", "&lt;").replace(">", "&gt;")
        link     = it.get("link", "#")
        h_html += (
            f'<tr><td style="padding:14px 0;border-bottom:1px solid rgba(255,255,255,0.05);">'
            f'<p style="color:#64748b;font-size:10px;text-transform:uppercase;'
            f'letter-spacing:1px;margin:0 0 5px 0;font-family:Arial,sans-serif;">{h["date"]}</p>'
            f'<a href="{link}" style="color:#f1f5f9;font-size:15px;font-weight:700;'
            f'text-decoration:none;font-family:Arial,sans-serif;">{headline}</a>'
            f'<p style="color:#94a3b8;font-size:13px;margin:6px 0 0 0;'
            f'line-height:1.5;font-family:Arial,sans-serif;">{summary}</p>'
            f'</td></tr>'
        )
    if not h_html:
        h_html = '<tr><td style="color:#64748b;padding:14px 0;font-family:Arial,sans-serif;">No stories this week.</td></tr>'

    # ── tool rows ──
    t_html, seen_tools = "", set()
    for t in all_tools[:6]:
        name = t.get("tool", "")
        if name in seen_tools:
            continue
        seen_tools.add(name)
        t_html += (
            f'<tr><td style="padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.04);">'
            f'<a href="{t.get("link","#")}" style="color:#00f0ff;font-size:14px;font-weight:700;'
            f'text-decoration:none;font-family:Arial,sans-serif;">{name}</a>'
            f'<span style="color:#64748b;font-size:11px;margin-left:8px;'
            f'font-family:Arial,sans-serif;">{t.get("category","")}</span>'
            f'<p style="color:#94a3b8;font-size:12px;margin:4px 0 0 0;'
            f'font-family:Arial,sans-serif;">{(t.get("what_it_does") or "")[:120]}</p>'
            f'</td></tr>'
        )
    if not t_html:
        t_html = '<tr><td style="color:#64748b;font-family:Arial,sans-serif;">None this week.</td></tr>'

    # ── research rows ──
    r_html, seen_research = "", set()
    for r in all_research[:5]:
        title = r.get("title", "")
        if title in seen_research:
            continue
        seen_research.add(title)
        col = "#b55fe6" if r.get("category") == "Research Paper" else "#00f0ff"
        r_html += (
            f'<tr><td style="padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.04);">'
            f'<span style="color:{col};font-size:10px;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:1px;font-family:Arial,sans-serif;">{r.get("category","")}</span>'
            f'<p style="margin:4px 0 0 0;"><a href="{r.get("link","#")}" '
            f'style="color:#f1f5f9;font-size:13px;font-weight:600;text-decoration:none;'
            f'font-family:Arial,sans-serif;">{title}</a></p>'
            f'<p style="color:#94a3b8;font-size:12px;margin:4px 0 0 0;'
            f'font-family:Arial,sans-serif;">{(r.get("why_it_matters") or "")[:120]}</p>'
            f'</td></tr>'
        )
    if not r_html:
        r_html = '<tr><td style="color:#64748b;font-family:Arial,sans-serif;">None this week.</td></tr>'

    n = len(week_digests)
    days_label = f"{n} day{'s' if n != 1 else ''} of coverage"

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<title>Week in AI &mdash; {week_label}</title></head>
<body style="margin:0;padding:0;background:#060913;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#060913;">
<tr><td align="center" style="padding:30px 20px;">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:620px;">

<tr><td style="background:linear-gradient(135deg,#0e0b2e,#1a0533);
               padding:32px 36px;border-radius:16px 16px 0 0;
               border-top:3px solid #b55fe6;">
  <p style="color:#b55fe6;font-size:11px;text-transform:uppercase;
     letter-spacing:2px;margin:0 0 8px 0;">&#x1F4C5; WEEKLY ROUNDUP</p>
  <h1 style="color:#fff;font-size:26px;font-weight:900;margin:0;">Week in AI</h1>
  <p style="color:#94a3b8;font-size:14px;margin:8px 0 0 0;">
    {week_label} &nbsp;&middot;&nbsp; {days_label}</p>
</td></tr>

<tr><td style="background:#090e1d;padding:28px 36px;">
  <p style="color:#00f0ff;font-size:11px;font-weight:700;text-transform:uppercase;
     letter-spacing:2px;margin:0 0 16px 0;">&#x1F525; TOP STORIES THIS WEEK</p>
  <table width="100%" cellpadding="0" cellspacing="0">{h_html}</table>
</td></tr>

<tr><td style="background:#07111e;padding:28px 36px;">
  <p style="color:#00ffaa;font-size:11px;font-weight:700;text-transform:uppercase;
     letter-spacing:2px;margin:0 0 16px 0;">&#x1F527; TOOLS DISCOVERED</p>
  <table width="100%" cellpadding="0" cellspacing="0">{t_html}</table>
</td></tr>

<tr><td style="background:#090e1d;padding:28px 36px;">
  <p style="color:#b55fe6;font-size:11px;font-weight:700;text-transform:uppercase;
     letter-spacing:2px;margin:0 0 16px 0;">&#x1F4DA; RESEARCH &amp; REPOS</p>
  <table width="100%" cellpadding="0" cellspacing="0">{r_html}</table>
</td></tr>

<tr><td style="background:#07111e;padding:28px 36px;text-align:center;">
  <a href="{_APP_URL}"
     style="display:inline-block;background:linear-gradient(135deg,#b55fe6,#00f0ff);
            color:#060913;font-size:14px;font-weight:700;text-decoration:none;
            padding:14px 32px;border-radius:100px;">
    Open Full Dashboard &rarr;
  </a>
</td></tr>

<tr><td style="background:#04060c;padding:20px 36px;
               border-radius:0 0 16px 16px;text-align:center;">
  <p style="color:#334155;font-size:11px;margin:0;">
    Daily AI Digest &nbsp;&middot;&nbsp; Weekly Edition &nbsp;&middot;&nbsp; {week_label}
  </p>
</td></tr>

</table></td></tr></table>
</body></html>"""


def build_weekly_plain_text(week_digests, week_label):
    lines = [f"WEEK IN AI -- {week_label}", "=" * 50, ""]
    for day in week_digests:
        c = day.get("content", {})
        lines.append(f"-- {day['date']} --")
        for item in (c.get("biggest_news") or [])[:2]:
            lines.append(f"  * {item.get('headline', '')}")
            lines.append(f"    {item.get('link', '')}")
        lines.append("")
    lines += ["", f"Full dashboard: {_APP_URL}"]
    return "\n".join(lines)


def send_weekly_emails(smtp_settings, recipients, week_digests, week_label):
    """Sends the weekly digest to all recipients. Returns {sent, failed, errors}."""
    html      = build_weekly_html_email(week_digests, week_label)
    plain     = build_weekly_plain_text(week_digests, week_label)
    from_name = smtp_settings.get("from_name", "Daily AI Digest")
    from_addr = formataddr((from_name, smtp_settings["user"]))
    subject   = f"Week in AI — {week_label}"
    sent, failed, errors = 0, 0, []

    try:
        port = int(smtp_settings.get("port", 587))
        host = smtp_settings["host"]
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        server.login(smtp_settings["user"], smtp_settings.get("password", ""))

        for recip in recipients:
            try:
                msg            = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"]    = from_addr
                to_addr        = recip.get("email", "")
                msg["To"]      = formataddr((recip.get("name", ""), to_addr)) if recip.get("name") else to_addr
                msg.attach(MIMEText(plain, "plain", "utf-8"))
                msg.attach(MIMEText(html,  "html",  "utf-8"))
                server.sendmail(from_addr, [to_addr], msg.as_string())
                sent += 1
            except Exception as e:
                failed += 1
                errors.append(f"{recip.get('email', '?')}: {e}")

        server.quit()

    except Exception as e:
        errors.append(f"SMTP connection failed: {e}")

    return {"sent": sent, "failed": failed, "errors": errors}
