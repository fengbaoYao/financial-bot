-- Financial Bot task contract: structured waiting, dropped terminal state,
-- authorization snapshot, disposition log and platform pause.
--
-- Additive and idempotent. The application store creates the same shapes for
-- SQLite; this migration keeps hosted PostgreSQL in step and extends RLS.

alter table public.fb_tasks add column if not exists waiting_reason text;
alter table public.fb_tasks add column if not exists attention_json text;
alter table public.fb_tasks add column if not exists authorization_json text;
alter table public.fb_tasks add column if not exists continued_from_task_id text;

create table if not exists public.fb_dispositions (
  id text primary key,
  owner_id text not null,
  task_id text,
  session_id text,
  input_ref text not null,
  disposition text not null,
  reason_code text,
  reason_display text,
  created_at text not null,
  unique (owner_id, input_ref)
);

create index if not exists fb_dispositions_owner_session
  on public.fb_dispositions(owner_id, session_id, created_at);

alter table public.fb_dispositions enable row level security;

drop policy if exists fb_dispositions_owner_all on public.fb_dispositions;
create policy fb_dispositions_owner_all on public.fb_dispositions
  for all using (owner_id = auth.uid()::text)
  with check (owner_id = auth.uid()::text);

grant select, insert, update, delete on public.fb_dispositions to authenticated;

-- Platform pause is operations data, not user data. RLS is enabled with no
-- policy and the authenticated role receives no grant, so only the service
-- role (which bypasses RLS) can read or change it.
create table if not exists public.fb_platform_state (
  id text primary key,
  paused integer not null default 0,
  paused_by text,
  pause_reason text,
  paused_at text,
  updated_at text not null
);

alter table public.fb_platform_state enable row level security;
