-- Bot Studio: editable prompt template + structured prompt configuration.
alter table public.fb_bots add column if not exists prompt_template text;
alter table public.fb_bots add column if not exists prompt_config text;
alter table public.fb_bots add column if not exists prompt_version integer not null default 1;

comment on column public.fb_bots.prompt_template is
  'User-editable Bot system prompt template. Server renders it with the same variables used by the preview endpoint.';
comment on column public.fb_bots.prompt_config is
  'JSON prompt configuration: role, markets, output style, capability toggles, memory/routine flags.';
