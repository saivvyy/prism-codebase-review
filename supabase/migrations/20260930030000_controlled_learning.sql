-- ==============================================================================
-- Migration: 20260930030000_controlled_learning.sql
-- Description: Controlled learning enhancements for Phase 4:
--              Hindsight synchronization tracking, observation frequency,
--              and temporal observation timestamps.
-- ==============================================================================

-- 1. Alter engineering_memories table for controlled learning metadata
ALTER TABLE public.engineering_memories
    ADD COLUMN IF NOT EXISTS sync_state TEXT DEFAULT 'SYNCED',
    ADD COLUMN IF NOT EXISTS observation_count INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS first_observed_at TIMESTAMPTZ DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS last_observed_at TIMESTAMPTZ DEFAULT NOW();

-- 2. Add constraint on sync_state
ALTER TABLE public.engineering_memories
    DROP CONSTRAINT IF EXISTS engineering_memories_sync_state_check;

ALTER TABLE public.engineering_memories
    ADD CONSTRAINT engineering_memories_sync_state_check
    CHECK (sync_state IN (
        'SYNCED',
        'UNAVAILABLE',
        'FAILED'
    ));

-- Backfill first_observed_at / last_observed_at from created_at
UPDATE public.engineering_memories
SET first_observed_at = created_at
WHERE first_observed_at IS NULL AND created_at IS NOT NULL;

UPDATE public.engineering_memories
SET last_observed_at = updated_at
WHERE last_observed_at IS NULL AND updated_at IS NOT NULL;
