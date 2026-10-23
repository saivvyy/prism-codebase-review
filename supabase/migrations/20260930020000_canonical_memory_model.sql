-- ==============================================================================
-- Migration: 20260930020000_canonical_memory_model.sql
-- Description: Canonical memory domain model with structured provenance,
--              authority semantics, and verdict enhancements.
-- ==============================================================================

-- 1. Alter engineering_memories table for canonical provenance and authority
ALTER TABLE public.engineering_memories
    ADD COLUMN IF NOT EXISTS statement TEXT,
    ADD COLUMN IF NOT EXISTS authority TEXT DEFAULT 'ENFORCEABLE',
    ADD COLUMN IF NOT EXISTS repository_full_name TEXT,
    ADD COLUMN IF NOT EXISTS source_commit_sha TEXT,
    ADD COLUMN IF NOT EXISTS source_pr_number INTEGER,
    ADD COLUMN IF NOT EXISTS identity_hash TEXT,
    ADD COLUMN IF NOT EXISTS supersedes UUID REFERENCES public.engineering_memories(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS superseded_by UUID REFERENCES public.engineering_memories(id) ON DELETE SET NULL;

-- Backfill statement from claim where statement is null
UPDATE public.engineering_memories
SET statement = claim
WHERE statement IS NULL AND claim IS NOT NULL;

-- Backfill source_pr_number from pr_number where source_pr_number is null
UPDATE public.engineering_memories
SET source_pr_number = pr_number
WHERE source_pr_number IS NULL AND pr_number IS NOT NULL;

-- Backfill legacy memory types to canonical knowledge types
UPDATE public.engineering_memories
SET type = CASE
    WHEN type IN ('architecture_decision', 'architectural_decision') THEN 'DOCUMENTED_DECISION'
    WHEN type IN ('approved_exception') THEN 'APPROVED_EXCEPTION'
    WHEN type IN ('rejected_pattern', 'architecture_drift') THEN 'REJECTED_PATTERN'
    WHEN type IN ('observed_convention', 'convention', 'convention_violation', 'lesson_learned', 'review_decision') THEN 'OBSERVED_CONVENTION'
    WHEN type IN ('review_observation', 'note') THEN 'REVIEW_OBSERVATION'
    ELSE 'DOCUMENTED_DECISION'
END;

-- Backfill legacy statuses to canonical uppercase statuses
UPDATE public.engineering_memories
SET status = CASE
    WHEN status IN ('active', 'ACTIVE') THEN 'ACTIVE'
    WHEN status IN ('superseded', 'SUPERSEDED') THEN 'SUPERSEDED'
    WHEN status IN ('revoked', 'deprecated', 'deleted', 'REVOKED') THEN 'REVOKED'
    ELSE 'ACTIVE'
END;

-- Update constraint on type to strictly canonical types
ALTER TABLE public.engineering_memories
    DROP CONSTRAINT IF EXISTS engineering_memories_type_check;

ALTER TABLE public.engineering_memories
    ADD CONSTRAINT engineering_memories_type_check
    CHECK (type IN (
        'DOCUMENTED_DECISION',
        'APPROVED_EXCEPTION',
        'REJECTED_PATTERN',
        'OBSERVED_CONVENTION',
        'REVIEW_OBSERVATION'
    ));

-- Update constraint on status to canonical statuses (ACTIVE, SUPERSEDED, REVOKED)
ALTER TABLE public.engineering_memories
    DROP CONSTRAINT IF EXISTS engineering_memories_status_check;

ALTER TABLE public.engineering_memories
    ADD CONSTRAINT engineering_memories_status_check
    CHECK (status IN (
        'ACTIVE',
        'SUPERSEDED',
        'REVOKED'
    ));

-- Add constraint on authority
ALTER TABLE public.engineering_memories
    DROP CONSTRAINT IF EXISTS engineering_memories_authority_check;

ALTER TABLE public.engineering_memories
    ADD CONSTRAINT engineering_memories_authority_check
    CHECK (authority IN (
        'ENFORCEABLE',
        'EXCEPTION',
        'REJECTED_PATTERN',
        'CONTEXTUAL',
        'NON_AUTHORITATIVE'
    ));

-- 2. Alter reviews table for explicit verdict state
ALTER TABLE public.reviews
    ADD COLUMN IF NOT EXISTS verdict TEXT DEFAULT 'NO_PRECEDENT';

ALTER TABLE public.reviews
    DROP CONSTRAINT IF EXISTS reviews_verdict_check;

ALTER TABLE public.reviews
    ADD CONSTRAINT reviews_verdict_check
    CHECK (verdict IN (
        'NO_PRECEDENT',
        'CONFORMING',
        'DRIFT',
        'APPROVED_EXCEPTION',
        'CONFLICTING_GUIDANCE'
    ));

-- Backfill reviews verdict based on drift_detected and exception_approved
UPDATE public.reviews
SET verdict = CASE
    WHEN drift_detected = true THEN 'DRIFT'
    WHEN exception_approved = true THEN 'APPROVED_EXCEPTION'
    WHEN historical_context_snapshot IS NOT NULL AND jsonb_array_length(historical_context_snapshot) > 0 THEN 'CONFORMING'
    ELSE 'NO_PRECEDENT'
END
WHERE verdict IS NULL OR verdict = 'NO_PRECEDENT' OR verdict = 'DRIFT_DETECTED';

-- Indexes for canonical queries
CREATE INDEX IF NOT EXISTS idx_engineering_memories_authority ON public.engineering_memories(authority);
CREATE INDEX IF NOT EXISTS idx_engineering_memories_supersedes ON public.engineering_memories(supersedes);
CREATE INDEX IF NOT EXISTS idx_engineering_memories_superseded_by ON public.engineering_memories(superseded_by);
CREATE INDEX IF NOT EXISTS idx_engineering_memories_source_pr_number ON public.engineering_memories(source_pr_number);
CREATE UNIQUE INDEX IF NOT EXISTS idx_engineering_memories_repo_identity
    ON public.engineering_memories(repository_id, identity_hash)
    WHERE identity_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_reviews_verdict ON public.reviews(verdict);
