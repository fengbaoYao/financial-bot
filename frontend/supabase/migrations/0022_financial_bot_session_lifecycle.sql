-- Persist session deletion fences so a stale request cannot recreate a
-- deleted conversation or race task creation while a delete is in progress.
create table if not exists public.fb_session_lifecycle (
  owner_id text not null,
  session_id text not null,
  status text not null default 'active'
    check (status in ('active', 'deleting', 'deleted')),
  updated_at text not null,
  primary key (owner_id, session_id)
);

alter table public.fb_session_lifecycle enable row level security;
revoke all on table public.fb_session_lifecycle from public, anon, authenticated;

create or replace function public.fb_ensure_session_lifecycle(
  p_owner_id text,
  p_session_id text
) returns text
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  lifecycle_status text;
begin
  if auth.uid() is null or auth.uid()::text <> p_owner_id then
    raise exception 'session owner mismatch' using errcode = '42501';
  end if;
  insert into public.fb_session_lifecycle(owner_id, session_id, status, updated_at)
  values (p_owner_id, p_session_id, 'active', now()::text)
  on conflict (owner_id, session_id) do nothing;
  select status into lifecycle_status
    from public.fb_session_lifecycle
   where owner_id = p_owner_id and session_id = p_session_id;
  return lifecycle_status;
end;
$$;

create or replace function public.fb_set_session_lifecycle(
  p_owner_id text,
  p_session_id text,
  p_status text
) returns text
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  lifecycle_status text;
begin
  if auth.uid() is null or auth.uid()::text <> p_owner_id then
    raise exception 'session owner mismatch' using errcode = '42501';
  end if;
  if p_status not in ('deleting', 'deleted') then
    raise exception 'invalid session lifecycle status' using errcode = '22023';
  end if;
  update public.fb_session_lifecycle
     set status = p_status, updated_at = now()::text
   where owner_id = p_owner_id
     and session_id = p_session_id
     and status in ('active', 'deleting', p_status)
   returning status into lifecycle_status;
  if lifecycle_status is null then
    raise exception 'session lifecycle transition rejected' using errcode = '55000';
  end if;
  return lifecycle_status;
end;
$$;

create or replace function public.fb_mark_session_openmuse_delete_pending(
  p_owner_id text,
  p_session_id text
) returns table(task_id text)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  if auth.uid() is null or auth.uid()::text <> p_owner_id then
    raise exception 'session owner mismatch' using errcode = '42501';
  end if;
  return query
  update public.fb_openmuse_task_mappings as mapping
     set deletion_status = 'pending', updated_at = now()::text
    from public.fb_tasks as task
   where task.owner_id = p_owner_id
     and task.session_id = p_session_id
     and mapping.owner_id = task.owner_id
     and mapping.task_id = task.id
     and mapping.deletion_status <> 'complete'
  returning mapping.task_id;
end;
$$;

revoke all on function public.fb_ensure_session_lifecycle(text, text) from public, anon;
revoke all on function public.fb_set_session_lifecycle(text, text, text) from public, anon;
revoke all on function public.fb_mark_session_openmuse_delete_pending(text, text) from public, anon;
grant execute on function public.fb_ensure_session_lifecycle(text, text) to authenticated;
grant execute on function public.fb_set_session_lifecycle(text, text, text) to authenticated;
grant execute on function public.fb_mark_session_openmuse_delete_pending(text, text) to authenticated;
