-- ==============================================================================
-- Migration: 20260929000000_engineering_memory.sql
-- Description: Core engineering_memories table for persistent repository knowledge
-- ==============================================================================

CREATE TABLE IF NOT EXISTS public.engineering_memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id UUID NOT NULL REFERENCES public.repositories(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('architecture_decision', 'rejected_pattern', 'approved_exception', 'review_decision', 'lesson_learned')),
    claim TEXT NOT NULL,
    rationale TEXT,
    scope TEXT,
    status TEXT DEFAULT 'active' NOT NULL CHECK (status IN ('active', 'deprecated', 'superseded')),
    source_type TEXT DEFAULT 'manual' NOT NULL CHECK (source_type IN ('manual', 'pr_review', 'github_issue', 'team_agreement')),
    source_ref TEXT,
    source_url TEXT,
    review_id UUID REFERENCES public.reviews(id) ON DELETE SET NULL,
    pr_number INTEGER,
    confidence TEXT DEFAULT 'supported' NOT NULL CHECK (confidence IN ('supported', 'partially_supported', 'insufficient_evidence')),
    evidence_items JSONB DEFAULT '[]'::jsonb NOT NULL,
    hindsight_document_id TEXT,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Indexes for performance & scoping
CREATE INDEX IF NOT EXISTS idx_engineering_memories_repo_id ON public.engineering_memories(repository_id);
CREATE INDEX IF NOT EXISTS idx_engineering_memories_type ON public.engineering_memories(type);
CREATE INDEX IF NOT EXISTS idx_engineering_memories_status ON public.engineering_memories(status);
CREATE INDEX IF NOT EXISTS idx_engineering_memories_hindsight_doc ON public.engineering_memories(hindsight_document_id);

-- Enable RLS
ALTER TABLE public.engineering_memories ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can access engineering memories for their repositories"
    ON public.engineering_memories FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM public.repositories r
            WHERE r.id = engineering_memories.repository_id
              AND r.user_id = auth.uid()
        )
    );
