-- Persistent task plans: the authoritative step stack, independent of chat history.
create table if not exists public.fb_task_plans (
  task_id text primary key,
  owner_id text not null,
  bot_id text,
  session_id text,
  original_query text not null,
  status text not null,
  source text not null default 'template',
  steps_json text not null,
  termination_condition text,
  created_at text not null,
  updated_at text not null
);

create index if not exists fb_task_plans_owner on public.fb_task_plans(owner_id, updated_at);

alter table public.fb_task_plans enable row level security;
drop policy if exists fb_task_plans_owner_all on public.fb_task_plans;
create policy fb_task_plans_owner_all on public.fb_task_plans
  for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text);
grant select, insert, update, delete on public.fb_task_plans to authenticated;

comment on table public.fb_task_plans is
  'Authoritative per-task step list. The agent can read it but cannot mark steps complete; the server validates tool evidence.';
