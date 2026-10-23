-- ==============================================================================
-- Migration: 20260930000000_persist_historical_context.sql
-- Description: Add historical_context_snapshot to reviews and
--              historical_evidence_snapshot + provenance_status to
--              review_findings so reopened reviews show the identical
--              Historical Context block that was used when the review ran.
-- ==============================================================================

-- Immutable snapshot of all Hindsight memories recalled during this review.
-- Prevents fresh Hindsight recall when reopening an existing review.
ALTER TABLE public.reviews
    ADD COLUMN IF NOT EXISTS historical_context_snapshot JSONB DEFAULT NULL;

-- Rich provenance object for each finding.
-- Replaces the runtime-only historical_evidence field so it survives persistence.
ALTER TABLE public.review_findings
    ADD COLUMN IF NOT EXISTS historical_evidence_snapshot JSONB DEFAULT NULL;

-- Tracks provenance confidence:
--   verified   = source PR number confirmed from Hindsight metadata
--   unverified = memory exists but PR provenance could not be confirmed
--   unavailable = memory present but no source reference at all
--   none       = no historical context for this finding
ALTER TABLE public.review_findings
    ADD COLUMN IF NOT EXISTS provenance_status TEXT DEFAULT 'none'
    CHECK (provenance_status IN ('verified', 'unverified', 'unavailable', 'none'));

CREATE INDEX IF NOT EXISTS idx_reviews_has_historical_context
    ON public.reviews ((historical_context_snapshot IS NOT NULL));

CREATE INDEX IF NOT EXISTS idx_findings_provenance_status
    ON public.review_findings (provenance_status);
