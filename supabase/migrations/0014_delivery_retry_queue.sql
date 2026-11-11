-- Automatic retry scheduling for failed external deliveries.
alter table public.fb_notification_deliveries add column if not exists next_retry_at text;
