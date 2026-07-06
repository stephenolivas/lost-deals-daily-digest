# Funnel Change Watcher — Setup Summary

Quick reference on how this automation was built and wired up. Lives in the same repo as `lost-deals-daily-digest`, since they share the Slack app, Close API key, and Python dependencies.

---

## What it does

Every 15 minutes, checks Close CRM for lead updates that changed the Funnel Name field. Filters out changes made by the API key owner (Stephen — same person, same key as the funnel sync automation). Sends an immediate Slack DM to Stephen for each remaining change, so unexpected funnel edits get surfaced fast.

**Recipient:** Stephen Olivas (hardcoded in `funnel_watcher.py`)
**Message format:** one DM per change, showing lead + link, previous funnel → new funnel, who did it, how long ago.

---

## Architecture

```
GitHub Actions cron (every 15 min)
       ↓
funnel_watcher.py
       ↓
Close /event/        → filter to funnel changes by other users
Close /lead/{id}/     → resolve lead names
Close /user/{id}/     → resolve user names
       ↓
Slack chat.postMessage → DM to Stephen
```

No state, no cache. Re-runs cleanly. Uses an 18-minute lookback (3-min buffer over the 15-min interval) to tolerate GitHub Actions cron drift.

---

## Repo

**Repo:** `stephenolivas/lost-deals-daily-digest` (shared with the Lost Deals digest)

```
lost-deals-daily-digest/
├── lost_deals_digest.py                # existing — daily Lost digest to Michael
├── funnel_watcher.py                   # NEW — 15-min funnel change watcher to Stephen
├── test_slack.py                       # existing — Slack connectivity test
├── requirements.txt                    # existing — `requests` already in here
├── .github/workflows/
│   ├── daily-digest.yml                # existing — cron-job.org triggers this
│   ├── funnel-watcher.yml              # NEW — native GitHub cron every 15 min
│   └── test-slack.yml                  # existing
└── README.md                           # existing
```

The two automations do not share any code. They both use `requests` and hit the same Close API key + Slack bot token; that's the extent of the overlap.

---

## Setup steps taken

### 1. Slack app

**Reused the existing `Close CRM Digest` app** from Lost Deals. Same bot scope (`chat:write`), same `xoxb-` token. No new install needed. Slack auto-opens the DM the first time the bot posts to Stephen.

### 2. GitHub secrets

No new secrets needed. Reuses:

| Secret | Notes |
|--------|-------|
| `CLOSE_API_KEY` | Same key Lost Deals uses. |
| `SLACK_BOT_TOKEN` | Same `xoxb-` from the existing `Close CRM Digest` app. |

**Recipient is hardcoded, not a secret.** `SLACK_USER_ID` at the top of `funnel_watcher.py` — set to Stephen's user ID. Slack user IDs aren't sensitive; hardcoding matches the Lost Deals script's pattern for the same reason.

### 3. Cron trigger

Unlike Lost Deals (which uses cron-job.org to work around the "dual triggers send Michael two digests" concern), this workflow uses GitHub Actions' native `cron: '*/15 * * * *'` in `.github/workflows/funnel-watcher.yml`. Reasons:

- No dual-trigger concern: this workflow doesn't exist elsewhere, and native cron is the sole schedule
- 15-minute cadence works well with native cron; cron-job.org adds unnecessary hops
- Frequent commits to the repo prevent the 60-day-inactivity workflow-disable rule from firing

---

## How classification works

Queries Close's `/event/` endpoint with:

- `object_type: lead`
- `action: updated`
- `date_updated__gt: <18 minutes ago>`

For each event, compares the funnel field value in `previous_data` vs. `data`. If they differ AND `user_id` is not Stephen's, it's a match. Fetches lead name + user name (both cached per-run) and sends a Slack DM.

Blank values render as `*Blank*`. So a fill looks like `*Blank* → \`YouTube\`` and an overwrite looks like `\`Reactivation Email\` → \`YouTube\`` — visually distinct in the DM feed.

---

## Key IDs / values

| Thing | Value |
|-------|-------|
| Funnel Name field (Close custom field, on lead object) | `cf_xqDQE8fkPsWa0RNEve7hcaxKblCe6489XeZGRDzyPdX` |
| Recipient Slack user ID | Stephen Olivas (hardcoded in `funnel_watcher.py`) |
| Cadence | Every 15 min via GitHub Actions native cron |
| Lookback window | 18 minutes (15-min cron + 3-min drift buffer) |

---

## Message format

```
*Funnel changed by Jason Aaron* — Rick Herbel
`Reactivation Email` → `YouTube` · 3 min ago
```

or, for a fill:

```
*Funnel changed by Katie Chen* — Andrew Marsh
*Blank* → `Instagram` · 8 min ago
```

Lead name is a Slack link to the Close lead page.

---

## Manual controls

**Trigger a run on demand:** Actions tab → **Funnel Change Watcher** → **Run workflow**

**Dry run:** Actions tab → Run workflow → set `dry_run` to `true`. Logs what would be sent without sending.

**Look further back for testing:** workflow_dispatch has a `lookback_minutes` input. Bump to `1440` (24h) with `dry_run: true` on the first run to see recent change history and validate before flipping live.

**Local dry run:**
```bash
CLOSE_API_KEY=xxx SLACK_BOT_TOKEN=xxx DRY_RUN=true python funnel_watcher.py
```

---

## Owner / maintenance notes

- **Owner:** Stephen Olivas
- **Brittle to:**
  - Close field ID change. `FUNNEL_FIELD_ID` in `funnel_watcher.py` is the `cf_...` ID, not the label. Safe against Close renaming the field, but if the field is deleted and recreated (unlikely), update this constant.
  - Close event log retention (~30 days). Non-issue at 15-min cadence unless the workflow is disabled for a while.
- **Not deduped across runs:** if the watcher runs twice on the same window (cron + manual `Run workflow`), you may get duplicate DMs. Rare in practice.
- **If GitHub Actions cron is delayed 3+ min:** we may miss events at the tail of the window. If this happens frequently, widen `LOOKBACK_MINUTES` (accepting occasional duplicates).
- **Switching recipient:** change `SLACK_USER_ID` at the top of `funnel_watcher.py`. Nothing else needs to change.
- **Adding more excluded users** (if a service account is added later): edit the `find_funnel_changes` filter in `funnel_watcher.py` to check a list of user IDs instead of one.

---

## Related automations

Same repo, same Close API key, same Slack app:

- `lost_deals_digest.py` — daily digest of lost deals grouped by reason, DM to Michael

External but conceptually paired:

- [`utm_close_funnel_updater`](https://github.com/stephenolivas/utm_close_funnel_updater) — the funnel sync script that this watcher audits. Both use the same Close custom field ID and the same API key user.
