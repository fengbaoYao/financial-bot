-- Per-Bot notification and monitoring preferences.
alter table public.fb_bots add column if not exists market_hours_only integer not null default 1;
alter table public.fb_bots add column if not exists max_alerts_per_day integer not null default 20;
alter table public.fb_bots add column if not exists quiet_hours_start text;
alter table public.fb_bots add column if not exists quiet_hours_end text;
alter table public.fb_bots add column if not exists webhook_url text;
alter table public.fb_bots add column if not exists notification_email text;
alter table public.fb_bots add column if not exists report_style text not null default 'brief';
