# Bots and model-call approval

## Product model

A **Bot** is the first-class owner of identity and work. A conversation is a
thread inside a Bot.

```text
Bot
├── name / description / instructions
├── approval_threshold (credits)
├── conversations (session_id)
└── monitors / goals (next layer)
```

The default Bot is created lazily for an owner and claims pre-existing sessions.
New conversations are mapped to the selected Bot. Switching Bots changes the
conversation list and the Bot instructions sent to the Agent.

Bot instructions are prepended to the Agent prompt at execution time. They are
subordinate to evidence and safety rules.

## Model-call approval

The approval policy is **only above budget**:

| Estimated credits | Behavior |
| --- | --- |
| 1–2 | auto-run |
| 4 | auto-run at the default threshold of 4 |
| 8 (deep research, portfolio, broad comparison) | show confirmation |

The same threshold is enforced server-side:

- Task creation without `model_approved` returns `409 model_approval_required`;
- the browser confirms through the preflight dialog and retries with
  `model_approved=true`;
- `/work` uses a fixed threshold of 4 and the same rule on the agent stream.

The estimate is a heuristic in credits, not a dollar forecast. Internal model
retries within one approved run are included in the run budget.

## APIs

```text
GET    /api/bots
POST   /api/bots
PATCH  /api/bots/{bot_id}
GET    /api/bots/{bot_id}/preflight?query=...
GET    /api/sessions?bot_id=...
POST   /api/tasks                  # bot_id, model_approved
POST   /api/agent/preflight
POST   /api/agent/query/stream     # model_approved
```

## Current boundary

Implemented: Bot profiles, default migration, conversation ownership, Bot
instructions, create-Bot UI, Bot/conversation sidebar, approval preflight and
server gate for `/bot`, `/work` approval modal and stream gate.

Implemented: Bot profiles, default migration, conversation ownership, Bot
instructions, create-Bot UI, Bot/conversation sidebar, approval preflight and
server gate for `/bot`, `/work` approval modal and stream gate, and Bot-scoped
long-term memory (legacy owner-level memories are visible only in the default
Bot).

Implemented: Bot settings/editing, delete with conversations/memories moved to
the default Bot, polling price monitors and a per-Bot in-app notification inbox.

Not yet implemented: scoped "delete all" by Bot, automatic model analysis from a
notification, announcement/earnings/scheduled triggers, external notification
channels. The worker never calls a language model on its own.


## Source relevance

Generic `web_search` results are checked before they become evidence. Results
that share only a generic word with the query (for example "Applied Industrial
Technologies" for an Applied Materials query) are omitted, counted as invalid
in `data_gaps`, and never included in the model context, source cards or
exports. Company-specific searches keep their existing identity verifier.

## Proactive monitors

`fb_monitors` stores a Bot-scoped price trigger. The worker polls existing market
data (never a new provider), computes the change from the last two closes, and
writes an unread `fb_notifications` row when the threshold and cooldown allow it.
It does not call a language model; opening/analyzing an alert remains a user
action.

```text
worker (polling)
  → due monitor
  → existing market-data adapter
  → threshold + cooldown
  → in-app notification
  → user decides whether to research
```

Notifications disclose the data timestamp and state that the result is polled /
delayed, not a real-time quote. The panel is intentionally scoped to the active
Bot.

### Analyze an alert

`POST /api/notifications/{id}/analyze` turns an alert into a normal research task
for the notification's Bot. It reuses the same budget gate: if the estimate is
above the Bot threshold, the endpoint returns 409 until the user confirms. The
task is linked back to the notification and opens in the monitor conversation.

### Announcement monitors

Announcement monitors use CNINFO for A-share symbols and deduplicate by the
latest announcement reference. They only contain the announcement title/date and
never read the filing body in the polling worker; the inbox can still turn the
alert into a normal research task with the model-budget gate.

### Earnings and watchlist reports

`earnings` monitors use the existing yfinance calendar (when available) and
notify only within the configured days-before window. `report` monitors build a
deterministic watchlist summary (daily or weekly) from the existing watchlist and
market-data adapter. Both paths use the same deduplicated notification inbox and
still do not call a language model automatically.

External notification channels are the remaining round.

### Outbound channels

The inbox is always written first. Optional webhook and email delivery run after
the notification is committed; failures are logged by exception type and never
roll back the alert.

```text
FINANCIAL_BOT_WEBHOOK_URL
FINANCIAL_BOT_WEBHOOK_TOKEN
FINANCIAL_BOT_NOTIFY_EMAIL
SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / SMTP_FROM
```

No secrets are returned by the API or written to logs.

### Per-Bot delivery settings

Bot settings now include market-hours-only price checks, a daily alert cap,
quiet hours for external delivery, webhook URL, notification email and a brief /
standard watchlist report style. The existing environment variables remain
fallbacks. Webhook URLs are never returned by the public Bot API; only a
`webhook_configured` boolean is exposed.

Price checks are skipped outside the symbol's regular session (US/HK/CN, no
holiday calendar). Quiet hours suppress webhook/email only; the in-app inbox is
always complete.


### Market holiday calendar

`api/financial_bot/market_calendar.py` contains an offline full-day closure table
for US/HK/CN regular sessions in 2026–2027. It is deliberately conservative and
does not model half-days or ad-hoc closures; it is not a live exchange feed.

### Channel test

`POST /api/bots/{id}/test-notification` sends a synthetic payload through the
Bot's configured webhook/email. The result only reports booleans and never
returns the webhook URL.

### Inbox grouping

The inbox groups notifications by monitor and shows the latest alert plus a
count. Marking the group read marks all notifications in that monitor group.

### Daily digest and delivery audit

Bot settings choose `immediate` or `daily` external delivery. In daily mode the
in-app inbox still receives every alert immediately, but outbound webhook/email
is replaced by one digest at the configured local hour. Delivery attempts are
recorded in `fb_notification_deliveries` with sent/failed status; no notification
body is copied into that table.

### Delivery log and retry

`fb_notification_deliveries` stores channel, status, attempts and the outbound
payload for failed sends. The list API deliberately omits the payload; a retry
endpoint reloads it, resends only the failed channel and increments attempts.

### Digest preview / send now

`GET /api/bots/{id}/digest-preview` returns the unread-notification digest without
sending. `POST /api/bots/{id}/digest/send` sends it immediately (still best
effort and redacted). The preview never contains webhook credentials.

### Automatic delivery retry

Failed deliveries are scheduled at 1, 5, 15 then 60 minute intervals up to four
attempts. A missing payload stops retrying. The manual retry endpoint uses the
same scheduling path and increments `attempts`.

### Half-days, ad-hoc closures, delivery filters

`market_calendar.py` models US/HK early closes for 2026 and accepts a
comma-separated `FINANCIAL_BOT_CALENDAR_CLOSURES` list of extra full-day closures.
The delivery log can be filtered by status/channel and exported as CSV from the
UI (the API also accepts `status` and `channel` query parameters).

### Ad-hoc closures, monitor mute, dead letters, export

Bot settings manage owner-scoped ad-hoc market closures (`fb_market_closures`);
the worker combines them with the static holiday table. Each monitor can be
muted for two hours (or unmuted), which skips its checks without changing its
schedule. Deliveries that fail four times become `dead` and stop automatic retry;
the manual retry endpoint can revive one. `GET /api/deliveries/export` emits a
server-generated CSV with the same status/channel filters.
