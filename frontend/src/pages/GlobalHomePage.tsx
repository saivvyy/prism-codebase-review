import React, { useEffect, useState, useMemo } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import {
  RepoIcon,
  GitPullRequestIcon,
  CheckCircleIcon,
  XCircleIcon,
  PlusIcon,
  ClockIcon,
  ArrowRightIcon,
  GitBranchIcon,
} from '@primer/octicons-react'
import { useRepo } from '@/context/useRepo'
import type { RecentReview } from '@/types'

export const GlobalHomePage: React.FC = () => {
  const { repositories, activeRepo, setActiveRepo, reviewsByRepo, fetchReviews } = useRepo()
  const navigate = useNavigate()
  const location = useLocation()
  const [crossRepoReviews, setCrossRepoReviews] = useState<RecentReview[]>([])
  const [isLoadingReviews, setIsLoadingReviews] = useState(repositories.length > 0)

  // Fetch reviews across repositories for cross-repository activity
  useEffect(() => {
    let isMounted = true
    if (repositories.length === 0) return

    Promise.all(repositories.map((repo) => fetchReviews(repo.id, false).catch(() => [])))
      .then((results) => {
        if (!isMounted) return
        const flattened = results.flat()
        flattened.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
        setCrossRepoReviews(flattened)
        setIsLoadingReviews(false)
      })
      .catch(() => {
        if (!isMounted) return
        setIsLoadingReviews(false)
      })

    return () => {
      isMounted = false
    }
  }, [repositories, fetchReviews])

  // Aggregate metrics
  const totalRepos = repositories.length
  const allReviews = useMemo(() => {
    const map = new Map<string, RecentReview>()
    for (const r of crossRepoReviews) {
      map.set(r.id, r)
    }
    for (const reviewsList of Object.values(reviewsByRepo)) {
      for (const r of reviewsList) {
        map.set(r.id, r)
      }
    }
    const combined = Array.from(map.values())
    combined.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    return combined
  }, [crossRepoReviews, reviewsByRepo])

  const driftReviews = useMemo(() => allReviews.filter((r) => r.drift_detected), [allReviews])
  const conformingReviews = useMemo(() => allReviews.filter((r) => !r.drift_detected && r.status === 'completed'), [allReviews])

  return (
    <div className="space-y-5">
      {/* 1. Global Home Header Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-[#30363d]">
        <div>
          <h1 className="text-xl font-semibold text-[#f0f6fc] tracking-tight">
            Workspace Overview
          </h1>
          <p className="text-xs text-[#9198a1] mt-0.5">
            Cross-repository engineering intelligence, connected codebases, and recent architecture review activity.
          </p>
        </div>

        <button
          type="button"
          onClick={() => navigate('/repositories/select', { state: { returnTo: location.pathname + location.search + location.hash } })}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[#238636] hover:bg-[#2ea043] border border-[rgba(240,246,252,0.1)] text-white text-xs font-semibold shadow-xs transition-colors cursor-pointer self-start sm:self-auto"
        >
          <PlusIcon size={14} />
          <span>Connect repository</span>
        </button>
      </div>

      {/* 2. Global Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="p-3.5 rounded-md border border-[#30363d] bg-[#0d1117] space-y-1">
          <div className="text-[11px] font-medium text-[#9198a1] flex items-center justify-between">
            <span>Connected Repositories</span>
            <RepoIcon size={14} className="text-[#656c76]" />
          </div>
          <div className="text-2xl font-semibold text-[#f0f6fc]">{totalRepos}</div>
          <div className="text-[11px] text-[#9198a1]">Tracked in workspace</div>
        </div>

        <div className="p-3.5 rounded-md border border-[#30363d] bg-[#0d1117] space-y-1">
          <div className="text-[11px] font-medium text-[#9198a1] flex items-center justify-between">
            <span>Recent Architecture Drift</span>
            <XCircleIcon size={14} className="text-[#f85149]" />
          </div>
          <div className="text-2xl font-semibold text-[#f85149]">{driftReviews.length}</div>
          <div className="text-[11px] text-[#9198a1]">Reviews with drift findings</div>
        </div>

        <div className="p-3.5 rounded-md border border-[#30363d] bg-[#0d1117] space-y-1">
          <div className="text-[11px] font-medium text-[#9198a1] flex items-center justify-between">
            <span>Conforming Reviews</span>
            <CheckCircleIcon size={14} className="text-[#3fb950]" />
          </div>
          <div className="text-2xl font-semibold text-[#3fb950]">{conformingReviews.length}</div>
          <div className="text-[11px] text-[#9198a1]">Passing architecture checks</div>
        </div>
      </div>

      {/* 3. Paired Section: Connected Repositories & Recent Activity */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-stretch">
        {/* Connected Repositories Card */}
        <div className="flex flex-col h-full space-y-2">
          <div className="h-6 flex items-center justify-between text-xs">
            <span className="font-semibold text-[#f0f6fc] flex items-center gap-1.5">
              <RepoIcon size={14} className="text-[#8b949e]" />
              <span>Connected Repositories ({repositories.length})</span>
            </span>
            <Link to="/repositories/select" state={{ returnTo: location.pathname + location.search + location.hash }} className="text-[#4493f8] hover:underline flex items-center gap-1">
              <span>Manage</span>
              <ArrowRightIcon size={12} />
            </Link>
          </div>

          <div className="flex-1 flex flex-col rounded-md border border-[#30363d] bg-[#0d1117] overflow-hidden min-h-[220px]">
            {repositories.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center p-8 text-center space-y-3 text-[#9198a1]">
                <div className="w-10 h-10 rounded-full bg-[#161b22] border border-[#30363d] flex items-center justify-center mx-auto text-[#8b949e]">
                  <RepoIcon size={20} />
                </div>
                <div className="space-y-1">
                  <div className="font-semibold text-sm text-[#f0f6fc]">No repositories connected</div>
                  <div className="text-xs text-[#8b949e]">Connect a GitHub repository to start reviewing pull requests.</div>
                </div>
                <button
                  type="button"
                  onClick={() => navigate('/repositories/select', { state: { returnTo: location.pathname + location.search + location.hash } })}
                  className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-md bg-[#238636] hover:bg-[#2ea043] border border-[rgba(240,246,252,0.1)] text-white text-xs font-semibold shadow-xs transition-colors cursor-pointer"
                >
                  <PlusIcon size={14} />
                  <span>Connect repository</span>
                </button>
              </div>
            ) : (
              <div className="divide-y divide-[#21262d] flex-1">
                {repositories.map((repo) => {
                  const isActive = activeRepo?.id === repo.id
                  return (
                    <div
                      key={repo.id}
                      className="p-3.5 flex items-center justify-between gap-3 hover:bg-[#161b22]/50 transition-colors"
                    >
                      <div className="min-w-0 space-y-1">
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => {
                              setActiveRepo(repo)
                              navigate('/overview')
                            }}
                            className="font-semibold text-xs text-[#58a6ff] hover:underline cursor-pointer truncate text-left"
                          >
                            {repo.full_name}
                          </button>
                          {isActive && (
                            <span className="px-1.5 py-0.2 rounded-full text-[10px] font-medium border border-[#3fb950]/30 bg-[#3fb950]/15 text-[#3fb950]">
                              active
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-3 text-[11px] text-[#8b949e]">
                          <span className="flex items-center gap-1">
                            <GitBranchIcon size={12} />
                            <span>{repo.default_branch || 'main'}</span>
                          </span>
                        </div>
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          type="button"
                          onClick={() => {
                            setActiveRepo(repo)
                            navigate('/overview')
                          }}
                          className="px-2.5 py-1 rounded-md bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] text-xs font-medium text-[#c9d1d9] hover:text-[#f0f6fc] transition-colors cursor-pointer"
                        >
                          Overview
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setActiveRepo(repo)
                            navigate('/pull-requests')
                          }}
                          className="px-2.5 py-1 rounded-md bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] text-xs font-medium text-[#c9d1d9] hover:text-[#f0f6fc] transition-colors cursor-pointer flex items-center gap-1"
                        >
                          <GitPullRequestIcon size={12} />
                          <span>PRs</span>
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        {/* Recent Workspace Architecture Reviews Card */}
        <div className="flex flex-col h-full space-y-2">
          <div className="h-6 flex items-center justify-between text-xs">
            <span className="font-semibold text-[#f0f6fc] flex items-center gap-1.5">
              <ClockIcon size={14} className="text-[#8b949e]" />
              <span>Recent Architecture Reviews ({allReviews.length})</span>
            </span>
          </div>

          <div className="flex-1 flex flex-col rounded-md border border-[#30363d] bg-[#0d1117] overflow-hidden min-h-[220px]">
            {isLoadingReviews && allReviews.length === 0 ? (
              <div className="flex-1 flex items-center justify-center p-6 text-center text-xs text-[#9198a1]">
                Loading reviews...
              </div>
            ) : allReviews.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center p-6 text-center space-y-1 text-[#9198a1]">
                <div className="font-medium text-[#f0f6fc]">No reviews recorded yet</div>
                <div className="text-[11px] text-[#8b949e]">Run an architecture review on any pull request to see workspace activity.</div>
              </div>
            ) : (
              <div className="divide-y divide-[#21262d] flex-1">
                {allReviews.slice(0, 5).map((rev) => {
                  const targetRepo = repositories.find((r) => r.id === rev.repository_id)
                  return (
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
                            className="font-semibold text-xs text-[#f0f6fc] hover:text-[#4493f8] transition-colors"
                          >
                            {targetRepo ? `${targetRepo.full_name} ` : ''}PR #{rev.pr_number}
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

                        <p className="text-[#c9d1d9] text-xs line-clamp-1">
                          {rev.summary || 'Architecture conformance review executed.'}
                        </p>

                        <div className="flex items-center gap-2 text-[11px] text-[#9198a1]">
                          <span>{new Date(rev.created_at).toLocaleDateString()}</span>
                          {rev.findings_count > 0 && (
                            <>
                              <span>•</span>
                              <span>{rev.findings_count} findings</span>
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default GlobalHomePage
