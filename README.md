# Lost Deals Daily Digest

Daily Slack digest of yesterday's lost deals from Close CRM, sent to Michael Schultheiss to inform YouTube content planning. Common objections in the Lost Reason field → likely video topics.

---

## What it does

Each morning at 8am Pacific:

1. Queries Close's `/activity/status_change/` endpoint for everything that transitioned to `💔 Lost` between 12:00 AM and 11:59 PM Pacific yesterday
2. For each unique lead, pulls the lead name and Lost Reason custom field
3. Sends a Slack DM to Michael with the list and links back to Close

Empty days send a `No deals lost yesterday 🎉` message — confirms the script is alive even when there's nothing to report.

---

## Setup

### 1. GitHub Secrets

| Secret | Value |
|--------|-------|
| `CLOSE_API_KEY` | Close CRM API key (reuse the one from your other automations) |
| `SLACK_BOT_TOKEN` | Slack bot token, starts with `xoxb-` — see below |

### 2. Slack App setup

Most workspaces let non-admins create apps; the *install to workspace* step may need admin approval depending on your workspace policy. Michael doesn't need to do anything — `chat.postMessage` to a user ID opens the DM automatically.

1. Go to https://api.slack.com/apps → **Create New App** → **From scratch**
2. Name it (e.g. "Close CRM Digest"), pick the Modern Amenities workspace
3. **OAuth & Permissions** → **Bot Token Scopes** → add `chat:write`
4. Scroll up → **Install to Workspace** *(if this is blocked, you'll need admin approval — request it; if denied, switch to email fallback, see below)*
5. Copy the **Bot User OAuth Token** (`xoxb-...`) and add it to GitHub Secrets as `SLACK_BOT_TOKEN`

### 3. cron-job.org trigger (recommended, matches your other automations)

- **URL:** `https://api.github.com/repos/{your-username}/lost-deals-daily-digest/actions/workflows/daily-digest.yml/dispatches`
- **Method:** POST
- **Headers:**
  - `Authorization: Bearer YOUR_GITHUB_PAT`
  - `Accept: application/vnd.github+json`
- **Body:** `{"ref": "main"}`
- **Schedule:** 8:00 AM, timezone `America/Los_Angeles` (handles DST automatically)
- PAT scope: `workflow`

The workflow also has a backup `schedule` cron at 16:00 UTC (= 8am PST / 9am PDT) in case cron-job.org goes down. Both firing on the same day = digest sent twice. Acceptable; remove the schedule cron if you'd rather not.

---

## Running manually / locally

```bash
# Dry run — prints the Slack message, doesn't send
CLOSE_API_KEY=xxx SLACK_BOT_TOKEN=xxx python lost_deals_digest.py --dry-run

# Live
CLOSE_API_KEY=xxx SLACK_BOT_TOKEN=xxx python lost_deals_digest.py
```

You can also trigger the workflow on demand from the Actions tab in GitHub.

---

## Reference

| Thing | Value |
|-------|-------|
| Lost Reason field (Close lead custom field) | `cf_R4i05fLNOQP8yveAs4ofTMMYGAQnkLLklunP4lov2Bt` |
| Lost lead status label | `💔 Lost` |
| Recipient Slack user ID | `U0AE7QUGP42` (Michael Schultheiss) |
| Schedule | 8am Pacific daily |

---

## Notes on classification

- Uses `/activity/status_change/`, not lead `date_updated`. A lead only counts if it *transitioned to Lost yesterday* — not if it was already Lost and someone edited the record.
- If a lead bounced Lost → Open → Lost in the same day, it's deduplicated and reported once.
- "Yesterday" is 00:00 to 24:00 Pacific. All Close timestamps are UTC; the script handles the conversion (same pattern as the first-sales-call automation).
- The Lost Reason field is a lead-level custom field despite its name suggesting opportunity. The script pulls it directly from the lead object.

---

## Switching to email (if Slack doesn't pan out)

If your workspace blocks the app install, swap `send_slack_dm` for an SMTP send. About 10 lines of change — use Gmail's SMTP relay with an app password, send to `michael.schultheiss@modern-amenities.com`. The message body works as plain text with minimal reformatting (replace `<url|text>` Slack links with plain URLs).
