-- Keep one local status event per upstream task revision. NULL revisions on
-- preexisting and ordinary Financial Bot events remain unconstrained.
ALTER TABLE public.fb_task_events
    ADD COLUMN IF NOT EXISTS openmuse_revision BIGINT;

CREATE UNIQUE INDEX IF NOT EXISTS fb_task_events_openmuse_revision
    ON public.fb_task_events(task_id, openmuse_revision);
