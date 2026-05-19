#!/usr/bin/env python3
"""
Lost Deals Daily Digest — Close CRM → Slack

Runs daily at 8am Pacific. Pulls all leads whose status changed to "💔 Lost"
yesterday (Pacific time), grabs the Lost Reason custom field, and sends a
formatted Slack DM to Michael Schultheiss for YouTube content planning.

Triggered by GitHub Actions:
  - Primary: cron-job.org → workflow_dispatch (timezone-correct)
  - Backup: schedule cron at 16:00 UTC

Run locally with --dry-run to preview the Slack message without sending.
"""

import os
import sys
import time
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ─── Config ─────────────────────────────────────────────────────
CLOSE_API_KEY = os.environ["CLOSE_API_KEY"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

SLACK_USER_ID = "U0AE7QUGP42"  # Michael Schultheiss
LOST_REASON_FIELD = "custom.cf_R4i05fLNOQP8yveAs4ofTMMYGAQnkLLklunP4lov2Bt"
LOST_STATUS_LABEL = "💔 Lost"

CLOSE_BASE = "https://api.close.com/api/v1"
PACIFIC = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")

DRY_RUN = "--dry-run" in sys.argv


# ─── Time helpers ───────────────────────────────────────────────
def yesterday_bounds_utc():
    """Return (start_utc_iso, end_utc_iso) for 'yesterday' in Pacific time."""
    now_pacific = datetime.now(PACIFIC)
    today_start = now_pacific.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    yesterday_end = today_start
    return (
        yesterday_start.astimezone(UTC).isoformat(),
        yesterday_end.astimezone(UTC).isoformat(),
    )


# ─── Close API ──────────────────────────────────────────────────
def close_get(path, params=None):
    """GET against Close API with basic auth + simple 429 backoff."""
    auth = (CLOSE_API_KEY, "")
    last_resp = None
    for attempt in range(3):
        r = requests.get(f"{CLOSE_BASE}{path}", auth=auth, params=params, timeout=30)
        last_resp = r
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    last_resp.raise_for_status()


def fetch_lost_status_changes(start_utc, end_utc):
    """Fetch all status_change activities in window where new status = Lost."""
    results = []
    skip = 0
    while True:
        data = close_get("/activity/status_change/lead/", params={
            "date_created__gt": start_utc,
            "date_created__lt": end_utc,
            "_limit": 100,
            "_skip": skip,
        })
        batch = data.get("data", [])
        for sc in batch:
            if sc.get("new_status_label") == LOST_STATUS_LABEL:
                results.append(sc)
        if not data.get("has_more"):
            break
        skip += len(batch)
    return results


def fetch_lead(lead_id):
    return close_get(f"/lead/{lead_id}/")


# ─── Slack ──────────────────────────────────────────────────────
def build_message(deals):
    today_pacific = datetime.now(PACIFIC).strftime("%A, %B %-d, %Y")

    if not deals:
        return f"*Lost Deals Digest — {today_pacific}*\n\nNo deals lost yesterday. 🎉"

    plural = "s" if len(deals) != 1 else ""
    lines = [
        f"*Lost Deals Digest — {today_pacific}*",
        f"*{len(deals)} deal{plural} lost yesterday:*",
        "",
    ]

    # Group deals by Lost Reason
    groups = {}
    for d in deals:
        reason = d["reason"] if d["reason"] else "No reason given"
        groups.setdefault(reason, []).append(d)

    # Sort: most common first, but force "No reason given" to the bottom.
    def sort_key(item):
        reason, group_deals = item
        is_no_reason = reason == "No reason given"
        return (is_no_reason, -len(group_deals), reason.lower())

    for reason, group_deals in sorted(groups.items(), key=sort_key):
        lines.append(f"*{reason}* ({len(group_deals)})")
        for d in sorted(group_deals, key=lambda x: x["name"].lower()):
            lines.append(f"• {d['name']} | <{d['url']}|View in Close>")
        lines.append("")

    return "\n".join(lines).rstrip()


def send_slack_dm(user_id, text):
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "channel": user_id,
        "text": text,
        "unfurl_links": False,
        "unfurl_media": False,
    }
    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        json=payload,
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"Slack API error: {body}")


# ─── Main ───────────────────────────────────────────────────────
def main():
    start_utc, end_utc = yesterday_bounds_utc()
    print(f"Fetching Lost status changes between {start_utc} and {end_utc}")

    status_changes = fetch_lost_status_changes(start_utc, end_utc)
    print(f"Found {len(status_changes)} Lost status change(s)")

    # Dedupe by lead — if a lead got toggled Lost multiple times yesterday,
    # count it once.
    seen = set()
    deals = []
    for sc in status_changes:
        lead_id = sc.get("lead_id")
        if not lead_id or lead_id in seen:
            continue
        seen.add(lead_id)

        try:
            lead = fetch_lead(lead_id)
        except Exception as e:
            print(f"  Skipping {lead_id}: {e}")
            continue

        deals.append({
            "name": lead.get("display_name") or lead.get("name") or "(no name)",
            "reason": (lead.get(LOST_REASON_FIELD) or "").strip(),
            "url": f"https://app.close.com/lead/{lead_id}/",
        })

    deals.sort(key=lambda d: d["name"].lower())

    message = build_message(deals)
    print("\n─── Slack message preview ───")
    print(message)
    print("─────────────────────────────\n")

    if DRY_RUN:
        print("DRY RUN — not sending.")
        return

    send_slack_dm(SLACK_USER_ID, message)
    print(f"✓ Sent digest to {SLACK_USER_ID} ({len(deals)} deal(s))")


if __name__ == "__main__":
    main()
