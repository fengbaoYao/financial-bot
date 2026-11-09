-- Cross-process claim for one durable OpenMuse deletion mapping.
-- The table remains server-only; these fields are used only by the local
-- SQLite recovery runner until a trusted hosted service role exists.
alter table public.fb_openmuse_task_mappings
  add column if not exists reconcile_lease_id text;

alter table public.fb_openmuse_task_mappings
  add column if not exists reconcile_lease_expires_at text;

comment on column public.fb_openmuse_task_mappings.reconcile_lease_id is
  'Server-only lease token for one cross-process deletion reconciliation attempt.';
comment on column public.fb_openmuse_task_mappings.reconcile_lease_expires_at is
  'Expiry time for the cross-process deletion reconciliation lease.';
