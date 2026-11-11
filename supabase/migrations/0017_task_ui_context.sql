-- Frontend-reported UI state, used only as bounded model context.
alter table public.fb_tasks add column if not exists ui_context_json text;

comment on column public.fb_tasks.ui_context_json is
  'Sanitized UI context (page, last action, task status, view). Never contains HTML or executable instructions.';
