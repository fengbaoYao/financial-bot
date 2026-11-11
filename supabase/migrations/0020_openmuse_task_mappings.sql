-- Durable owner-scoped OpenMuse task mapping.
--
-- Do not add a foreign key to fb_tasks: a local task deletion must not cascade
-- away the upstream identifier and deletion tombstone before remote cleanup is
-- confirmed. The application verifies the owner/task binding when creating a
-- mapping; after deletion, the mapping remains available for recovery.
-- Do not grant this control table to `authenticated` or expose it for public
-- writes. The current Financial Bot PostgreSQL connection assumes that role,
-- so Store mapping operations deliberately fail closed on PostgreSQL until a
-- separate trusted server-only role/connection is designed and provisioned.

create table if not exists public.fb_openmuse_task_mappings (
  owner_id text not null,
  task_id text not null,
  request_id text not null,
  payload_sha256 text not null,
  upstream_task_id text,
  status text not null default 'prepared',
  deletion_status text not null default 'not_requested',
  created_at text not null,
  updated_at text not null,
  primary key (owner_id, task_id),
  unique (owner_id, request_id),
  unique (owner_id, upstream_task_id)
);

create index if not exists fb_openmuse_task_mappings_deletion
  on public.fb_openmuse_task_mappings(owner_id, deletion_status, updated_at);

revoke all on table public.fb_openmuse_task_mappings from public, anon, authenticated;

alter table public.fb_openmuse_task_mappings enable row level security;
drop policy if exists fb_openmuse_task_mappings_owner_all
  on public.fb_openmuse_task_mappings;
create policy fb_openmuse_task_mappings_owner_all
  on public.fb_openmuse_task_mappings for all
  using (owner_id = auth.uid()::text)
  with check (owner_id = auth.uid()::text);

comment on table public.fb_openmuse_task_mappings is
  'Server-only owner binding for OpenMuse tasks; direct client grants are intentionally revoked.';
