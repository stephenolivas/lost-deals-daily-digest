#!/usr/bin/env python3
"""
One-off test: sends a sample Lost Deals Digest message to a test Slack user.

Confirms two things:
  1. The bot token works
  2. The Slack mrkdwn formatting renders the way we want before Michael sees it

Run:
    SLACK_BOT_TOKEN=xoxb-... python test_slack.py
"""

import os
import sys
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

# Hard-coded test recipient — Stephen's own Slack ID.
# Swap to U0AE7QUGP42 (Michael) only after this looks right.
TEST_USER_ID = "U0A7QRN25S8"

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
if not SLACK_BOT_TOKEN:
    sys.exit("Missing SLACK_BOT_TOKEN env var. Set it and rerun.")

PACIFIC = ZoneInfo("America/Los_Angeles")
today = datetime.now(PACIFIC).strftime("%A, %B %-d, %Y")

# Sample message mimicking the real digest format
message = f"""*Lost Deals Digest — {today} (TEST)*
*3 deals lost yesterday:*

• *Acme Vending Co.*
    • Lost Reason: Couldn't find suitable locations
    • <https://app.close.com/lead/lead_test123|View in Close>

• *Hometown Snacks LLC*
    • Lost Reason: Price too high vs competitor quote
    • <https://app.close.com/lead/lead_test456|View in Close>

• *Sunset Refreshments*
    • Lost Reason: Timing not right, revisit Q3
    • <https://app.close.com/lead/lead_test789|View in Close>

_This is a test message. If you see this and the formatting looks right, the Slack integration is working._"""

print("─── Sending this to Slack ───")
print(message)
print("─────────────────────────────\n")

resp = requests.post(
    "https://slack.com/api/chat.postMessage",
    headers={
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    },
    json={
        "channel": TEST_USER_ID,
        "text": message,
        "unfurl_links": False,
        "unfurl_media": False,
    },
    timeout=30,
)

body = resp.json()
if body.get("ok"):
    print(f"✓ Sent to {TEST_USER_ID} — check your Slack DMs.")
else:
    print(f"✗ Slack rejected the message:")
    print(f"  error: {body.get('error')}")
    print(f"  full response: {body}")
    sys.exit(1)
