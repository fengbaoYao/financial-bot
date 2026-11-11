# Conversation thread migration

## Current model

`fb_messages` stores the continuous Bot thread:

```text
session_id, role, kind, content, task_id, sequence
```

Kinds:

- `message` / user prompt
- `answer` / final assistant answer
- `welcome` / persisted onboarding
- `activity` / completed tool activity
- `proactive` / Routine notification promoted into the thread

Tasks still own execution details and artifacts. The UI attaches a task to its
messages through `task_id`, renders the compact `Execution details` section, and
falls back to old task cards only when a legacy session has no messages.

## APIs

```text
GET  /api/sessions/{session_id}/messages
POST /api/sessions/{session_id}/welcome
POST /api/conversation/backfill
POST /api/threads
```

`GET .../messages` lazily backfills old task cards. `POST /conversation/backfill`
projected all local legacy sessions (23 sessions / 188 messages during the
migration run, then 0 / 0 on a repeat).

## Streaming

The executor publishes two complementary event types:

1. real model text deltas (`token` task events, source `model_delta`);
2. a validated summary fallback split into bounded chunks when the model only
   emitted a structured `final_result`.

The frontend accumulates `RunState.streamText` and shows a live Markdown answer
with a cursor until the final result document arrives.

## Side threads

`fb_threads` links a side session to `parent_session_id`. A side thread has its
own task/message history and the executor injects recent parent-thread
user/assistant messages as context, so the main thread is not mutated.

## Progressive disclosure

- user and assistant bubbles are the primary thread;
- execution details are collapsed by default;
- process/detail cards live behind `Execution details`;
- the right rail owns research overview, routines and artifacts.

## Migrations

- SQLite applies additive schema changes in `Database.initialize`.
- PostgreSQL: apply `supabase/migrations/0006_financial_bot.sql` (messages,
  threads, monitor `session_id`, RLS and grants).


## Deletion

```text
DELETE /api/sessions/{session_id}
DELETE /api/bots/{bot_id}/sessions
```

Deleting a chat removes its tasks, task events, plans, messages, thread row and
Bot-session binding. Memories are not deleted by chat deletion. The roster
exposes a per-chat × and a `Clear all chats` action; non-default Bots also expose
`Delete Bot`, which moves chats and memories to the default Bot.
