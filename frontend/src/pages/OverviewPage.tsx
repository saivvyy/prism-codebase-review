import React, { useEffect } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import {
  RepoIcon,
  GitPullRequestIcon,
  CheckCircleIcon,
  XCircleIcon,
  ClockIcon,
  CommentDiscussionIcon,
  ArrowRightIcon,
  PlusIcon,
} from '@primer/octicons-react'
import { useRepo } from '@/context/useRepo'

export const OverviewPage: React.FC = () => {
  const {
    repositories,
    activeRepo,
    isLoadingRepositories,
    pullRequestsByRepoState,
    fetchPullRequests,
    fetchReviews,
    reviewsByRepo,
    memoryCache,
    fetchRepositoryMemories,
  } = useRepo()
  const navigate = useNavigate()
  const location = useLocation()

  const targetRepo = activeRepo || repositories[0] || null
  const targetRepoId = targetRepo?.id
  const baseKey = targetRepoId ? `${targetRepoId}:all:` : ''

  const reviews = targetRepoId ? reviewsByRepo[targetRepoId] || [] : []
  const recentMemories = (targetRepoId && memoryCache[baseKey]) ? memoryCache[baseKey].slice(0, 6) : []
  const reviewsLoading = targetRepoId ? (reviewsByRepo[targetRepoId] === undefined) : false

  // Fetch reviews and memory if not yet loaded in context
  useEffect(() => {
    if (!targetRepoId) return

    if (!reviewsByRepo[targetRepoId]) {
      fetchReviews(targetRepoId, false).catch(() => {})
    }
    if (baseKey && !memoryCache[baseKey]) {
      fetchRepositoryMemories(targetRepoId, 'all', '', false).catch(() => {})
    }
  }, [targetRepoId, baseKey, reviewsByRepo, memoryCache, fetchReviews, fetchRepositoryMemories])

  useEffect(() => {
    if (targetRepoId && !pullRequestsByRepoState[`${targetRepoId}:open`]) {
      fetchPullRequests(targetRepoId, 'open', false).catch(() => {})
    }
  }, [targetRepoId, fetchPullRequests, pullRequestsByRepoState])

  const openPRs = targetRepoId ? pullRequestsByRepoState[`${targetRepoId}:open`] || [] : []
  const openPRsCount = openPRs.length

  const driftFindingsCount = reviews.reduce((acc, r) => (r.drift_detected ? acc + 1 : acc), 0)
  const conformingCount = reviews.filter((r) => !r.drift_detected && r.status === 'completed').length

  if (isLoadingRepositories && repositories.length === 0) {
    return (
      <div className="p-16 text-center space-y-3">
        <div className="w-6 h-6 border-2 border-[#1f6feb] border-t-transparent rounded-full animate-spin mx-auto" />
        <div className="text-xs text-[#9198a1]">Loading repositories...</div>
      </div>
    )
  }

  if (!isLoadingRepositories && repositories.length === 0) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between pb-1 border-b border-[#30363d]">
          <h1 className="text-xl font-semibold text-[#f0f6fc] tracking-tight">
            Repository Overview
          </h1>
        </div>

        <div className="rounded-md border border-[#30363d] bg-[#0d1117] p-12 text-center space-y-4 max-w-xl mx-auto my-8">
          <div className="w-12 h-12 rounded-full bg-[#161b22] border border-[#30363d] flex items-center justify-center mx-auto text-[#8b949e]">
            <RepoIcon size={24} />
          </div>
          <div className="space-y-1">
            <h2 className="text-base font-semibold text-[#f0f6fc]">
              No repositories connected
            </h2>
            <p className="text-xs text-[#8b949e] max-w-md mx-auto">
              Connect a GitHub repository to start reviewing pull requests.
            </p>
          </div>
          <div className="pt-2">
            <button
              type="button"
              onClick={() => navigate('/repositories/select', { state: { returnTo: location.pathname + location.search + location.hash } })}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-md bg-[#238636] hover:bg-[#2ea043] border border-[rgba(240,246,252,0.1)] text-white text-xs font-semibold shadow-xs transition-colors cursor-pointer"
            >
              <PlusIcon size={14} />
              <span>Connect repository</span>
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {/* 1. Page Header Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-[#30363d]">
        <div>
          <h1 className="text-xl font-semibold text-[#f0f6fc] tracking-tight">
            Repository Overview
          </h1>
          <p className="text-xs text-[#9198a1] mt-0.5">
            Architecture conformance, review activity, and engineering memory.
          </p>
        </div>

        {targetRepo && (
          <div className="flex items-center gap-2">
            <span className="text-xs px-2.5 py-1 rounded-full bg-[#161b22] border border-[#30363d] text-[#8b949e]">
              {targetRepo.full_name}
            </span>
          </div>
        )}
      </div>

      {/* 2. Top Summary Metric Strip */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="p-3.5 rounded-md border border-[#30363d] bg-[#0d1117] space-y-1">
          <div className="text-[11px] font-medium text-[#9198a1] flex items-center justify-between">
            <span>Repository</span>
            <RepoIcon size={14} className="text-[#656c76]" />
          </div>
          <div className="text-base font-semibold text-[#f0f6fc] truncate" title={targetRepo?.full_name || 'None'}>
            {targetRepo?.full_name?.split('/')[1] || targetRepo?.full_name || 'Select repo'}
          </div>
          <div className="text-[11px] text-[#9198a1] truncate">{targetRepo?.default_branch || 'main'} branch</div>
        </div>

        <div className="p-3.5 rounded-md border border-[#30363d] bg-[#0d1117] space-y-1">
          <div className="text-[11px] font-medium text-[#9198a1] flex items-center justify-between">
            <span>Open Pull Requests</span>
            <GitPullRequestIcon size={14} className="text-[#3fb950]" />
          </div>
          <div className="text-2xl font-semibold text-[#f0f6fc]">{openPRsCount}</div>
          <div className="text-[11px] text-[#9198a1]">In active repository</div>
        </div>

        <div className="p-3.5 rounded-md border border-[#30363d] bg-[#0d1117] space-y-1">
          <div className="text-[11px] font-medium text-[#9198a1] flex items-center justify-between">
            <span>Drift Detected</span>
            <XCircleIcon size={14} className="text-[#f85149]" />
          </div>
          <div className="text-2xl font-semibold text-[#f85149]">{driftFindingsCount}</div>
          <div className="text-[11px] text-[#9198a1]">Reviews with drift findings</div>
        </div>

        <div className="p-3.5 rounded-md border border-[#30363d] bg-[#0d1117] space-y-1">
          <div className="text-[11px] font-medium text-[#9198a1] flex items-center justify-between">
            <span>Conforming / Approved</span>
            <CheckCircleIcon size={14} className="text-[#3fb950]" />
          </div>
          <div className="text-2xl font-semibold text-[#3fb950]">{conformingCount}</div>
          <div className="text-[11px] text-[#9198a1]">Passing checks</div>
        </div>
      </div>

      {/* 3. Paired Content Grid: Recent Architecture Reviews & Engineering Precedents */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-stretch">
        {/* Left Column: Recent Architecture Reviews */}
        <div className="flex flex-col h-full space-y-2">
          <div className="h-6 flex items-center justify-between text-xs">
            <span className="font-semibold text-[#f0f6fc] flex items-center gap-1.5">
              <ClockIcon size={14} className="text-[#8b949e]" />
              <span>Recent Architecture Reviews ({reviews.length})</span>
            </span>
            <Link to="/pull-requests" className="text-[#4493f8] hover:underline flex items-center gap-1">
              <span>View PRs</span>
              <ArrowRightIcon size={12} />
            </Link>
          </div>

          <div className="flex-1 flex flex-col rounded-md border border-[#30363d] bg-[#0d1117] overflow-hidden min-h-[300px]">
            {reviewsLoading ? (
              <div className="flex-1 flex items-center justify-center p-6 text-center text-xs text-[#9198a1]">
                Loading reviews...
              </div>
            ) : reviews.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center p-6 text-center space-y-1 text-[#9198a1]">
                <div className="font-medium text-[#f0f6fc]">No reviews executed yet</div>
                <div className="text-[11px] text-[#8b949e]">Open any pull request and run an architecture review.</div>
              </div>
            ) : (
              <div className="divide-y divide-[#21262d] flex-1">
                {reviews.slice(0, 6).map((rev) => (
                  <div key={rev.id} className="p-3.5 flex items-start gap-3 hover:bg-[#161b22]/50 transition-colors">
                    <div className="pt-0.5 shrink-0">
                      {rev.drift_detected ? (
                        <XCircleIcon size={16} className="text-[#f85149]" />
                      ) : (
                        <CheckCircleIcon size={16} className="text-[#3fb950]" />
                      )}
                    </div>

                    <div className="flex-1 min-w-0 space-y-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Link
                          to={`/pull-requests/${rev.repository_id}/${rev.pr_number}`}
                          className="font-semibold text-[#f0f6fc] hover:text-[#4493f8] transition-colors"
                        >
                          PR #{rev.pr_number}
                        </Link>

                        <span
                          className={`px-2 py-0.2 rounded-full text-[10px] font-medium border ${
                            rev.drift_detected
                              ? 'border-[#f85149]/40 bg-[#f85149]/15 text-[#f85149]'
                              : 'border-[#3fb950]/40 bg-[#3fb950]/15 text-[#3fb950]'
                          }`}
                        >
                          {rev.drift_detected ? 'drift detected' : 'conforming'}
                        </span>
                      </div>

                      <p className="text-[#c9d1d9] text-xs line-clamp-2">
                        {rev.summary || 'Architecture conformance review executed.'}
                      </p>

                      <div className="flex items-center gap-2 text-[11px] text-[#9198a1]">
                        <span>{new Date(rev.created_at).toLocaleDateString()}</span>
                        {rev.findings_count > 0 && (
                          <>
                            <span>•</span>
                            <span>{rev.findings_count} findings evaluated</span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right Column: Engineering Precedents */}
        <div className="flex flex-col h-full space-y-2">
          <div className="h-6 flex items-center justify-between text-xs">
            <span className="font-semibold text-[#f0f6fc] flex items-center gap-1.5">
              <CommentDiscussionIcon size={14} className="text-[#8b949e]" />
              <span>Engineering Precedents ({recentMemories.length})</span>
            </span>
            <Link to="/memory" className="text-[#4493f8] hover:underline flex items-center gap-1">
              <span>Explore memory</span>
              <ArrowRightIcon size={12} />
            </Link>
          </div>

          <div className="flex-1 flex flex-col rounded-md border border-[#30363d] bg-[#0d1117] overflow-hidden min-h-[300px]">
            {recentMemories.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center p-6 text-center space-y-1.5 text-[#9198a1]">
                <div className="font-semibold text-[#f0f6fc] text-xs">No engineering precedents yet</div>
                <p className="text-[11px] text-[#8b949e] leading-relaxed max-w-xs mx-auto">
                  Run an architecture review on a pull request to start building repository memory.
                </p>
              </div>
            ) : (
              <div className="divide-y divide-[#21262d] flex-1">
                {recentMemories.map((mem) => (
                  <div key={mem.id} className="p-3.5 hover:bg-[#161b22]/50 transition-colors space-y-1.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-medium border border-[#30363d] bg-[#161b22] text-[#58a6ff] capitalize">
                        {mem.type.replace(/_/g, ' ')}
                      </span>
                      {(() => {
                        const targetPr = mem.pr_number || (mem.evidence_items?.[0]?.match(/#(\d+)/)?.[1])
                        if (targetPr) {
                          return (
                            <span className="px-1.5 py-0.2 rounded border border-[#30363d] bg-[#21262d] text-[10px] text-[#58a6ff] font-mono">
                              PR #{targetPr}
                            </span>
                          )
                        }
                        const firstClean = mem.evidence_items?.find(
                          (e) => e && e !== 'Hindsight memory' && e !== 'Hindsight' && e !== 'Repository Decision'
                        )
                        if (firstClean) {
                          return (
                            <span className="px-1.5 py-0.2 rounded border border-[#30363d] bg-[#21262d] text-[10px] text-[#8b949e] font-mono">
                              {firstClean}
                            </span>
                          )
                        }
                        return null
                      })()}
                    </div>
                    <h4 className="font-semibold text-[#f0f6fc] text-xs line-clamp-2">
                      {mem.claim}
                    </h4>
                    {mem.rationale && (
                      <p className="text-[11px] text-[#8b949e] line-clamp-2">
                        {mem.rationale}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default OverviewPage
