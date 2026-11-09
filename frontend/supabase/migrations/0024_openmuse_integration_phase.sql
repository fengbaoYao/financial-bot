ALTER TABLE public.fb_openmuse_task_mappings
    ADD COLUMN IF NOT EXISTS integration_phase TEXT NOT NULL DEFAULT 'prepared';

ALTER TABLE public.fb_openmuse_task_mappings
    ADD COLUMN IF NOT EXISTS upstream_task_revision BIGINT,
    ADD COLUMN IF NOT EXISTS upstream_projection_sha256 TEXT;

ALTER TABLE public.fb_openmuse_task_mappings
    ADD CONSTRAINT fb_openmuse_task_mappings_integration_phase_check
    CHECK (integration_phase IN (
        'prepared', 'creating', 'create_unknown', 'held', 'provisioning',
        'provision_unknown', 'provisioned', 'resuming', 'resume_unknown',
        'active', 'rejected'
    ));
