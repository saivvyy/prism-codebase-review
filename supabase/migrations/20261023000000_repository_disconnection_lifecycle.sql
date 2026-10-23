-- ==============================================================================
-- Migration: 20261023000000_repository_disconnection_lifecycle.sql
-- Description: Repository disconnection lifecycle & engineering memory preservation.
--
-- ARCHITECTURAL INVARIANT:
-- Disconnecting a repository deactivates PRism monitoring for that repository
-- while PRESERVING all institutional engineering memory, review history, PRs,
-- and findings.
--
-- Destructive cascading hard-deletions on pull_requests and engineering_memories
-- are replaced with ON DELETE RESTRICT to prevent accidental memory erasure.
-- ==============================================================================

-- 1. Add disconnection lifecycle columns to public.repositories
ALTER TABLE public.repositories
    ADD COLUMN IF NOT EXISTS is_connected BOOLEAN DEFAULT true NOT NULL,
    ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active' NOT NULL
        CHECK (status IN ('active', 'disconnected')),
    ADD COLUMN IF NOT EXISTS disconnected_at TIMESTAMPTZ;

-- 2. Performance indexes for active repository queries
CREATE INDEX IF NOT EXISTS idx_repositories_user_connected
    ON public.repositories(user_id, is_connected);

CREATE INDEX IF NOT EXISTS idx_repositories_status
    ON public.repositories(status);

-- 3. Replace destructive cascade deletions with ON DELETE RESTRICT
-- Engineering memories MUST NOT be automatically deleted if a repository row is deleted.
ALTER TABLE public.engineering_memories
    DROP CONSTRAINT IF EXISTS engineering_memories_repository_id_fkey;

ALTER TABLE public.engineering_memories
    ADD CONSTRAINT engineering_memories_repository_id_fkey
    FOREIGN KEY (repository_id) REFERENCES public.repositories(id)
    ON DELETE RESTRICT;

-- Pull requests and reviews MUST NOT be automatically deleted if a repository row is deleted.
ALTER TABLE public.pull_requests
    DROP CONSTRAINT IF EXISTS pull_requests_repository_id_fkey;

ALTER TABLE public.pull_requests
    ADD CONSTRAINT pull_requests_repository_id_fkey
    FOREIGN KEY (repository_id) REFERENCES public.repositories(id)
    ON DELETE RESTRICT;

-- ==============================================================================
-- ROLLBACK INSTRUCTIONS:
-- In the event a rollback is required:
-- 1. Restore cascading foreign keys:
--    ALTER TABLE public.engineering_memories
--        DROP CONSTRAINT IF EXISTS engineering_memories_repository_id_fkey;
--    ALTER TABLE public.engineering_memories
--        ADD CONSTRAINT engineering_memories_repository_id_fkey
--        FOREIGN KEY (repository_id) REFERENCES public.repositories(id)
--        ON DELETE CASCADE;
--    ALTER TABLE public.pull_requests
--        DROP CONSTRAINT IF EXISTS pull_requests_repository_id_fkey;
--    ALTER TABLE public.pull_requests
--        ADD CONSTRAINT pull_requests_repository_id_fkey
--        FOREIGN KEY (repository_id) REFERENCES public.repositories(id)
--        ON DELETE CASCADE;
-- 2. Drop lifecycle columns:
--    ALTER TABLE public.repositories
--        DROP COLUMN IF EXISTS is_connected,
--        DROP COLUMN IF EXISTS status,
--        DROP COLUMN IF EXISTS disconnected_at;
-- ==============================================================================
