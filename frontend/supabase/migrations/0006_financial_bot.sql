-- Financial Bot: owner-scoped tasks, events, long-term memory and deletion jobs.
--
-- Every table carries owner_id and enforces auth.uid() row-level security. The
-- application also filters by owner in every statement; RLS is the independent
-- second boundary for a hosted PostgreSQL/Supabase deployment.
--
-- The bot runs on SQLite only in the explicitly labelled local single-user mode.
-- Multi-user trial remains gated until this migration is applied and the RLS
-- integration test has been executed against a real PostgreSQL instance.

create extension if not exists pgcrypto;

create table if not exists public.fb_tasks (
  id text primary key,
  owner_id text not null,
  session_id text,
  idempotency_key text not null,
  query text not null,
  language text not null default 'auto',
  research_mode text not null default 'quick',
  status text not null,
  status_reason text,
  result_json text,
  error_code text,
  error_message text,
  input_tokens integer not null default 0,
  output_tokens integer not null default 0,
  cost_usd double precision not null default 0,
  created_at text not null,
  updated_at text not null,
  started_at text,
  finished_at text,
  cancel_requested integer not null default 0,
  sequence integer not null default 0,
  unique (owner_id, idempotency_key)
);

create index if not exists fb_tasks_owner_session on public.fb_tasks(owner_id, session_id, created_at);
create index if not exists fb_tasks_owner_status on public.fb_tasks(owner_id, status);

create table if not exists public.fb_task_events (
  event_id text primary key,
  task_id text not null,
  owner_id text not null,
  sequence integer not null,
  occurred_at text not null,
  kind text not null,
  step_id text,
  status text not null,
  display_summary text not null,
  source_refs_json text not null default '[]',
  unique (task_id, sequence)
);

create index if not exists fb_task_events_owner on public.fb_task_events(owner_id, task_id, sequence);

create table if not exists public.fb_messages (
  id text primary key,
  owner_id text not null,
  bot_id text,
  session_id text not null,
  role text not null,
  kind text not null default 'message',
  content text not null,
  task_id text,
  metadata_json text,
  sequence integer not null,
  created_at text not null,
  unique (owner_id, session_id, sequence)
);

create index if not exists fb_messages_owner_session on public.fb_messages(owner_id, session_id, sequence);

create table if not exists public.fb_threads (
  session_id text primary key,
  owner_id text not null,
  bot_id text not null,
  parent_session_id text,
  title text not null,
  kind text not null default 'side',
  folder text,
  approval_mode text not null default 'ask',
  research_mode text not null default 'quick',
  pinned boolean not null default false,
  archived boolean not null default false,
  unread integer not null default 0,
  last_read_at text,
  workflow_json text,
  current_task_id text,
  created_at text not null,
  updated_at text not null
);

create index if not exists fb_threads_owner_bot on public.fb_threads(owner_id, bot_id, updated_at);

create table if not exists public.fb_memories (
  id text primary key,
  owner_id text not null,
  kind text not null,
  text text not null,
  source_text text,
  source_ref text,
  status text not null,
  version integer not null default 1,
  text_hash text not null,
  created_at text not null,
  updated_at text not null,
  confirmed_at text,
  deletion_state text not null default 'active',
  unique (owner_id, kind, text_hash)
);

create index if not exists fb_memories_owner_kind on public.fb_memories(owner_id, kind, status);

create table if not exists public.fb_memory_versions (
  id text primary key,
  memory_id text not null,
  owner_id text not null,
  version integer not null,
  text text not null,
  source_text text,
  reason text,
  created_at text not null,
  unique (memory_id, version)
);

create index if not exists fb_memory_versions_owner on public.fb_memory_versions(owner_id, memory_id, version);

create table if not exists public.fb_memory_candidates (
  id text primary key,
  owner_id text not null,
  task_id text,
  source_message_ref text,
  proposed_text text not null,
  rationale text,
  source_text text,
  status text not null,
  version integer not null default 1,
  created_at text not null,
  decided_at text,
  memory_id text,
  idempotency_key text,
  unique (owner_id, idempotency_key)
);

create index if not exists fb_memory_candidates_owner on public.fb_memory_candidates(owner_id, status, created_at);

create table if not exists public.fb_memory_index (
  owner_id text not null,
  memory_id text not null,
  term text not null,
  primary key (owner_id, memory_id, term)
);

create index if not exists fb_memory_index_term on public.fb_memory_index(owner_id, term);

create table if not exists public.fb_deletion_jobs (
  id text primary key,
  owner_id text not null,
  scope text not null,
  target_ids_json text not null default '[]',
  status text not null,
  layers_json text not null default '{}',
  detail text,
  request_id text not null,
  requested_at text not null,
  updated_at text not null,
  completed_at text,
  unique (owner_id, request_id)
);

create index if not exists fb_deletion_jobs_owner on public.fb_deletion_jobs(owner_id, requested_at);

create table if not exists public.fb_audit_log (
  id text primary key,
  owner_id text not null,
  action text not null,
  object_id text not null,
  detail text,
  created_at text not null
);

create index if not exists fb_audit_log_owner on public.fb_audit_log(owner_id, created_at);

-- ---------------------------------------------------------------- RLS setup
alter table public.fb_tasks enable row level security;
alter table public.fb_task_events enable row level security;
alter table public.fb_memories enable row level security;
alter table public.fb_memory_versions enable row level security;
alter table public.fb_memory_candidates enable row level security;
alter table public.fb_memory_index enable row level security;
alter table public.fb_deletion_jobs enable row level security;
alter table public.fb_audit_log enable row level security;

do $$
declare
  tbl text;
begin
  foreach tbl in array array[
    'fb_tasks', 'fb_task_events', 'fb_messages', 'fb_threads', 'fb_memories', 'fb_memory_versions',
    'fb_memory_candidates', 'fb_memory_index', 'fb_deletion_jobs'
  ]
  loop
    execute format('drop policy if exists %I on public.%I', tbl || '_owner_all', tbl);
    execute format(
      'create policy %I on public.%I for all using (owner_id = auth.uid()::text) with check (owner_id = auth.uid()::text)',
      tbl || '_owner_all', tbl
    );
  end loop;
end
$$;

drop policy if exists fb_audit_log_owner_select on public.fb_audit_log;
create policy fb_audit_log_owner_select on public.fb_audit_log
  for select using (owner_id = auth.uid()::text);

drop policy if exists fb_audit_log_owner_insert on public.fb_audit_log;
create policy fb_audit_log_owner_insert on public.fb_audit_log
  for insert with check (owner_id = auth.uid()::text);

grant select, insert, update, delete on public.fb_tasks to authenticated;
grant select, insert, update, delete on public.fb_task_events to authenticated;
grant select, insert, update, delete on public.fb_messages to authenticated;
grant select, insert, update, delete on public.fb_threads to authenticated;
grant select, insert, update, delete on public.fb_memories to authenticated;
grant select, insert, update, delete on public.fb_memory_versions to authenticated;
grant select, insert, update, delete on public.fb_memory_candidates to authenticated;
grant select, insert, update, delete on public.fb_memory_index to authenticated;
grant select, insert, update, delete on public.fb_deletion_jobs to authenticated;
grant select, insert on public.fb_audit_log to authenticated;

comment on table public.fb_memories is
  'Financial Bot long-term memory. Only confirmed rows are ever injected into model context; pending candidates live in fb_memory_candidates.';
comment on table public.fb_deletion_jobs is
  'Per-layer deletion evidence. A job is blocked rather than completed when provider retention or backups cannot be verified.';
