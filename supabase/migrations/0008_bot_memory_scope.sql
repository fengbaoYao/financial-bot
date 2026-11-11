-- Scope long-term memories and memory candidates to a Bot.
-- Existing rows keep bot_id NULL; the default Bot includes them as legacy
-- owner-level memories, while custom Bots only see their own rows.

alter table public.fb_memories add column if not exists bot_id text;
alter table public.fb_memory_candidates add column if not exists bot_id text;

create index if not exists fb_memories_owner_bot on public.fb_memories(owner_id, bot_id, status);
create index if not exists fb_memory_candidates_owner_bot on public.fb_memory_candidates(owner_id, bot_id, status);
