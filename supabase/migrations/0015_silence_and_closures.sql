-- Per-monitor mute window and owner-scoped ad-hoc market closures.
alter table public.fb_monitors add column if not exists muted_until text;

create table if not exists public.fb_market_closures (
  id text primary key,
  owner_id text not null,
  market text not null,
  closure_date text not null,
  reason text,
  created_at text not null,
  unique (owner_id, market, closure_date)
);

create index if not exists fb_market_closures_owner on public.fb_market_closures(owner_id, closure_date);

alter table public.fb_market_closures enable row level security;
drop policy if exists fb_market_closures_owner_all on public.fb_market_closures;
create policy fb_market_closures_owner_all on public.fb_market_closures
  for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text);
grant select, insert, update, delete on public.fb_market_closures to authenticated;
