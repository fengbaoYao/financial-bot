-- Financial Bot: first-class Bot profiles and Bot-to-conversation ownership.
-- Existing conversations may remain unclaimed until the default Bot is created;
-- the application claims them once for the owner's default Bot.

create table if not exists public.fb_bots (
  id text primary key,
  owner_id text not null,
  name text not null,
  description text,
  instructions text,
  is_default integer not null default 0,
  approval_threshold integer not null default 4,
  created_at text not null,
  updated_at text not null
);

create index if not exists fb_bots_owner on public.fb_bots(owner_id, is_default, updated_at);

create table if not exists public.fb_bot_sessions (
  bot_id text not null,
  owner_id text not null,
  session_id text not null,
  created_at text not null,
  updated_at text not null,
  primary key (bot_id, session_id)
);

create index if not exists fb_bot_sessions_owner on public.fb_bot_sessions(owner_id, bot_id, updated_at);
create unique index if not exists fb_bot_sessions_one_bot on public.fb_bot_sessions(owner_id, session_id);

alter table public.fb_bots enable row level security;
alter table public.fb_bot_sessions enable row level security;

drop policy if exists fb_bots_owner_all on public.fb_bots;
create policy fb_bots_owner_all on public.fb_bots
  for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text);

drop policy if exists fb_bot_sessions_owner_all on public.fb_bot_sessions;
create policy fb_bot_sessions_owner_all on public.fb_bot_sessions
  for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text);

grant select, insert, update, delete on public.fb_bots to authenticated;
grant select, insert, update, delete on public.fb_bot_sessions to authenticated;

comment on table public.fb_bots is
  'First-class Bot profiles. A Bot owns conversations, instructions, approval budget and (later) proactive monitors.';
