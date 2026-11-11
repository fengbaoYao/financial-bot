-- Financial Bot: proactive price monitors and in-app notification inbox.
-- Checks are polled, not streaming; notifications disclose data timestamps.

create table if not exists public.fb_monitors (
  id text primary key,
  owner_id text not null,
  bot_id text not null,
  name text not null,
  symbol text not null,
  condition text not null default 'change_pct',
  threshold double precision not null,
  direction text not null default 'both',
  interval_minutes integer not null default 15,
  cooldown_minutes integer not null default 120,
  status text not null default 'active',
  last_checked_at text,
  last_triggered_at text,
  last_error text,
  created_at text not null,
  updated_at text not null
);

create index if not exists fb_monitors_due on public.fb_monitors(status, last_checked_at);
create index if not exists fb_monitors_owner_bot on public.fb_monitors(owner_id, bot_id, status);

create table if not exists public.fb_notifications (
  id text primary key,
  owner_id text not null,
  bot_id text not null,
  monitor_id text,
  title text not null,
  body text not null,
  severity text not null default 'info',
  status text not null default 'unread',
  task_id text,
  created_at text not null,
  read_at text
);

create index if not exists fb_notifications_owner_bot on public.fb_notifications(owner_id, bot_id, status, created_at);

alter table public.fb_monitors enable row level security;
alter table public.fb_notifications enable row level security;

drop policy if exists fb_monitors_owner_all on public.fb_monitors;
create policy fb_monitors_owner_all on public.fb_monitors
  for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text);

drop policy if exists fb_notifications_owner_all on public.fb_notifications;
create policy fb_notifications_owner_all on public.fb_notifications
  for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text);

grant select, insert, update, delete on public.fb_monitors to authenticated;
grant select, insert, update, delete on public.fb_notifications to authenticated;
