-- Announcement/event monitor deduplication.
alter table public.fb_monitors add column if not exists last_event_ref text;
