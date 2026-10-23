-- ==============================================================================
-- Migration: 20260928000000_initial_schema.sql
-- Description: Core tables for Engineering Memory platform
-- Tables: profiles, repositories, pull_requests, reviews, review_findings
-- ==============================================================================

-- 1. Profiles Table (Linked to Supabase auth.users)
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    github_username TEXT,
    email TEXT,
    avatar_url TEXT,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- 2. Repositories Table
CREATE TABLE IF NOT EXISTS public.repositories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    github_repo_id BIGINT NOT NULL,
    full_name TEXT NOT NULL,
    default_branch TEXT DEFAULT 'main' NOT NULL,
    webhook_active BOOLEAN DEFAULT false NOT NULL,
    webhook_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT uq_repo_user_repo_id UNIQUE (user_id, github_repo_id)
);

-- 3. Pull Requests Table
CREATE TABLE IF NOT EXISTS public.pull_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id UUID NOT NULL REFERENCES public.repositories(id) ON DELETE CASCADE,
    pr_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    base_branch TEXT DEFAULT 'main' NOT NULL,
    status TEXT DEFAULT 'open' NOT NULL CHECK (status IN ('open', 'merged', 'closed')),
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT uq_repo_pr_number UNIQUE (repository_id, pr_number)
);

-- 4. Reviews Table
CREATE TABLE IF NOT EXISTS public.reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pull_request_id UUID NOT NULL REFERENCES public.pull_requests(id) ON DELETE CASCADE,
    commit_sha TEXT NOT NULL,
    status TEXT DEFAULT 'pending' NOT NULL CHECK (status IN ('pending', 'in_progress', 'completed', 'failed')),
    summary TEXT,
    drift_detected BOOLEAN DEFAULT false NOT NULL,
    exception_approved BOOLEAN DEFAULT false NOT NULL,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL,
    completed_at TIMESTAMPTZ
);

-- 5. Review Findings Table
CREATE TABLE IF NOT EXISTS public.review_findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id UUID NOT NULL REFERENCES public.reviews(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    line_number INTEGER,
    category TEXT NOT NULL CHECK (category IN ('architecture_drift', 'rejected_pattern', 'approved_exception', 'convention_violation', 'note')),
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    historical_pr_ref TEXT,
    hindsight_memory_id TEXT,
    severity TEXT DEFAULT 'warning' NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    created_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- ==============================================================================
-- Indexes
-- ==============================================================================
CREATE INDEX IF NOT EXISTS idx_repositories_user_id ON public.repositories(user_id);
CREATE INDEX IF NOT EXISTS idx_repositories_full_name ON public.repositories(full_name);
CREATE INDEX IF NOT EXISTS idx_pull_requests_repo_id ON public.pull_requests(repository_id);
CREATE INDEX IF NOT EXISTS idx_pull_requests_status ON public.pull_requests(status);
CREATE INDEX IF NOT EXISTS idx_reviews_pull_request_id ON public.reviews(pull_request_id);
CREATE INDEX IF NOT EXISTS idx_reviews_status ON public.reviews(status);
CREATE INDEX IF NOT EXISTS idx_review_findings_review_id ON public.review_findings(review_id);
CREATE INDEX IF NOT EXISTS idx_review_findings_category ON public.review_findings(category);
CREATE INDEX IF NOT EXISTS idx_review_findings_hindsight_id ON public.review_findings(hindsight_memory_id);

-- ==============================================================================
-- Row Level Security (RLS) Policies
-- ==============================================================================
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.repositories ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pull_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.review_findings ENABLE ROW LEVEL SECURITY;

-- Profiles: users read & update their own profile
CREATE POLICY "Users can view own profile"
    ON public.profiles FOR SELECT
    USING (auth.uid() = id);

CREATE POLICY "Users can update own profile"
    ON public.profiles FOR UPDATE
    USING (auth.uid() = id);

-- Repositories: users manage repositories they own
CREATE POLICY "Users can manage their repositories"
    ON public.repositories FOR ALL
    USING (auth.uid() = user_id);

-- Pull Requests: users access PRs belonging to their repositories
CREATE POLICY "Users can access repository pull requests"
    ON public.pull_requests FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM public.repositories r
            WHERE r.id = pull_requests.repository_id
              AND r.user_id = auth.uid()
        )
    );

-- Reviews: users access reviews for their pull requests
CREATE POLICY "Users can access pull request reviews"
    ON public.reviews FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM public.pull_requests pr
            JOIN public.repositories r ON r.id = pr.repository_id
            WHERE pr.id = reviews.pull_request_id
              AND r.user_id = auth.uid()
        )
    );

-- Review Findings: users access findings for their reviews
CREATE POLICY "Users can access review findings"
    ON public.review_findings FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM public.reviews rev
            JOIN public.pull_requests pr ON pr.id = rev.pull_request_id
            JOIN public.repositories r ON r.id = pr.repository_id
            WHERE rev.id = review_findings.review_id
              AND r.user_id = auth.uid()
        )
    );
