import React, { useEffect, useState, useMemo } from 'react'
import { Link, useSearchParams, useNavigate, useLocation } from 'react-router-dom'
import {
  GitPullRequestIcon,
  GitMergeIcon,
  GitPullRequestClosedIcon,
  CheckIcon,
  CheckCircleIcon,
  XCircleIcon,
  SearchIcon,
  TriangleDownIcon,
  PlusIcon,
  RepoIcon,
} from '@primer/octicons-react'
import { useRepo } from '@/context/useRepo'
import type { GitHubPR, RecentReview } from '@/types'

export type CanonicalPRView = 'open' | 'closed' | 'drift' | 'approved'
type SortOrder = 'newest' | 'oldest'

function formatTimeAgo(dateStr?: string): string {
  if (!dateStr) return 'recently'
  const diff = (Date.now() - new Date(dateStr).getTime()) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)} minutes ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)} hours ago`
  if (diff < 2592000) return `${Math.floor(diff / 86400)} days ago`
  return new Date(dateStr).toLocaleDateString()
}

function getPaginationItems(current: number, total: number): (number | '...')[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1)
  }
  if (current <= 4) {
    return [1, 2, 3, 4, 5, '...', total]
  }
  if (current >= total - 3) {
    return [1, '...', total - 4, total - 3, total - 2, total - 1, total]
  }
  return [1, '...', current - 1, current, current + 1, '...', total]
}

export const PullRequestsPage: React.FC = () => {
  const {
    repositories,
    activeRepo,
    setActiveRepo,
    isLoadingRepositories,
    pullRequestsByRepoState,
    fetchPullRequests,
    fetchReviews,
    isSyncing,
  } = useRepo()

  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const queryRepoId = searchParams.get('repoId')

  const currentRepo = useMemo(() => {
    if (queryRepoId) {
      const found = repositories.find((r) => r.id === queryRepoId)
      if (found) return found
    }
    return activeRepo || repositories[0] || null
  }, [queryRepoId, activeRepo, repositories])

  const [reviews, setReviews] = useState<RecentReview[]>([])
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [internalView, setInternalView] = useState<CanonicalPRView>('open')
  const [internalAuthorFilter, setInternalAuthorFilter] = useState<string>('all')
  const [sortOrder, setSortOrder] = useState<SortOrder>('newest')

  const ITEMS_PER_PAGE = 25
  const [currentPage, setCurrentPage] = useState<number>(1)

  const [openAuthorMenu, setOpenAuthorMenu] = useState(false)
  const [openSortMenu, setOpenSortMenu] = useState(false)

  const currentRepoId = currentRepo?.id

  // Synchronize active view with URL parameters (supports ?view=..., ?state=..., ?workflow=...)
  const viewParam = searchParams.get('view')
  const stateParam = searchParams.get('state')
  const workflowParam = searchParams.get('workflow')

  const canonicalView: CanonicalPRView = useMemo(() => {
    if (viewParam === 'drift' || workflowParam === 'drift') return 'drift'
    if (viewParam === 'approved' || workflowParam === 'approved') return 'approved'
    if (viewParam === 'closed' || stateParam === 'closed') return 'closed'
    if (viewParam === 'open' || stateParam === 'open') return 'open'
    return internalView
  }, [viewParam, stateParam, workflowParam, internalView])

  const authorParam = searchParams.get('author')
  const authorFilter = authorParam || internalAuthorFilter

  // Reset pagination on filter or repository change
  const filterKey = `${currentRepoId || ''}-${canonicalView}-${authorFilter}-${searchQuery}-${sortOrder}`
  const [prevFilterKey, setPrevFilterKey] = useState(filterKey)
  if (prevFilterKey !== filterKey) {
    setPrevFilterKey(filterKey)
    setCurrentPage(1)
  }

  const setCanonicalView = (view: CanonicalPRView) => {
    setInternalView(view)
    const next = new URLSearchParams(searchParams)
    next.set('view', view)
    next.delete('state')
    next.delete('workflow')
    setSearchParams(next)
  }

  const setAuthorFilter = (auth: string) => {
    setInternalAuthorFilter(auth)
    const next = new URLSearchParams(searchParams)
    if (auth === 'all') next.delete('author')
    else next.set('author', auth)
    setSearchParams(next)
  }

  const [openRepoMenu, setOpenRepoMenu] = useState(false)
  const [repoFilterQuery, setRepoFilterQuery] = useState('')
  const [highlightedRepoIndex, setHighlightedRepoIndex] = useState(0)
  const repoTriggerRef = React.useRef<HTMLButtonElement>(null)
  const repoSearchInputRef = React.useRef<HTMLInputElement>(null)

  const filteredRepositories = useMemo(() => {
    const q = repoFilterQuery.trim().toLowerCase()
    if (!q) return repositories
    return repositories.filter((r) => r.full_name.toLowerCase().includes(q))
  }, [repositories, repoFilterQuery])

  useEffect(() => {
    if (openRepoMenu) {
      const timer = setTimeout(() => {
        repoSearchInputRef.current?.focus()
      }, 50)
      return () => clearTimeout(timer)
    }
  }, [openRepoMenu])

  const handleSelectRepo = (repo: (typeof repositories)[0]) => {
    setActiveRepo(repo)
    setSearchParams({ repoId: repo.id, view: canonicalView })
    setOpenRepoMenu(false)
    setRepoFilterQuery('')
    repoTriggerRef.current?.focus()
  }

  const handleRepoInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      setOpenRepoMenu(false)
      setRepoFilterQuery('')
      repoTriggerRef.current?.focus()
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightedRepoIndex((prev) =>
        filteredRepositories.length > 0 ? (prev + 1) % filteredRepositories.length : 0
      )
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightedRepoIndex((prev) =>
        filteredRepositories.length > 0
          ? (prev - 1 + filteredRepositories.length) % filteredRepositories.length
          : 0
      )
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const targetRepo = filteredRepositories[highlightedRepoIndex]
      if (targetRepo) {
        handleSelectRepo(targetRepo)
      }
    }
  }

  // Cached PR collections per state
  const openPRs = currentRepoId ? pullRequestsByRepoState[`${currentRepoId}:open`] : undefined
  const closedPRs = currentRepoId ? pullRequestsByRepoState[`${currentRepoId}:closed`] : undefined

  // Fetch reviews strictly scoped to currentRepoId (cached in context)
  useEffect(() => {
    if (!currentRepoId) return
    let isMounted = true

    fetchReviews(currentRepoId, false)
      .then((recentReviews) => {
        if (!isMounted) return
        setReviews(recentReviews)
      })
      .catch(() => {})

    return () => {
      isMounted = false
    }
  }, [currentRepoId, fetchReviews])

  // Lazy / on-demand fetch for active PR view:
  // For 'open' and 'closed', fetches respective state from GitHub.
  // For 'drift' and 'approved', ZERO GitHub PR fetch is triggered.
  useEffect(() => {
    if (!currentRepoId) return
    if (canonicalView === 'drift' || canonicalView === 'approved') return

    let isMounted = true
    fetchPullRequests(currentRepoId, canonicalView, false)
      .then(() => {
        if (!isMounted) return
        setError(null)
      })
      .catch((err) => {
        if (!isMounted) return
        setError(err instanceof Error ? err.message : 'Failed to load pull requests')
      })

    return () => {
      isMounted = false
    }
  }, [currentRepoId, canonicalView, fetchPullRequests])

  const reviewsByPrNumber = useMemo(() => {
    const map = new Map<number, RecentReview>()
    for (const r of reviews) {
      if (
        r.pr_number &&
        (!map.has(r.pr_number) ||
          new Date(r.created_at) > new Date(map.get(r.pr_number)!.created_at))
      ) {
        map.set(r.pr_number, r)
      }
    }
    return map
  }, [reviews])

  // Source list of PRs for current canonical view
  const basePullRequests: GitHubPR[] = useMemo(() => {
    if (!currentRepoId) return []

    // Helper to deduplicate any array by repoId + ":" + pr.number
    const deduplicateByRepoPr = (items: GitHubPR[] | undefined): GitHubPR[] => {
      if (!items || items.length === 0) return []
      const seen = new Set<string>()
      const result: GitHubPR[] = []
      for (const item of items) {
        const key = `${currentRepoId}:${item.number}`
        if (!seen.has(key)) {
          seen.add(key)
          result.push(item)
        }
      }
      return result
    }

    if (canonicalView === 'open') {
      return deduplicateByRepoPr(openPRs)
    }
    if (canonicalView === 'closed') {
      return deduplicateByRepoPr(closedPRs)
    }

    // Drift Detected: only PRs with actual reviewed drift findings, deduplicated by pr_number
    if (canonicalView === 'drift') {
      const driftRevs = reviews.filter((r) => r.drift_detected && typeof r.pr_number === 'number')
      const allKnown = [...(openPRs || []), ...(closedPRs || [])]
      const knownMap = new Map(allKnown.map((p) => [p.number, p]))
      const seenPrNumbers = new Set<number>()
      const result: GitHubPR[] = []

      for (const rev of driftRevs) {
        const prNum = rev.pr_number as number
        if (seenPrNumbers.has(prNum)) continue
        seenPrNumbers.add(prNum)

        const existing = knownMap.get(prNum)
        if (existing) {
          result.push(existing)
        } else {
          result.push({
            id: prNum,
            number: prNum,
            title: rev.pr_title || `PR #${prNum} Architecture Review`,
            state: 'open',
            author: rev.author || 'contributor',
            created_at: rev.created_at,
            updated_at: rev.created_at,
            head_sha: 'HEAD',
            base_branch: 'main',
          } as GitHubPR)
        }
      }
      return result
    }

    // Conforming / Approved: only PRs with conforming or approved exception reviews, deduplicated by pr_number
    if (canonicalView === 'approved') {
      const approvedRevs = reviews.filter((r) => !r.drift_detected && r.status === 'completed' && typeof r.pr_number === 'number')
      const allKnown = [...(openPRs || []), ...(closedPRs || [])]
      const knownMap = new Map(allKnown.map((p) => [p.number, p]))
      const seenPrNumbers = new Set<number>()
      const result: GitHubPR[] = []

      for (const rev of approvedRevs) {
        const prNum = rev.pr_number as number
        if (seenPrNumbers.has(prNum)) continue
        seenPrNumbers.add(prNum)

        const existing = knownMap.get(prNum)
        if (existing) {
          result.push(existing)
        } else {
          result.push({
            id: prNum,
            number: prNum,
            title: rev.pr_title || `PR #${prNum} Architecture Review`,
            state: 'open',
            author: rev.author || 'contributor',
            created_at: rev.created_at,
            updated_at: rev.created_at,
            head_sha: 'HEAD',
            base_branch: 'main',
          } as GitHubPR)
        }
      }
      return result
    }

    return []
  }, [currentRepoId, canonicalView, openPRs, closedPRs, reviews])

  // Available authors in active dataset
  const availableAuthors = useMemo(() => {
    const s = new Set<string>()
    for (const p of basePullRequests) {
      if (p.author) s.add(p.author)
    }
    return Array.from(s).sort()
  }, [basePullRequests])

  // Subheader counts for the 4 canonical views
  const counts = useMemo(() => {
    const dedup = (items: GitHubPR[] | undefined) => {
      if (!items) return 0
      const seen = new Set<number>()
      for (const p of items) seen.add(p.number)
      return seen.size
    }
    const openCount = dedup(openPRs)
    const closedCount = dedup(closedPRs)
    const driftCount = new Set(reviews.filter((r) => r.drift_detected && typeof r.pr_number === 'number').map((r) => r.pr_number)).size
    const approvedCount = new Set(reviews.filter((r) => !r.drift_detected && r.status === 'completed' && typeof r.pr_number === 'number').map((r) => r.pr_number)).size

    return {
      open: openCount,
      closed: closedCount,
      drift: driftCount,
      approved: approvedCount,
    }
  }, [openPRs, closedPRs, reviews])

  // Filtered & sorted PR list
  const filteredPRs = useMemo(() => {
    if (!currentRepo) return []

    const list = basePullRequests.filter((pr) => {
      // Author dropdown filter
      if (authorFilter !== 'all' && pr.author !== authorFilter) return false

      // GitHub-style search query parsing (supports qualifiers: is:pr, is:open, is:closed, is:merged, is:drift, is:approved, author:username, and free text)
      if (searchQuery.trim()) {
        const rawTokens = searchQuery.trim().split(/\s+/)
        for (const token of rawTokens) {
          const lowerToken = token.toLowerCase()
          if (lowerToken === 'is:pr') {
            // All items are pull requests
            continue
          }
          if (lowerToken === 'is:open') {
            if (pr.state !== 'open') return false
            continue
          }
          if (lowerToken === 'is:closed') {
            if (pr.state !== 'closed' && pr.state !== 'merged') return false
            continue
          }
          if (lowerToken === 'is:merged') {
            if (pr.state !== 'merged' && !pr.is_merged) return false
            continue
          }
          if (lowerToken === 'is:drift') {
            const rev = reviewsByPrNumber.get(pr.number)
            if (!rev?.drift_detected) return false
            continue
          }
          if (lowerToken === 'is:approved' || lowerToken === 'is:conforming') {
            const rev = reviewsByPrNumber.get(pr.number)
            if (!rev || rev.drift_detected || rev.status !== 'completed') return false
            continue
          }
          if (lowerToken.startsWith('author:')) {
            const authQuery = lowerToken.slice(7)
            if (authQuery && pr.author.toLowerCase() !== authQuery) return false
            continue
          }
          // Free text keyword: must match title, number, or author
          const matchKeyword =
            pr.title.toLowerCase().includes(lowerToken) ||
            String(pr.number).includes(lowerToken) ||
            pr.author.toLowerCase().includes(lowerToken)
          if (!matchKeyword) return false
        }
      }

      return true
    })

    list.sort((a, b) => {
      const dateA = new Date(a.created_at || 0).getTime()
      const dateB = new Date(b.created_at || 0).getTime()
      return sortOrder === 'newest' ? dateB - dateA : dateA - dateB
    })

    return list
  }, [currentRepo, basePullRequests, authorFilter, searchQuery, sortOrder, reviewsByPrNumber])

  const totalFiltered = filteredPRs.length
  const totalPages = Math.max(1, Math.ceil(totalFiltered / ITEMS_PER_PAGE))
  const safeCurrentPage = Math.min(Math.max(1, currentPage), totalPages)

  const paginatedPRs = useMemo(() => {
    const startIndex = (safeCurrentPage - 1) * ITEMS_PER_PAGE
    return filteredPRs.slice(startIndex, startIndex + ITEMS_PER_PAGE)
  }, [filteredPRs, safeCurrentPage])

  const startItem = totalFiltered === 0 ? 0 : (safeCurrentPage - 1) * ITEMS_PER_PAGE + 1
  const endItem = Math.min(safeCurrentPage * ITEMS_PER_PAGE, totalFiltered)

  const isStateLoaded = currentRepoId
    ? canonicalView === 'open'
      ? !!openPRs
      : canonicalView === 'closed'
        ? !!closedPRs
        : true
    : true

  const isLoading =
    (!!currentRepoId && !isStateLoaded && !error) ||
    (isSyncing && basePullRequests.length === 0)

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
        <div className="flex items-center justify-between pb-1">
          <h1 className="text-xl font-semibold text-[#f0f6fc] tracking-tight">
            Pull requests
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
    <div className="space-y-4">
      {/* 1. Header Bar: Title + New Pull Request Button */}
      <div className="flex items-center justify-between pb-1">
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold text-[#f0f6fc] tracking-tight">
            Pull requests
          </h1>
          {currentRepo && (
            <span className="text-xs text-[#9198a1]">
              in <span className="text-[#f0f6fc] font-medium">{currentRepo.full_name}</span>
            </span>
          )}
        </div>

        <button
          type="button"
          onClick={() => {
            if (currentRepo) {
              window.open(`https://github.com/${currentRepo.full_name}/compare`, '_blank')
            }
          }}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[#238636] hover:bg-[#2ea043] border border-[rgba(240,246,252,0.1)] text-white text-xs font-semibold shadow-xs transition-colors cursor-pointer"
        >
          <PlusIcon size={14} />
          <span>New pull request</span>
        </button>
      </div>

      {/* 2. GitHub Search & Filter Control Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
        <div className="flex-1 flex items-center rounded-md border border-[#30363d] bg-[#0d1117] focus-within:border-[#1f6feb] focus-within:ring-1 focus-within:ring-[#1f6feb] transition-all">
          <div className="relative flex-1 flex items-center">
            <SearchIcon size={14} className="absolute left-3 text-[#656c76] pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={`is:pr is:${canonicalView} ${authorFilter !== 'all' ? `author:${authorFilter} ` : ''}`}
              className="w-full pl-9 pr-3 py-1.5 bg-transparent text-xs text-[#f0f6fc] placeholder-[#656c76] focus:outline-none"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                className="px-2 text-xs text-[#9198a1] hover:text-[#f0f6fc] cursor-pointer"
              >
                Clear
              </button>
            )}
          </div>
        </div>

        {/* Primer GitHub Repository Switcher */}
        <div className="relative sm:w-72 shrink-0 flex items-center">
          <button
            ref={repoTriggerRef}
            type="button"
            onClick={() => {
              const nextOpen = !openRepoMenu
              setOpenRepoMenu(nextOpen)
              if (nextOpen) {
                const idx = repositories.findIndex((r) => r.id === currentRepo?.id)
                setHighlightedRepoIndex(idx >= 0 ? idx : 0)
              }
              setOpenAuthorMenu(false)
              setOpenSortMenu(false)
            }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                const idx = repositories.findIndex((r) => r.id === currentRepo?.id)
                setHighlightedRepoIndex(idx >= 0 ? idx : 0)
                setOpenRepoMenu(true)
                setOpenAuthorMenu(false)
                setOpenSortMenu(false)
              }
            }}
            aria-haspopup="listbox"
            aria-expanded={openRepoMenu}
            className="w-full flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-md bg-[#161b22] border border-[#30363d] hover:border-[#8b949e] hover:bg-[#21262d] text-xs text-[#f0f6fc] focus:outline-none focus:ring-1 focus:ring-[#1f6feb] focus:border-[#1f6feb] transition-colors cursor-pointer select-none"
            title={currentRepo ? currentRepo.full_name : 'Select repository'}
          >
            <div className="flex items-center gap-1.5 truncate min-w-0">
              <RepoIcon size={14} className="text-[#9198a1] shrink-0" />
              <span className="truncate font-medium">
                {currentRepo ? currentRepo.full_name : 'Select repository'}
              </span>
            </div>
            <TriangleDownIcon
              size={12}
              className={`text-[#9198a1] shrink-0 transition-transform ${openRepoMenu ? 'rotate-180' : ''}`}
            />
          </button>

          {openRepoMenu && (
            <>
              <div
                className="fixed inset-0 z-40"
                onClick={() => {
                  setOpenRepoMenu(false)
                  setRepoFilterQuery('')
                }}
              />
              <div
                role="listbox"
                aria-label="Repositories"
                className="absolute right-0 top-full mt-1.5 w-80 max-w-[calc(100vw-2rem)] rounded-md bg-[#161b22] border border-[#30363d] shadow-2xl py-1 z-50 text-xs flex flex-col"
              >
                <div className="px-3 py-2 text-[11px] font-semibold text-[#9198a1] border-b border-[#30363d] flex items-center justify-between">
                  <span>Switch repository</span>
                </div>

                <div className="p-2 border-b border-[#30363d] bg-[#161b22]">
                  <div className="relative flex items-center">
                    <SearchIcon size={14} className="absolute left-2.5 text-[#656c76] pointer-events-none" />
                    <input
                      ref={repoSearchInputRef}
                      type="text"
                      value={repoFilterQuery}
                      onChange={(e) => {
                        setRepoFilterQuery(e.target.value)
                        setHighlightedRepoIndex(0)
                      }}
                      onKeyDown={handleRepoInputKeyDown}
                      placeholder="Search repositories..."
                      className="w-full pl-8 pr-2.5 py-1.5 bg-[#0d1117] border border-[#30363d] rounded-md text-xs text-[#f0f6fc] placeholder-[#656c76] focus:outline-none focus:ring-1 focus:ring-[#1f6feb] focus:border-[#1f6feb]"
                    />
                  </div>
                </div>

                <div className="max-h-60 overflow-y-auto py-1">
                  {filteredRepositories.length === 0 ? (
                    <div className="px-3 py-4 text-center text-[#9198a1] italic">
                      No repositories found
                    </div>
                  ) : (
                    filteredRepositories.map((repo, idx) => {
                      const isSelected = currentRepo?.id === repo.id
                      const isHighlighted = idx === highlightedRepoIndex
                      return (
                        <button
                          key={repo.id}
                          type="button"
                          role="option"
                          aria-selected={isSelected}
                          onClick={() => handleSelectRepo(repo)}
                          onMouseEnter={() => setHighlightedRepoIndex(idx)}
                          className={`w-full text-left px-3 py-2 flex items-center gap-2 transition-colors cursor-pointer ${
                            isHighlighted ? 'bg-[#21262d]' : ''
                          } ${isSelected ? 'text-[#f0f6fc] font-semibold' : 'text-[#c9d1d9]'}`}
                        >
                          <div className="w-4 shrink-0 flex items-center justify-center">
                            {isSelected && <CheckIcon size={14} className="text-[#4493f8]" />}
                          </div>
                          <RepoIcon size={14} className="text-[#9198a1] shrink-0" />
                          <span className="truncate flex-1">{repo.full_name}</span>
                        </button>
                      )
                    })
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* 3. MAIN CANONICAL PR TABLE */}
      <div className="border border-[#30363d] rounded-md bg-[#0d1117] overflow-hidden">
        {/* Table Header Row: The 4 Canonical Views */}
        <div className="bg-[#161b22] border-b border-[#30363d] px-4 py-2.5 flex flex-wrap items-center justify-between gap-3 text-xs">
          {/* Left: 4 Canonical PR View Tabs with counts */}
          <div className="flex items-center gap-3 overflow-x-auto">
            <button
              type="button"
              onClick={() => setCanonicalView('open')}
              className={`flex items-center gap-1.5 font-medium transition-colors cursor-pointer ${
                canonicalView === 'open'
                  ? 'font-semibold text-[#f0f6fc]'
                  : 'text-[#9198a1] hover:text-[#f0f6fc]'
              }`}
            >
              <GitPullRequestIcon size={16} className={canonicalView === 'open' ? 'text-[#3fb950]' : 'text-[#8b949e]'} />
              <span>{counts.open} Open</span>
            </button>

            <button
              type="button"
              onClick={() => setCanonicalView('closed')}
              className={`flex items-center gap-1.5 font-medium transition-colors cursor-pointer ${
                canonicalView === 'closed'
                  ? 'font-semibold text-[#f0f6fc]'
                  : 'text-[#9198a1] hover:text-[#f0f6fc]'
              }`}
            >
              <GitMergeIcon size={16} className={canonicalView === 'closed' ? 'text-[#ab7df8]' : 'text-[#8b949e]'} />
              <span>{counts.closed} Closed & Merged</span>
            </button>

            <button
              type="button"
              onClick={() => setCanonicalView('drift')}
              className={`flex items-center gap-1.5 font-medium transition-colors cursor-pointer ${
                canonicalView === 'drift'
                  ? 'font-semibold text-[#f0f6fc]'
                  : 'text-[#9198a1] hover:text-[#f0f6fc]'
              }`}
            >
              <XCircleIcon size={16} className={canonicalView === 'drift' ? 'text-[#f85149]' : 'text-[#8b949e]'} />
              <span>{counts.drift} Drift Detected</span>
            </button>

            <button
              type="button"
              onClick={() => setCanonicalView('approved')}
              className={`flex items-center gap-1.5 font-medium transition-colors cursor-pointer ${
                canonicalView === 'approved'
                  ? 'font-semibold text-[#f0f6fc]'
                  : 'text-[#9198a1] hover:text-[#f0f6fc]'
              }`}
            >
              <CheckCircleIcon size={16} className={canonicalView === 'approved' ? 'text-[#3fb950]' : 'text-[#8b949e]'} />
              <span>{counts.approved} Conforming / Approved</span>
            </button>
          </div>

          {/* Right: Dropdown Controls (Author, Sort) */}
          <div className="flex items-center gap-3 text-[#9198a1]">
            {/* Author Dropdown */}
            <div className="relative">
              <button
                type="button"
                onClick={() => {
                  setOpenAuthorMenu(!openAuthorMenu)
                  setOpenSortMenu(false)
                }}
                className="flex items-center gap-1 hover:text-[#f0f6fc] cursor-pointer"
              >
                <span>Author {authorFilter !== 'all' ? `(${authorFilter})` : ''}</span>
                <TriangleDownIcon size={12} />
              </button>
              {openAuthorMenu && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setOpenAuthorMenu(false)} />
                  <div className="absolute right-0 mt-1.5 w-48 rounded-md bg-[#161b22] border border-[#30363d] shadow-2xl py-1 z-50 text-xs">
                    <div className="px-3 py-1.5 font-semibold text-[11px] border-b border-[#30363d] text-[#f0f6fc]">
                      Filter by author
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setAuthorFilter('all')
                        setOpenAuthorMenu(false)
                      }}
                      className="w-full text-left px-3 py-1.5 hover:bg-[#21262d] text-[#c9d1d9] flex items-center justify-between cursor-pointer"
                    >
                      <span>All authors</span>
                      {authorFilter === 'all' && <CheckIcon size={12} className="text-[#4493f8]" />}
                    </button>
                    {availableAuthors.map((auth) => (
                      <button
                        key={auth}
                        type="button"
                        onClick={() => {
                          setAuthorFilter(auth)
                          setOpenAuthorMenu(false)
                        }}
                        className="w-full text-left px-3 py-1.5 hover:bg-[#21262d] text-[#c9d1d9] flex items-center justify-between cursor-pointer"
                      >
                        <span className="truncate">@{auth}</span>
                        {authorFilter === auth && <CheckIcon size={12} className="text-[#4493f8]" />}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* Sort Dropdown */}
            <div className="relative">
              <button
                type="button"
                onClick={() => {
                  setOpenSortMenu(!openSortMenu)
                  setOpenAuthorMenu(false)
                }}
                className="flex items-center gap-1 hover:text-[#f0f6fc] cursor-pointer"
              >
                <span>Sort</span>
                <TriangleDownIcon size={12} />
              </button>
              {openSortMenu && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setOpenSortMenu(false)} />
                  <div className="absolute right-0 mt-1.5 w-40 rounded-md bg-[#161b22] border border-[#30363d] shadow-2xl py-1 z-50 text-xs">
                    <div className="px-3 py-1.5 font-semibold text-[11px] border-b border-[#30363d] text-[#f0f6fc]">
                      Sort by
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setSortOrder('newest')
                        setOpenSortMenu(false)
                      }}
                      className="w-full text-left px-3 py-1.5 hover:bg-[#21262d] text-[#c9d1d9] flex items-center justify-between cursor-pointer"
                    >
                      <span>Newest</span>
                      {sortOrder === 'newest' && <CheckIcon size={12} className="text-[#4493f8]" />}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setSortOrder('oldest')
                        setOpenSortMenu(false)
                      }}
                      className="w-full text-left px-3 py-1.5 hover:bg-[#21262d] text-[#c9d1d9] flex items-center justify-between cursor-pointer"
                    >
                      <span>Oldest</span>
                      {sortOrder === 'oldest' && <CheckIcon size={12} className="text-[#4493f8]" />}
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        {/* PR Rows */}
        {isLoading ? (
          <div className="p-12 text-center space-y-3">
            <div className="w-6 h-6 border-2 border-[#1f6feb] border-t-transparent rounded-full animate-spin mx-auto" />
            <div className="text-xs text-[#9198a1]">Fetching pull requests...</div>
          </div>
        ) : error ? (
          <div className="p-12 text-center space-y-3">
            <XCircleIcon size={28} className="text-[#f85149] mx-auto mb-1" />
            <div className="text-sm font-semibold text-[#f0f6fc]">
              Failed to load pull requests
            </div>
            <p className="text-xs text-[#f85149] max-w-md mx-auto">
              {error}
            </p>
            <button
              type="button"
              onClick={() => {
                setError(null)
                if (currentRepoId) {
                  fetchPullRequests(currentRepoId, canonicalView, true).catch((err) => {
                    setError(err instanceof Error ? err.message : 'Failed to load pull requests')
                  })
                }
              }}
              className="px-3 py-1.5 rounded-md bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] text-xs text-[#f0f6fc] font-medium transition-colors cursor-pointer"
            >
              Retry
            </button>
          </div>
        ) : filteredPRs.length === 0 ? (
          <div className="p-12 text-center space-y-2">
            <GitPullRequestIcon size={28} className="text-[#656c76] mx-auto mb-1" />
            <div className="text-sm font-semibold text-[#f0f6fc]">
              {canonicalView === 'drift'
                ? 'No pull requests with architectural drift'
                : canonicalView === 'approved'
                  ? 'No conforming pull requests yet'
                  : 'No pull requests match this filter'}
            </div>
            <p className="text-xs text-[#9198a1] max-w-sm mx-auto">
              {canonicalView === 'drift'
                ? 'Pull requests where an executed architecture review identifies architectural violations will appear here.'
                : canonicalView === 'approved'
                  ? 'Pull requests with conforming reviews or approved exceptions will appear here.'
                  : 'Try adjusting your search query or switching views.'}
            </p>
          </div>
        ) : (
          <div className="divide-y divide-[#21262d]">
            {paginatedPRs.map((pr) => {
              const isOpen = pr.state === 'open'
              const isMerged = pr.state === 'merged' || pr.is_merged === true
              const rev = reviewsByPrNumber.get(pr.number)

              return (
                <div
                  key={`${currentRepoId}:${pr.number}`}
                  className="px-4 py-3 flex items-start gap-3 hover:bg-[#161b22]/60 transition-colors group"
                >
                  {/* Left: PR State Icon */}
                  <div className="pt-0.5 shrink-0">
                    {isMerged ? (
                      <GitMergeIcon size={16} className="text-[#ab7df8]" />
                    ) : isOpen ? (
                      <GitPullRequestIcon size={16} className="text-[#3fb950]" />
                    ) : (
                      <GitPullRequestClosedIcon size={16} className="text-[#9198a1]" />
                    )}
                  </div>

                  {/* Center: Title + Labels + Subtitle */}
                  <div className="flex-1 min-w-0 space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Link
                        to={`/pull-requests/${currentRepoId}/${pr.number}`}
                        className="text-sm font-semibold text-[#f0f6fc] hover:text-[#4493f8] group-hover:text-[#4493f8] transition-colors leading-snug break-words"
                      >
                        {pr.title}
                      </Link>

                      {/* Conformance Label Pills */}
                      {rev && rev.drift_detected && (
                        <span className="px-2 py-0.5 rounded-full text-[11px] font-medium border border-[#f85149]/40 bg-[#f85149]/15 text-[#f85149]">
                          architecture drift
                        </span>
                      )}

                      {rev && !rev.drift_detected && (
                        <span className="px-2 py-0.5 rounded-full text-[11px] font-medium border border-[#3fb950]/40 bg-[#3fb950]/15 text-[#3fb950]">
                          approved
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-1.5 text-xs text-[#9198a1] flex-wrap">
                      <span>#{pr.number}</span>
                      <span>opened {formatTimeAgo(pr.created_at)}</span>
                      <span>by</span>
                      <span className="text-[#c9d1d9] font-medium">@{pr.author}</span>
                      {pr.base_branch && (
                        <>
                          <span>into</span>
                          <code className="px-1 py-0.2 rounded bg-[#21262d] text-[#c9d1d9] text-[11px] font-mono">
                            {pr.base_branch}
                          </code>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Right: Architecture Check Status + Review Action */}
                  <div className="shrink-0 flex items-center gap-3 pt-0.5 text-xs text-[#9198a1]">
                    {rev ? (
                      rev.drift_detected ? (
                        <div className="flex items-center gap-1 text-[#f85149]" title="Architecture drift detected">
                          <XCircleIcon size={16} />
                        </div>
                      ) : (
                        <div className="flex items-center gap-1 text-[#3fb950]" title="Architectural conformance verified">
                          <CheckCircleIcon size={16} />
                        </div>
                      )
                    ) : (
                      <span className="text-[11px] text-[#656c76]">Not reviewed</span>
                    )}

                    <Link
                      to={`/pull-requests/${currentRepoId}/${pr.number}`}
                      className="opacity-0 group-hover:opacity-100 px-2 py-1 rounded bg-[#21262d] hover:bg-[#30363d] text-xs font-medium text-[#f0f6fc] transition-all"
                    >
                      Review
                    </Link>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {/* 4. Client-side Pagination Bar */}
        {totalFiltered > 0 && (
          <div className="flex flex-col sm:flex-row items-center justify-between gap-3 px-4 py-3 border-t border-[#30363d] bg-[#161b22]/40 text-xs">
            <div className="text-[#9198a1]">
              Showing <span className="text-[#f0f6fc] font-medium">{startItem}–{endItem}</span> of{' '}
              <span className="text-[#f0f6fc] font-medium">{totalFiltered}</span>
            </div>

            {totalPages > 1 && (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                  disabled={safeCurrentPage === 1}
                  className={`px-3 py-1 rounded-md border text-xs font-medium transition-colors ${
                    safeCurrentPage === 1
                      ? 'border-transparent text-[#484f58] cursor-not-allowed pointer-events-none'
                      : 'border-[#30363d] bg-[#21262d] text-[#c9d1d9] hover:bg-[#30363d] hover:text-[#f0f6fc] cursor-pointer'
                  }`}
                >
                  Previous
                </button>

                <div className="flex items-center gap-1">
                  {getPaginationItems(safeCurrentPage, totalPages).map((p, idx) => {
                    if (p === '...') {
                      return (
                        <span key={`ellipsis-${idx}`} className="px-2 py-1 text-[#9198a1]">
                          …
                        </span>
                      )
                    }
                    const pageNum = p as number
                    const isActive = pageNum === safeCurrentPage
                    return (
                      <button
                        key={pageNum}
                        type="button"
                        onClick={() => setCurrentPage(pageNum)}
                        className={`min-w-7 px-2 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer ${
                          isActive
                            ? 'bg-[#1f6feb] text-white border border-[#1f6feb]'
                            : 'border border-transparent text-[#c9d1d9] hover:border-[#30363d] hover:bg-[#21262d]'
                        }`}
                      >
                        {pageNum}
                      </button>
                    )
                  })}
                </div>

                <button
                  type="button"
                  onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                  disabled={safeCurrentPage === totalPages}
                  className={`px-3 py-1 rounded-md border text-xs font-medium transition-colors ${
                    safeCurrentPage === totalPages
                      ? 'border-transparent text-[#484f58] cursor-not-allowed pointer-events-none'
                      : 'border-[#30363d] bg-[#21262d] text-[#c9d1d9] hover:bg-[#30363d] hover:text-[#f0f6fc] cursor-pointer'
                  }`}
                >
                  Next
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default PullRequestsPage
