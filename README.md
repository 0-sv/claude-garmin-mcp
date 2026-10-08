# Garmin chat MCP

A local MCP server built with uv, python-garminconnect and the official MCP Python
SDK. Three read-only Garmin tools and four local symptom diary tools run over stdio.
Python 3.12+ is required.

## Setup

```sh
uv sync --locked
uv run garmin-login
```

Enter your Garmin email, password and MFA code in your terminal. Password and MFA
input are hidden. The server never asks for credentials through chat.
Tokens are stored outside this repository in `~/.local/share/claude-garmin-mcp`
with owner-only permissions. Set `GARMIN_TOKEN_DIR` to override that directory
for both the login command and server. Re-run login if the session is rejected.
Your watch must have synced to Garmin Connect before its data is available.

## Connect your MCP client

For a client supporting local stdio servers, add this entry to its MCP configuration.
These absolute paths are for this checkout; adjust them on another machine.

```json
{
  "mcpServers": {
    "garmin": {
      "command": "/Users/sv/.local/bin/uv",
      "args": [
        "--directory", "/Users/sv/dev/claude-garmin-mcp",
        "run", "--frozen", "garmin-mcp"
      ]
    }
  }
}
```

Restart/reconnect the client after editing its configuration. Running
`uv run garmin-mcp` directly waits for MCP messages; it is not an interactive chat.

## Tools

| Tool | Questions it supports |
| --- | --- |
| `get_daily_summary(day)` | How did I sleep? What were my steps, resting HR, stress and HRV on this date? |
| `list_activities(start_date, end_date, activity_type?, limit?)` | What runs did I do last week? How much distance did I cover? |
| `get_activity_details(activity_id)` | Tell me about that run. How hard was it? |

Dates use Garmin calendar dates. Activity ranges are inclusive, at most 31 days;
responses contain at most 100 activities and explicitly flag truncation. Narrow
the range when truncated before computing totals. Missing metrics remain missing;
unsupported health sections report errors without discarding successful sections.
Sleep/HRV responses omit intraday arrays. Activity lists include compact fields;
details contain Garmin's activity metadata and summary, not sample/GPS streams.
The server exposes no Garmin account mutation or arbitrary endpoint tools.

Try: “List my runs from 2026-10-01 to 2026-10-07, then tell me about the longest.”

## Tracer bullet milestones

1. **Implemented:** uv project, separate login, three read-only tools, protocol tests.
2. **Live acceptance:** log in locally, connect a client, ask for a known activity
   and daily summary, and compare dates/units/values with Garmin Connect.
3. **Next slice:** `get_health_metrics(start_date, end_date, metrics)` for bounded
   sleep/HRV/stress trends; then `get_training_status(day)` for training/recovery.
   Add caching when repeated queries demonstrate a need.

The assistant can compare days with the current tools. Bulk health trends are
intentionally a later slice. The local diary uses SQLite; there is no background
sync or Garmin write access.
Unofficial Garmin authentication and endpoints can change; dependencies are locked.
The MCP SDK stays on its 1.x API and garminconnect on its native-auth 0.3.x API.

## Verification

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Tests use fake Garmin responses through MCP sessions and launch the actual stdio
entry point with an empty token directory. They do not log into Garmin or read
personal health data. Live acceptance requires your interactive login.

Upstream: [python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
and [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x).

## Symptom diary

The diary works without Garmin login. It is created on first diary use, stored
locally outside the repo at `~/.local/share/claude-garmin-mcp/diary.sqlite3` with
owner-only file permissions. Override with `GARMIN_DIARY_PATH` in the MCP server's
environment. It is a normal SQLite database, not encrypted. Selected entries are
returned to your chat client when it calls diary tools.

- `log_diary_entry(entry)`: save symptoms, food/drink intake, or a symptom-free check-in.
- `get_diary_entries(start_date, end_date, limit?, offset?)`: read an inclusive
  date range; follow `next_offset` for additional results.
- `update_diary_entry(entry_id, entry)`: replace an entry with corrected values;
  send all fields to retain. The original logging time is preserved.
- `delete_diary_entry(entry_id)`: remove a specific entry.

Entries require `occurred_at` with a timezone offset, for example
`2026-10-08T14:00:00+02:00`. Use your Garmin-local offset to align daily summaries.
The server separately records when you logged and last updated each entry in UTC.
Optional fields: pain, bloating, nausea, urgency, and perceived_stress (0–10),
bristol_type (1–7), structured food/alcohol/caffeine lists, and notes (medication
changes or other context).
Omitted scores stay unknown. `symptom_free: true` explicitly records a good-day
check-in; days without entries are unrecorded, not assumed symptom-free.
Entries are listed by local occurrence date, then logging time.

After reconnecting your MCP client, try:

> Log bloating 6/10 at 2 pm today, after lunch.

The assistant should clarify any missing date/offset, save the entry, and return
its ID. Then ask:

> Read that day's diary and Garmin daily summary together.

This is the diary tracer bullet: save → read back → pair with Garmin measurements.
It does not yet calculate correlations or establish causes.

### Check-in prompts

Server instructions ask the assistant to check today's diary during conversations
about gut symptoms and offer one brief check-in if none exists, including on good
days. The assistant should not repeat a declined offer in that conversation, and
should only save information you report and ask/agree to log. Following these
instructions depends on your chat client; this is not an automatic notification.

A stdio MCP server cannot start a chat, wake the assistant, or deliver scheduled
reminders. For daily prompts outside a conversation, use a calendar/phone reminder
or a separately configured scheduler. No scheduled reminder is configured here.


### Food, alcohol and caffeine

Use the existing diary tools for intake-only entries or intake alongside symptoms:

- `food`: items with `name` and optional `amount` (e.g. “one bowl”).
- `alcohol`: drinks with `name`, optional `amount`, total `volume_ml` consumed,
  and `abv_percent` if known.
- `caffeine`: sources with `name`, optional `amount`, and total `caffeine_mg`
  consumed if known.

Names and portions preserve what you report. The assistant should not guess
ingredients, caffeine doses or alcohol strength. Unknown intake remains null,
not zero. Lists must contain at least one item when provided; use notes for
explicit abstinence reports and their time period, such as “no alcohol today”.

Try “Log one coffee at 9 am”, “Log two 330 ml beers, 5% ABV, at 7 pm”, or
“Log a bowl of pasta at 1 pm”. Supply the date/offset if the assistant needs it.
Different consumption times use separate entries so timing relative to symptoms
is preserved. Symptoms are optional, and intake alone does not imply symptom-free.

Existing entries require no migration. Older notes remain intact; their new
intake fields are returned as unknown until explicitly corrected. Reconnect your
MCP client after updating to load the new tool schemas.
