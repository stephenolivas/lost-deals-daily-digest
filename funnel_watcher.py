"""
Funnel Change Watcher.

Every 15 minutes, checks Close CRM for lead updates that changed the
Funnel Name field. Filters out changes made by the API key owner (Stephen).
Sends a Slack DM per change to a configured user.

No state cache. Uses a lookback window slightly wider than the interval to
tolerate GitHub Actions cron drift. Occasional duplicate DMs possible if a
run is significantly delayed; misses avoided.
"""
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
# Custom field ID for "Funnel Name DEAL (Opp)" — a LEAD-level field despite
# the (Opp) suffix. Same ID used by the funnel sync script.
FUNNEL_FIELD_ID   = "cf_xqDQE8fkPsWa0RNEve7hcaxKblCe6489XeZGRDzyPdX"
FUNNEL_FIELD_PATH = f"custom.{FUNNEL_FIELD_ID}"

# Slack recipient. Hardcoded here (not a secret) to match the Lost Deals
# Digest pattern. Swap this ID if the recipient changes; no redeploy needed
# beyond the commit.
# TODO: replace with Stephen's actual Slack user ID before first run.
SLACK_USER_ID = "U0A7QRN25S8"  # e.g. "U01234ABCDE"

# 15-minute cron + 3-minute buffer for GitHub Actions cron drift. If drift
# exceeds 3 minutes we can miss events, so keep an eye on run timing.
LOOKBACK_MINUTES  = int(os.environ.get("LOOKBACK_MINUTES", "18"))

# Environment secrets (from GitHub Actions secrets)
CLOSE_API_KEY     = os.environ.get("CLOSE_API_KEY", "")
SLACK_BOT_TOKEN   = os.environ.get("SLACK_BOT_TOKEN", "")

DRY_RUN           = os.environ.get("DRY_RUN", "false").lower() == "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Close API helpers
# -----------------------------------------------------------------------------
def close_get(path, params=None, retry=3):
    """GET Close API with 0.5s throttle + 429 retry."""
    url = f"https://api.close.com/api/v1{path}"
    for _ in range(retry):
        time.sleep(0.5)
        resp = requests.get(url, auth=(CLOSE_API_KEY, ""), params=params, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 10))
            log.warning("429 rate limited, waiting %ds", wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Close API failed after {retry} retries: {path}")


def get_api_user():
    """Fetch the user_id + name of whoever owns the API key we're using."""
    me = close_get("/me/")
    uid = me.get("id")
    first = (me.get("first_name") or "").strip()
    last  = (me.get("last_name") or "").strip()
    name = " ".join(p for p in [first, last] if p) or me.get("email") or uid
    return uid, name


def fetch_lead_update_events(lookback_minutes):
    """
    Fetch lead update events from the past `lookback_minutes`.
    Cursor-paginated. Returns a list.

    Notes from Close's docs on /event/:
      - `_skip` is NOT supported — must use `_cursor` for pagination.
      - `_limit` caps at 50; default 50.
      - Events ordered by date_updated DESC.
      - Supported filter combo we use: object_type + action.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    cutoff_iso = cutoff.isoformat()

    log.info(
        "Fetching lead update events since %s (last %d minutes)",
        cutoff_iso, lookback_minutes,
    )

    events = []
    cursor = None
    page   = 0

    while True:
        params = {
            "object_type":      "lead",
            "action":           "updated",
            "date_updated__gt": cutoff_iso,
            "_limit":           50,
        }
        if cursor:
            params["_cursor"] = cursor

        data = close_get("/event/", params=params)
        batch = data.get("data", []) or []
        events.extend(batch)
        page += 1

        cursor = data.get("cursor_next")
        if not cursor or not batch:
            break
        if page >= 20:  # sanity cap: 20 pages × 50 = 1000 events per run
            log.warning("Hit page cap of 20 (~1000 events); results may be incomplete")
            break

    log.info("Fetched %d lead update events total across %d page(s)", len(events), page)
    return events


def extract_funnel_value(data):
    """Get funnel value from an event's data or previous_data snapshot.

    Returns the stripped string, or None if blank / not present.
    """
    if not data:
        return None
    v = data.get(FUNNEL_FIELD_PATH)
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v if v else None
    return v  # unexpected type; return as-is


def find_funnel_changes(events, exclude_user_id):
    """Return [(event, prev_funnel, curr_funnel), ...] for events where the
    funnel value differs and the user is not the API key owner.
    """
    results = []
    for event in events:
        # Skip changes made by the API key user (Stephen + our own automation)
        if event.get("user_id") == exclude_user_id:
            continue

        prev_funnel = extract_funnel_value(event.get("previous_data") or {})
        curr_funnel = extract_funnel_value(event.get("data") or {})

        if prev_funnel == curr_funnel:
            continue

        results.append((event, prev_funnel, curr_funnel))

    return results


def get_lead_display_name(lead_id, cache):
    """Fetch a lead's display_name, cached."""
    if lead_id in cache:
        return cache[lead_id]
    try:
        data = close_get(f"/lead/{lead_id}/", params={"_fields": "id,display_name"})
        name = data.get("display_name") or "(no name)"
    except Exception as e:
        log.warning("Failed to fetch lead %s: %s", lead_id, e)
        name = "(unknown lead)"
    cache[lead_id] = name
    return name


def get_user_display_name(user_id, cache):
    """Fetch a Close user's display name, cached."""
    if not user_id:
        return "(no user)"
    if user_id in cache:
        return cache[user_id]
    try:
        data = close_get(f"/user/{user_id}/")
        first = (data.get("first_name") or "").strip()
        last  = (data.get("last_name") or "").strip()
        name  = " ".join(p for p in [first, last] if p) or data.get("email") or user_id
    except Exception as e:
        log.warning("Failed to fetch user %s: %s", user_id, e)
        name = user_id
    cache[user_id] = name
    return name


# -----------------------------------------------------------------------------
# Formatting
# -----------------------------------------------------------------------------
def format_relative_time(iso_ts):
    """'N min ago' from an ISO timestamp."""
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        mins = int(delta.total_seconds() / 60)
        if mins < 1:
            return "just now"
        if mins == 1:
            return "1 min ago"
        if mins < 60:
            return f"{mins} min ago"
        hours = mins // 60
        if hours == 1:
            return "1 hour ago"
        return f"{hours} hours ago"
    except Exception:
        return iso_ts or "unknown time"


def build_slack_message(event, prev_funnel, curr_funnel, lead_name, user_name):
    """Two-line Slack message describing one funnel change."""
    lead_id  = event.get("object_id", "")
    lead_url = f"https://app.close.com/lead/{lead_id}/"
    when     = format_relative_time(event.get("date_updated", ""))

    prev_display = f"`{prev_funnel}`" if prev_funnel else "*Blank*"
    curr_display = f"`{curr_funnel}`" if curr_funnel else "*Blank*"

    return (
        f"*Funnel changed by {user_name}* — <{lead_url}|{lead_name}>\n"
        f"{prev_display} → {curr_display} · {when}"
    )


# -----------------------------------------------------------------------------
# Slack
# -----------------------------------------------------------------------------
def send_slack_dm(user_id, text):
    """POST to chat.postMessage."""
    if DRY_RUN:
        log.info("[DRY] Would send Slack DM to %s:\n%s\n", user_id, text)
        return

    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type":  "application/json; charset=utf-8",
        },
        json={
            "channel":       user_id,
            "text":          text,
            "unfurl_links":  False,
        },
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data}")
    log.info("Sent Slack DM to %s", user_id)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main():
    # --- Validate config ---
    if not CLOSE_API_KEY:
        log.error("CLOSE_API_KEY not set")
        return 1
    if not DRY_RUN:
        if not SLACK_BOT_TOKEN:
            log.error("SLACK_BOT_TOKEN not set")
            return 1
        if not SLACK_USER_ID or SLACK_USER_ID.startswith("REPLACE_"):
            log.error("SLACK_USER_ID constant not set — edit funnel_watcher.py")
            return 1

    # --- Identify API key owner to exclude ---
    try:
        exclude_user_id, exclude_user_name = get_api_user()
    except Exception as e:
        log.error("Failed to identify API key user: %s", e)
        return 1
    log.info("Excluding edits by API key owner: %s (%s)", exclude_user_name, exclude_user_id)

    # --- Fetch events ---
    try:
        events = fetch_lead_update_events(LOOKBACK_MINUTES)
    except Exception as e:
        log.error("Failed to fetch events: %s", e)
        return 1

    # --- Filter to funnel changes by other users ---
    changes = find_funnel_changes(events, exclude_user_id)
    log.info(
        "Scanned %d events; %d are funnel changes by non-excluded users",
        len(events), len(changes),
    )

    if not changes:
        log.info("Nothing to alert on this run")
        return 0

    # --- Send one DM per change ---
    lead_name_cache = {}
    user_name_cache = {}
    sent = 0
    failed = 0

    for event, prev_funnel, curr_funnel in changes:
        lead_id = event.get("object_id", "")
        user_id = event.get("user_id", "")

        lead_name = get_lead_display_name(lead_id, lead_name_cache)
        user_name = get_user_display_name(user_id, user_name_cache)

        text = build_slack_message(event, prev_funnel, curr_funnel, lead_name, user_name)

        try:
            send_slack_dm(SLACK_USER_ID, text)
            sent += 1
        except Exception as e:
            log.warning("Failed to send DM for lead %s: %s", lead_id, e)
            failed += 1

    log.info("Done: sent=%d failed=%d", sent, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
