-- Store the outbound payload so a failed delivery can be retried.
alter table public.fb_notification_deliveries add column if not exists payload_json text;
