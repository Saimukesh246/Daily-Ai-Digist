"""
Email delivery module for Daily AI Digest.
Builds a rich HTML email from digest JSON and sends via SMTP.
Works with any SMTP provider: Gmail (587/STARTTLS), SSL (465), or SendGrid relay.
"""

import os
import smtplib
import ssl
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from datetime import datetime

# Public URL of the dashboard — set APP_URL env var in production
_APP_URL = os.environ.get("APP_URL", "http://localhost:8000")

logger = logging.getLogger("emailer")


def build_html_email(digest_content, date_str):
    """Converts a digest JSON dict into an email-client-safe HTML string (inline CSS, table layout)."""
    c = digest_content

    try:
        formatted_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %d, %Y")
    except Exception:
        formatted_date = date_str

    # --- Pre-compute all dynamic sections ---

    trend = c.get("editorial_trend", {})
    trend_title = trend.get("title", "Today's AI Landscape")
    trend_paras = "".join(
        f'<p style="color:#94a3b8;font-size:15px;line-height:1.7;margin:0 0 12px 0;font-family:Arial,Helvetica,sans-serif;">{p}</p>'
        for p in trend.get("paragraphs", [])
    )

    # Top 3 news items
    news_blocks = ""
    for item in c.get("biggest_news", [])[:3]:
        headline = item.get("headline", "")
        summary  = item.get("summary", "")
        why      = item.get("why_it_matters", "")
        tldr     = item.get("tldr", "")
        link     = item.get("link", "#")
        news_blocks += (
            '<table width="100%" cellpadding="0" cellspacing="0" style="background:#161e35;border-radius:10px;margin-bottom:16px;border:1px solid rgba(255,255,255,0.05);">'
            '<tr><td style="padding:20px 24px;">'
            '<p style="color:#00f0ff;font-size:10px;text-transform:uppercase;letter-spacing:1.5px;margin:0 0 8px 0;font-family:Arial,Helvetica,sans-serif;">TOP STORY</p>'
            f'<h4 style="color:#f1f5f9;font-size:16px;font-weight:700;margin:0 0 10px 0;line-height:1.4;font-family:Arial,Helvetica,sans-serif;">{headline}</h4>'
            f'<p style="color:#94a3b8;font-size:14px;line-height:1.6;margin:0 0 12px 0;font-family:Arial,Helvetica,sans-serif;">{summary}</p>'
            '<table width="100%" cellpadding="0" cellspacing="0" style="border-left:3px solid #00f0ff;margin-bottom:12px;">'
            f'<tr><td style="padding:8px 14px;background:rgba(0,240,255,0.05);"><p style="color:#94a3b8;font-size:13px;margin:0;font-family:Arial,Helvetica,sans-serif;"><strong style="color:#00f0ff;">WHY IT MATTERS:</strong> {why}</p></td></tr>'
            '</table>'
            f'<p style="color:#64748b;font-size:12px;margin:0 0 12px 0;font-family:Arial,Helvetica,sans-serif;"><strong style="color:#c0cce0;">TL;DR:</strong> {tldr}</p>'
            f'<a href="{link}" style="display:inline-block;color:#00f0ff;font-size:13px;text-decoration:none;border:1px solid rgba(0,240,255,0.4);padding:6px 14px;border-radius:6px;font-family:Arial,Helvetica,sans-serif;">Read Source &#8594;</a>'
            '</td></tr></table>'
        )

    # Tools table — up to 5 rows
    tools_rows = ""
    for tool in c.get("discovered_tools", [])[:5]:
        tools_rows += (
            '<tr>'
            f'<td style="padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.05);color:#f1f5f9;font-size:14px;font-weight:600;font-family:Arial,Helvetica,sans-serif;">{tool.get("tool","")}</td>'
            f'<td style="padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.05);font-family:Arial,Helvetica,sans-serif;"><span style="background:rgba(181,95,230,0.15);color:#b55fe6;font-size:11px;padding:3px 8px;border-radius:20px;">{tool.get("category","")}</span></td>'
            f'<td style="padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.05);color:#94a3b8;font-size:13px;font-family:Arial,Helvetica,sans-serif;">{tool.get("what_it_does","")}</td>'
            f'<td style="padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.05);color:#00ffaa;font-size:12px;font-family:Arial,Helvetica,sans-serif;white-space:nowrap;">{tool.get("pricing","")}</td>'
            '</tr>'
        )

    # What changed — up to 3 rows
    changes_rows = ""
    for ch in c.get("what_changed", [])[:3]:
        changes_rows += (
            '<tr>'
            f'<td style="padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.05);color:#f1f5f9;font-size:14px;font-weight:600;font-family:Arial,Helvetica,sans-serif;">{ch.get("tool_or_company","")}</td>'
            f'<td style="padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.05);color:#475569;font-size:13px;text-decoration:line-through;font-family:Arial,Helvetica,sans-serif;">{ch.get("yesterday","")}</td>'
            f'<td style="padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.05);color:#00f0ff;font-size:13px;font-family:Arial,Helvetica,sans-serif;">{ch.get("today","")}</td>'
            f'<td style="padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.05);color:#94a3b8;font-size:13px;font-family:Arial,Helvetica,sans-serif;">{ch.get("why_it_matters","")}</td>'
            '</tr>'
        )

    # Quick takes — up to 4
    takes_html = ""
    for take in c.get("quick_takes", [])[:4]:
        hype = take.get("hype_level", "")
        hype_color = "#00f0ff"
        if "underrated" in hype.lower():
            hype_color = "#00ffaa"
        elif "overhyped" in hype.lower():
            hype_color = "#ff2e63"
        takes_html += (
            '<table width="100%" cellpadding="0" cellspacing="0" style="background:#161e35;border-radius:8px;margin-bottom:10px;border:1px solid rgba(255,255,255,0.05);">'
            '<tr><td style="padding:14px 18px;font-family:Arial,Helvetica,sans-serif;">'
            '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td><strong style="color:#f1f5f9;font-size:14px;">{take.get("topic","")}</strong></td>'
            f'<td align="right"><span style="color:{hype_color};font-size:11px;font-weight:600;">{hype}</span></td>'
            '</tr></table>'
            f'<p style="color:#94a3b8;font-size:13px;margin:8px 0 0 0;line-height:1.6;">{take.get("opinion","")}</p>'
            '</td></tr></table>'
        )

    # What to watch — up to 5
    watch_rows = ""
    for w in c.get("what_to_watch", [])[:5]:
        watch_rows += (
            '<tr><td style="padding:10px 16px;border-bottom:1px solid rgba(255,255,255,0.04);">'
            '<table cellpadding="0" cellspacing="0"><tr>'
            '<td style="width:20px;vertical-align:top;padding-top:2px;color:#00f0ff;font-size:18px;font-family:Arial,Helvetica,sans-serif;">&#8250;</td>'
            '<td style="padding-left:8px;">'
            f'<strong style="color:#f1f5f9;font-size:14px;display:block;margin-bottom:4px;font-family:Arial,Helvetica,sans-serif;">{w.get("item","")}</strong>'
            f'<span style="color:#94a3b8;font-size:13px;line-height:1.5;font-family:Arial,Helvetica,sans-serif;">{w.get("details","")}</span>'
            '</td></tr></table>'
            '</td></tr>'
        )

    # --- Assemble the full HTML email ---
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="dark">
<title>Daily AI Digest &#8212; {formatted_date}</title>
</head>
<body style="margin:0;padding:0;background-color:#060913;font-family:Arial,Helvetica,sans-serif;-webkit-text-size-adjust:100%;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#060913;">
<tr><td align="center" style="padding:24px 12px;">
<table width="640" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;width:100%;">

  <!-- HEADER -->
  <tr><td style="background:linear-gradient(135deg,#1a1040 0%,#0e1a35 100%);border-radius:16px 16px 0 0;padding:30px 36px;border-bottom:2px solid rgba(0,240,255,0.2);">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td>
        <p style="color:#00f0ff;font-size:10px;text-transform:uppercase;letter-spacing:3px;margin:0 0 6px 0;font-family:Arial,Helvetica,sans-serif;">DAILY AI INTELLIGENCE REPORT</p>
        <h1 style="color:#f1f5f9;font-size:26px;font-weight:900;margin:0;line-height:1.2;font-family:Arial,Helvetica,sans-serif;">AI Digest</h1>
        <p style="color:#64748b;font-size:13px;margin:5px 0 0 0;font-family:Arial,Helvetica,sans-serif;">Your autonomous daily briefing on AI innovation</p>
      </td>
      <td align="right" valign="top">
        <p style="color:#94a3b8;font-size:12px;margin:0;font-family:Arial,Helvetica,sans-serif;white-space:nowrap;">{formatted_date}</p>
      </td>
    </tr></table>
  </td></tr>

  <!-- EDITORIAL TREND -->
  <tr><td style="background:#12192e;padding:26px 36px;border-left:3px solid #b55fe6;">
    <p style="color:#b55fe6;font-size:10px;text-transform:uppercase;letter-spacing:2px;margin:0 0 10px 0;font-family:Arial,Helvetica,sans-serif;">TODAY&#39;S CORE TREND FOCUS</p>
    <h2 style="color:#f1f5f9;font-size:20px;font-weight:700;margin:0 0 14px 0;line-height:1.4;font-family:Arial,Helvetica,sans-serif;">{trend_title}</h2>
    {trend_paras}
  </td></tr>

  <!-- BIGGEST NEWS -->
  <tr><td style="background:#0e1426;padding:26px 36px;">
    <h3 style="color:#00f0ff;font-size:12px;text-transform:uppercase;letter-spacing:2px;margin:0 0 18px 0;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,0.06);font-family:Arial,Helvetica,sans-serif;">1 / Biggest AI News Today</h3>
    {news_blocks}
  </td></tr>

  <!-- TOOLS TABLE -->
  <tr><td style="background:#0a0f1e;padding:26px 36px;">
    <h3 style="color:#00f0ff;font-size:12px;text-transform:uppercase;letter-spacing:2px;margin:0 0 14px 0;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,0.06);font-family:Arial,Helvetica,sans-serif;">2 / New AI Tools Discovered</h3>
    <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid rgba(255,255,255,0.07);border-radius:10px;overflow:hidden;">
      <tr style="background:rgba(0,240,255,0.05);">
        <th style="text-align:left;padding:10px 14px;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:1px;font-family:Arial,Helvetica,sans-serif;font-weight:600;">Tool</th>
        <th style="text-align:left;padding:10px 14px;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:1px;font-family:Arial,Helvetica,sans-serif;font-weight:600;">Category</th>
        <th style="text-align:left;padding:10px 14px;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:1px;font-family:Arial,Helvetica,sans-serif;font-weight:600;">What It Does</th>
        <th style="text-align:left;padding:10px 14px;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:1px;font-family:Arial,Helvetica,sans-serif;font-weight:600;">Pricing</th>
      </tr>
      {tools_rows}
    </table>
  </td></tr>

  <!-- WHAT CHANGED -->
  <tr><td style="background:#0e1426;padding:26px 36px;">
    <h3 style="color:#00f0ff;font-size:12px;text-transform:uppercase;letter-spacing:2px;margin:0 0 14px 0;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,0.06);font-family:Arial,Helvetica,sans-serif;">3 / What Changed Since Yesterday</h3>
    <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid rgba(255,255,255,0.07);border-radius:10px;overflow:hidden;">
      <tr style="background:rgba(0,240,255,0.05);">
        <th style="text-align:left;padding:10px 14px;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:1px;font-family:Arial,Helvetica,sans-serif;font-weight:600;">Company/Tool</th>
        <th style="text-align:left;padding:10px 14px;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:1px;font-family:Arial,Helvetica,sans-serif;font-weight:600;">Before</th>
        <th style="text-align:left;padding:10px 14px;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:1px;font-family:Arial,Helvetica,sans-serif;font-weight:600;">Now</th>
        <th style="text-align:left;padding:10px 14px;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:1px;font-family:Arial,Helvetica,sans-serif;font-weight:600;">Impact</th>
      </tr>
      {changes_rows}
    </table>
  </td></tr>

  <!-- QUICK TAKES -->
  <tr><td style="background:#0a0f1e;padding:26px 36px;">
    <h3 style="color:#00f0ff;font-size:12px;text-transform:uppercase;letter-spacing:2px;margin:0 0 14px 0;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,0.06);font-family:Arial,Helvetica,sans-serif;">7 / Quick Takes</h3>
    {takes_html}
  </td></tr>

  <!-- WHAT TO WATCH -->
  <tr><td style="background:#0e1426;padding:26px 36px;">
    <h3 style="color:#00f0ff;font-size:12px;text-transform:uppercase;letter-spacing:2px;margin:0 0 14px 0;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,0.06);font-family:Arial,Helvetica,sans-serif;">8 / What to Watch Tomorrow</h3>
    <table width="100%" cellpadding="0" cellspacing="0">{watch_rows}</table>
  </td></tr>

  <!-- CTA -->
  <tr><td style="background:linear-gradient(135deg,#1a1040 0%,#0e1a35 100%);padding:26px 36px;text-align:center;border-top:1px solid rgba(181,95,230,0.25);">
    <p style="color:#94a3b8;font-size:14px;margin:0 0 14px 0;font-family:Arial,Helvetica,sans-serif;">View the full interactive digest with all sections and source links</p>
    <a href="{_APP_URL}" style="display:inline-block;background:linear-gradient(135deg,#b55fe6,#00f0ff);color:#060913;font-weight:700;font-size:14px;text-decoration:none;padding:12px 28px;border-radius:8px;font-family:Arial,Helvetica,sans-serif;">Open Full Dashboard &#8594;</a>
  </td></tr>

  <!-- FOOTER -->
  <tr><td style="background:#060913;padding:18px 36px;border-radius:0 0 16px 16px;border-top:1px solid rgba(255,255,255,0.04);">
    <p style="color:#334155;font-size:11px;text-align:center;margin:0;line-height:1.6;font-family:Arial,Helvetica,sans-serif;">
      Daily AI Digest &bull; Autonomous AI intelligence system &bull; {formatted_date}<br>
      You are receiving this as a subscriber. To unsubscribe, contact your system administrator.
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def build_plain_text(digest_content, date_str):
    """Builds a plain-text fallback for email clients that don't render HTML."""
    c = digest_content
    sep = "-" * 48
    lines = [
        f"DAILY AI DIGEST — {date_str}",
        "=" * 48,
        "",
        "TODAY'S CORE TREND FOCUS",
        sep,
        c.get("editorial_trend", {}).get("title", ""),
    ]
    for p in c.get("editorial_trend", {}).get("paragraphs", []):
        lines.append(p)
    lines.append("")

    lines += ["1 / BIGGEST AI NEWS TODAY", sep]
    for item in c.get("biggest_news", [])[:3]:
        lines += [
            f"• {item.get('headline','')}",
            f"  {item.get('summary','')}",
            f"  TL;DR: {item.get('tldr','')}",
            f"  Link: {item.get('link','')}",
            "",
        ]

    lines += ["2 / NEW AI TOOLS", sep]
    for tool in c.get("discovered_tools", [])[:5]:
        lines.append(f"• {tool.get('tool','')} [{tool.get('category','')}] — {tool.get('pricing','')}")
        lines.append(f"  {tool.get('what_it_does','')}")
    lines.append("")

    lines += ["7 / QUICK TAKES", sep]
    for take in c.get("quick_takes", [])[:4]:
        lines.append(f"• {take.get('topic','')} [{take.get('hype_level','')}]")
        lines.append(f"  {take.get('opinion','')}")
    lines.append("")

    lines += ["8 / WHAT TO WATCH TOMORROW", sep]
    for w in c.get("what_to_watch", [])[:5]:
        lines.append(f"• {w.get('item','')}: {w.get('details','')}")

    lines += ["", "—", f"View full digest: {_APP_URL}", ""]
    return "\n".join(lines)


def send_emails(smtp_settings, recipients, digest_content, date_str):
    """
    Sends the daily digest to a list of recipients via SMTP.

    smtp_settings dict keys: host, port, user, password, from_name
    recipients: list of {"email": str, "name": str}
    Returns: {"sent": int, "failed": int, "errors": [str, ...]}
    """
    if not recipients:
        return {"sent": 0, "failed": 0, "errors": ["No recipients provided."]}

    html_body = build_html_email(digest_content, date_str)
    text_body = build_plain_text(digest_content, date_str)
    subject   = f"Daily AI Digest — {date_str}"

    host      = smtp_settings.get("host", "")
    port      = int(smtp_settings.get("port", 587))
    user      = smtp_settings.get("user", "")
    password  = smtp_settings.get("password", "")
    from_name = smtp_settings.get("from_name", "Daily AI Digest")
    from_addr = user

    results = {"sent": 0, "failed": 0, "errors": []}

    try:
        if port == 465:
            ctx    = ssl.create_default_context()
            server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.ehlo()
            if port == 587:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()

        if user and password:
            server.login(user, password)

        try:
            for recipient in recipients:
                to_email = recipient.get("email", "").strip()
                to_name  = recipient.get("name", "").strip()
                if not to_email:
                    continue
                try:
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = subject
                    msg["From"]    = formataddr((from_name, from_addr))
                    msg["To"]      = formataddr((to_name, to_email)) if to_name else to_email
                    msg.attach(MIMEText(text_body, "plain", "utf-8"))
                    msg.attach(MIMEText(html_body, "html",  "utf-8"))
                    server.sendmail(from_addr, [to_email], msg.as_string())
                    results["sent"] += 1
                    logger.info(f"Digest email sent to {to_email}")
                except Exception as exc:
                    results["failed"] += 1
                    err = f"Failed to send to {to_email}: {exc}"
                    results["errors"].append(err)
                    logger.error(err)
        finally:
            server.quit()

    except Exception as exc:
        err = f"SMTP connection error ({host}:{port}): {exc}"
        results["errors"].append(err)
        results["failed"] += len(recipients) - results["sent"]
        logger.error(err)

    return results
