-- Store only stable, credential-free retry reason codes for pending deletes.
ALTER TABLE public.fb_openmuse_task_mappings
    ADD COLUMN IF NOT EXISTS deletion_error text NOT NULL DEFAULT '';
