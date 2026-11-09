-- Per-Bot notification digest mode and delivery audit.
alter table public.fb_bots add column if not exists digest_mode text not null default 'immediate';
alter table public.fb_bots add column if not exists digest_hour text not null default '18:00';
alter table public.fb_bots add column if not exists last_digest_date text;

create table if not exists public.fb_notification_deliveries (
  id text primary key,
  owner_id text not null,
  bot_id text not null,
  notification_id text,
  channel text not null,
  status text not null,
  error text,
  attempts integer not null default 1,
  created_at text not null,
  sent_at text
);

create index if not exists fb_notification_deliveries_owner on public.fb_notification_deliveries(owner_id, bot_id, created_at);

alter table public.fb_notification_deliveries enable row level security;
drop policy if exists fb_notification_deliveries_owner_all on public.fb_notification_deliveries;
create policy fb_notification_deliveries_owner_all on public.fb_notification_deliveries
  for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text);
grant select, insert, update, delete on public.fb_notification_deliveries to authenticated;
