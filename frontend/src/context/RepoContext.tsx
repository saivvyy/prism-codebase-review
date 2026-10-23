import React, { useEffect, useState, useCallback, useRef } from 'react'
import type { ConnectedRepo, GitHubPR, GitHubPRListResponse, RecentReview, EngineeringMemory } from '@/types'
import {
  listRepositories,
  listPullRequests,
  listRecentReviews,
  getRepositoryPRCount,
  listRepositoryMemories,
  searchRepositoryMemories,
} from '@/services/api'
import { RepoContext } from './repo-context-def'
import { useAuth } from './useAuth'

const ACTIVE_REPO_STORAGE_KEY = 'eng_review_active_repo_id'

export const RepoProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { sessionToken } = useAuth()
  const [repositories, setRepositories] = useState<ConnectedRepo[]>([])
  const [activeRepo, setActiveRepoState] = useState<ConnectedRepo | null>(null)
  const [pullRequestsByRepo, setPullRequestsByRepo] = useState<Record<string, GitHubPR[]>>({})
  const [pullRequestsByRepoState, setPullRequestsByRepoState] = useState<Record<string, GitHubPR[]>>({})
  const [prMetaByRepo, setPrMetaByRepo] = useState<Record<string, GitHubPRListResponse>>({})
  const [reviewsByRepo, setReviewsByRepo] = useState<Record<string, RecentReview[]>>({})
  const [repoPRCounts, setRepoPRCounts] = useState<Record<string, number>>({})
  const [isSidebarOpen, setSidebarOpen] = useState<boolean>(true)
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [isSyncing, setIsSyncing] = useState<boolean>(false)
  const [activePR, setActivePR] = useState<GitHubPR | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Engineering Memory Cache State
  const [memoryCache, setMemoryCache] = useState<Record<string, EngineeringMemory[]>>({})
  const [memoryRequestState, setMemoryRequestState] = useState<Record<string, 'idle' | 'loading' | 'success' | 'error'>>({})
  const [memoryError, setMemoryError] = useState<Record<string, string | null>>({})
  const [memoryLastFetched, setMemoryLastFetched] = useState<Record<string, number>>({})

  // Tracking refs to prevent duplicate calls, race conditions, and StrictMode double-fetching
  const pullRequestsRef = useRef<Record<string, GitHubPR[]>>(pullRequestsByRepo)
  const pullRequestsByRepoStateRef = useRef<Record<string, GitHubPR[]>>(pullRequestsByRepoState)
  const reviewsRef = useRef<Record<string, RecentReview[]>>(reviewsByRepo)
  const repoPRCountsRef = useRef<Record<string, number>>(repoPRCounts)
  const activeRepoRef = useRef<ConnectedRepo | null>(activeRepo)
  const memoryCacheRef = useRef<Record<string, EngineeringMemory[]>>(memoryCache)

  useEffect(() => {
    pullRequestsRef.current = pullRequestsByRepo
    pullRequestsByRepoStateRef.current = pullRequestsByRepoState
    reviewsRef.current = reviewsByRepo
    repoPRCountsRef.current = repoPRCounts
    activeRepoRef.current = activeRepo
    memoryCacheRef.current = memoryCache
  }, [pullRequestsByRepo, pullRequestsByRepoState, reviewsByRepo, repoPRCounts, activeRepo, memoryCache])

  const inFlightPRsRef = useRef<Record<string, Promise<GitHubPR[]>>>({})
  const inFlightReviewsRef = useRef<Record<string, Promise<RecentReview[]>>>({})
  const inFlightMemoriesRef = useRef<Record<string, Promise<EngineeringMemory[]>>>({})
  const inFlightReposRef = useRef<Promise<ConnectedRepo[]> | null>(null)

  // Fetch pull requests with per-state in-memory caching and in-flight deduplication
  const fetchPullRequests = useCallback(async (
    repoId: string,
    stateOrForce: string | boolean = 'open',
    forceFlag = false
  ): Promise<GitHubPR[]> => {
    if (!repoId) return []

    const targetState = typeof stateOrForce === 'string' ? stateOrForce : 'open'
    const force = typeof stateOrForce === 'boolean' ? stateOrForce : forceFlag
    const cacheKey = `${repoId}:${targetState}`

    // 1. Cache hit check
    const cached = pullRequestsByRepoStateRef.current[cacheKey]
    if (!force && cached) {
      return cached
    }

    // 2. In-flight request deduplication check
    const inFlight = inFlightPRsRef.current[cacheKey]
    if (inFlight) {
      return inFlight
    }

    // 3. Initiate fetch for requested state ('open', 'closed', or 'all')
    const fetchPromise = listPullRequests(repoId, targetState)
      .then((res) => {
        const seen = new Set<string>()
        const deduped: GitHubPR[] = []
        for (const item of (res.items || [])) {
          const canonicalKey = `${repoId}:${item.number}`
          if (!seen.has(canonicalKey)) {
            seen.add(canonicalKey)
            deduped.push(item)
          }
        }

        setPullRequestsByRepoState((prev) => ({ ...prev, [cacheKey]: deduped }))
        setPullRequestsByRepo((prev) => ({ ...prev, [repoId]: deduped }))
        const updatedMeta = { ...res, items: deduped }
        setPrMetaByRepo((prev) => ({ ...prev, [repoId]: updatedMeta, [cacheKey]: updatedMeta }))
        if (res.open_pr_count !== undefined && res.open_pr_count !== null) {
          setRepoPRCounts((prev) => ({ ...prev, [repoId]: res.open_pr_count! }))
        } else if (res.total_repository_prs !== undefined && res.total_repository_prs !== null) {
          setRepoPRCounts((prev) => ({ ...prev, [repoId]: res.total_repository_prs! }))
        }
        return deduped
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : `Failed to fetch pull requests for repository ${repoId}`
        console.error('Error fetching PRs:', msg)
        throw new Error(msg)
      })
      .finally(() => {
        delete inFlightPRsRef.current[cacheKey]
      })

    inFlightPRsRef.current[cacheKey] = fetchPromise
    return fetchPromise
  }, [])

  // Fetch actual GitHub PR count metadata for repository
  const inFlightPRCountsRef = useRef<Record<string, Promise<number>>>({})
  const fetchRepoPRCount = useCallback(async (repoId: string, force = false): Promise<number> => {
    if (!repoId) return 0
    if (!force && repoPRCountsRef.current[repoId] !== undefined) {
      return repoPRCountsRef.current[repoId]
    }
    const inFlight = inFlightPRCountsRef.current[repoId]
    if (inFlight) {
      return inFlight
    }
    const countPromise = getRepositoryPRCount(repoId)
      .then((count) => {
        setRepoPRCounts((prev) => ({ ...prev, [repoId]: count }))
        return count
      })
      .catch((err) => {
        console.warn(`Failed to fetch PR count for repository ${repoId}:`, err)
        return repoPRCountsRef.current[repoId] ?? 0
      })
      .finally(() => {
        delete inFlightPRCountsRef.current[repoId]
      })
    inFlightPRCountsRef.current[repoId] = countPromise
    return countPromise
  }, [])

  // Fetch reviews strictly scoped to a repository with in-memory caching and in-flight deduplication
  const fetchReviews = useCallback(async (repoId: string, force = false): Promise<RecentReview[]> => {
    if (!repoId) return []

    const cached = reviewsRef.current[repoId]
    if (!force && cached) {
      return cached
    }

    const inFlight = inFlightReviewsRef.current[repoId]
    if (inFlight) {
      return inFlight
    }

    const fetchPromise = listRecentReviews(50, repoId)
      .then((revs) => {
        setReviewsByRepo((prev) => ({ ...prev, [repoId]: revs }))
        return revs
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : `Failed to fetch reviews for repository ${repoId}`
        console.error('Error fetching reviews:', msg)
        return []
      })
      .finally(() => {
        delete inFlightReviewsRef.current[repoId]
      })

    inFlightReviewsRef.current[repoId] = fetchPromise
    return fetchPromise
  }, [])

  // Select different repository: updates activeRepo, persists to storage, and loads PRs/reviews once if not cached
  const setActiveRepo = useCallback((repo: ConnectedRepo | null) => {
    setActiveRepoState(repo)
    if (repo) {
      try {
        localStorage.setItem(ACTIVE_REPO_STORAGE_KEY, repo.id)
      } catch {
        // Storage disabled or unavailable
      }
      if (!reviewsRef.current[repo.id]) {
        fetchReviews(repo.id, false).catch(() => {})
      }
      if (repoPRCountsRef.current[repo.id] === undefined) {
        if (repo.open_pr_count !== undefined && repo.open_pr_count !== null) {
          setRepoPRCounts((prev) => ({ ...prev, [repo.id]: repo.open_pr_count! }))
        } else if (repo.pull_requests_count !== undefined && repo.pull_requests_count !== null) {
          setRepoPRCounts((prev) => ({ ...prev, [repo.id]: repo.pull_requests_count! }))
        } else {
          fetchRepoPRCount(repo.id, false).catch(() => {})
        }
      }
    }
  }, [fetchReviews, fetchRepoPRCount])

  // In-flight repository PR synchronization deduplication map keyed by repoId
  const syncInFlightRef = useRef<Record<string, Promise<void>>>({})
  const [syncingRepos, setSyncingRepos] = useState<Record<string, boolean>>({})

  // Centralized single source of truth for repository PR synchronization
  const syncRepository = useCallback(async (repoId: string): Promise<void> => {
    if (!repoId) return

    // 1. In-flight deduplication: multiple calls for same repo share existing promise
    const inFlight = syncInFlightRef.current[repoId]
    if (inFlight) {
      return inFlight
    }

    const syncPromise = (async () => {
      setSyncingRepos((prev) => ({ ...prev, [repoId]: true }))
      if (activeRepoRef.current?.id === repoId) {
        setIsSyncing(true)
      }
      setError(null)

      try {
        // Fetch both Open and Closed datasets concurrently with separate promises
        const [openResult, closedResult, prCountResult] = await Promise.allSettled([
          listPullRequests(repoId, 'open'),
          listPullRequests(repoId, 'closed'),
          fetchRepoPRCount(repoId, true).catch(() => 0),
        ])

        const errors: string[] = []

        // Process OPEN PRs
        let deduplicatedOpen: GitHubPR[] = []
        if (openResult.status === 'fulfilled') {
          const res = openResult.value
          const openItems = res.items || []
          // Replace cached dataset cleanly, deduplicating by canonical repoId + ":" + item.number
          const seen = new Set<string>()
          for (const item of openItems) {
            const canonicalKey = `${repoId}:${item.number}`
            if (!seen.has(canonicalKey)) {
              seen.add(canonicalKey)
              deduplicatedOpen.push(item)
            }
          }

          setPullRequestsByRepoState((prev) => ({
            ...prev,
            [`${repoId}:open`]: deduplicatedOpen,
          }))
          const metaWithDedup = { ...res, items: deduplicatedOpen }
          setPrMetaByRepo((prev) => ({
            ...prev,
            [repoId]: metaWithDedup,
            [`${repoId}:open`]: metaWithDedup,
          }))
          if (res.open_pr_count !== undefined && res.open_pr_count !== null) {
            setRepoPRCounts((prev) => ({ ...prev, [repoId]: res.open_pr_count! }))
          } else if (prCountResult.status === 'fulfilled' && typeof prCountResult.value === 'number' && prCountResult.value > 0) {
            setRepoPRCounts((prev) => ({ ...prev, [repoId]: prCountResult.value }))
          }
        } else {
          const openErrMsg = openResult.reason instanceof Error ? openResult.reason.message : 'Failed to fetch open PRs'
          errors.push(`Open PRs: ${openErrMsg}`)
        }

        // Process CLOSED PRs
        let deduplicatedClosed: GitHubPR[] = []
        if (closedResult.status === 'fulfilled') {
          const res = closedResult.value
          const closedItems = res.items || []
          // Replace cached dataset cleanly, deduplicating by canonical repoId + ":" + item.number
          const seen = new Set<string>()
          for (const item of closedItems) {
            const canonicalKey = `${repoId}:${item.number}`
            if (!seen.has(canonicalKey)) {
              seen.add(canonicalKey)
              deduplicatedClosed.push(item)
            }
          }

          setPullRequestsByRepoState((prev) => ({
            ...prev,
            [`${repoId}:closed`]: deduplicatedClosed,
          }))
          setPrMetaByRepo((prev) => ({
            ...prev,
            [`${repoId}:closed`]: { ...res, items: deduplicatedClosed },
          }))
        } else {
          const closedErrMsg = closedResult.reason instanceof Error ? closedResult.reason.message : 'Failed to fetch closed PRs'
          errors.push(`Closed PRs: ${closedErrMsg}`)
        }

        // If both succeeded, update pullRequestsByRepo with combined open + closed for search/convenience
        if (openResult.status === 'fulfilled' && closedResult.status === 'fulfilled') {
          const combined = [
            ...deduplicatedOpen,
            ...deduplicatedClosed,
          ]
          const seenCombined = new Set<string>()
          const dedupCombined: GitHubPR[] = []
          for (const p of combined) {
            const canonicalKey = `${repoId}:${p.number}`
            if (!seenCombined.has(canonicalKey)) {
              seenCombined.add(canonicalKey)
              dedupCombined.push(p)
            }
          }
          setPullRequestsByRepo((prev) => ({
            ...prev,
            [repoId]: dedupCombined,
          }))
        } else if (openResult.status === 'fulfilled') {
          setPullRequestsByRepo((prev) => ({
            ...prev,
            [repoId]: deduplicatedOpen,
          }))
        }

        // Also fetch reviews for repo without running Groq analysis
        fetchReviews(repoId, true).catch(() => [])

        // If there were partial or total errors, surface them without overwriting successful data
        if (errors.length > 0) {
          const fullErr = errors.join('; ')
          setError(fullErr)
          throw new Error(fullErr)
        }
      } finally {
        setSyncingRepos((prev) => {
          const next = { ...prev }
          delete next[repoId]
          return next
        })
        if (activeRepoRef.current?.id === repoId) {
          setIsSyncing(false)
        }
        delete syncInFlightRef.current[repoId]
      }
    })()

    syncInFlightRef.current[repoId] = syncPromise
    return syncPromise
  }, [fetchReviews, fetchRepoPRCount])

  // Explicit sync / refresh action
  const refreshRepos = useCallback(async (force = true) => {
    setIsSyncing(true)
    setError(null)
    try {
      if (!inFlightReposRef.current) {
        inFlightReposRef.current = listRepositories().finally(() => {
          inFlightReposRef.current = null
        })
      }
      const repos = await inFlightReposRef.current
      setRepositories(repos)

      let currentActive = activeRepoRef.current
      if (repos.length > 0) {
        if (!currentActive || !repos.some((r) => r.id === currentActive?.id)) {
          // Check saved preference
          const savedId = localStorage.getItem(ACTIVE_REPO_STORAGE_KEY)
          const matchedSaved = savedId ? repos.find((r) => r.id === savedId) : null
          currentActive = matchedSaved || repos[0]
          setActiveRepo(currentActive)
        }
        // If force sync was requested and we have an active repository, sync both open and closed PRs via syncRepository
        if (force && currentActive) {
          await syncRepository(currentActive.id)
        }
      } else {
        setActiveRepo(null)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to synchronize repositories')
    } finally {
      setIsSyncing(false)
      setIsLoading(false)
    }
  }, [setActiveRepo, syncRepository])

  const addRepository = useCallback((repo: ConnectedRepo) => {
    setRepositories((prev) => {
      if (prev.some((r) => r.id === repo.id)) {
        return prev.map((r) => (r.id === repo.id ? repo : r))
      }
      return [...prev, repo]
    })
  }, [])

  const removeRepository = useCallback((repoId: string) => {
    setRepositories((prev) => {
      const remaining = prev.filter((r) => r.id !== repoId)
      // If deleted repository was active, migrate activeRepo or clear it
      setActiveRepoState((currentActive) => {
        if (currentActive?.id === repoId) {
          const nextActive = remaining.length > 0 ? remaining[0] : null
          try {
            if (nextActive) {
              localStorage.setItem(ACTIVE_REPO_STORAGE_KEY, nextActive.id)
            } else {
              localStorage.removeItem(ACTIVE_REPO_STORAGE_KEY)
            }
          } catch {
            // ignore localStorage error
          }
          return nextActive
        }
        return currentActive
      })
      return remaining
    })

    // Clean up caches isolated strictly to this repoId
    setPullRequestsByRepo((prev) => {
      if (!(repoId in prev)) return prev
      const next = { ...prev }
      delete next[repoId]
      return next
    })
    setPullRequestsByRepoState((prev) => {
      const next = { ...prev }
      let changed = false
      for (const key of Object.keys(next)) {
        if (key.startsWith(`${repoId}:`)) {
          delete next[key]
          changed = true
        }
      }
      return changed ? next : prev
    })
    setPrMetaByRepo((prev) => {
      if (!(repoId in prev)) return prev
      const next = { ...prev }
      delete next[repoId]
      return next
    })
    setReviewsByRepo((prev) => {
      if (!(repoId in prev)) return prev
      const next = { ...prev }
      delete next[repoId]
      return next
    })
    setRepoPRCounts((prev) => {
      if (!(repoId in prev)) return prev
      const next = { ...prev }
      delete next[repoId]
      return next
    })
    setMemoryCache((prev) => {
      const next = { ...prev }
      let changed = false
      for (const key of Object.keys(next)) {
        if (key.startsWith(`${repoId}:`)) {
          delete next[key]
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [])

  // Engineering Memory Fetch with caching, local tab filtering, and in-flight deduplication
  const fetchRepositoryMemories = useCallback(async (
    repoId: string,
    category = 'all',
    query = '',
    force = false
  ): Promise<EngineeringMemory[]> => {
    if (!repoId) return []

    const cleanCategory = category || 'all'
    const cleanQuery = query.trim()
    const cacheKey = `${repoId}:${cleanCategory}:${cleanQuery}`
    const baseKey = `${repoId}:all:`

    // 1. If base key exists and user switched category without a search query, filter locally without network request!
    if (!force && !cleanQuery && cleanCategory !== 'all') {
      const baseCached = memoryCacheRef.current[baseKey]
      if (baseCached) {
        const filtered = baseCached.filter((m) => m.type === cleanCategory)
        setMemoryCache((prev) => ({ ...prev, [cacheKey]: filtered }))
        setMemoryRequestState((prev) => ({ ...prev, [cacheKey]: 'success' }))
        return filtered
      }
    }

    // 2. Cache hit check
    const cached = memoryCacheRef.current[cacheKey]
    if (!force && cached) {
      setMemoryRequestState((prev) => ({ ...prev, [cacheKey]: 'success' }))
      return cached
    }

    // 3. In-flight request deduplication check
    const inFlight = inFlightMemoriesRef.current[cacheKey]
    if (inFlight) {
      return inFlight
    }

    // 4. Mark request loading
    setMemoryRequestState((prev) => ({ ...prev, [cacheKey]: 'loading' }))
    setMemoryError((prev) => ({ ...prev, [cacheKey]: null }))

    const fetchPromise = (async () => {
      try {
        let memories: EngineeringMemory[] = []
        if (cleanQuery) {
          const res = await searchRepositoryMemories(repoId, cleanQuery)
          memories = res.memories || []
          if (cleanCategory !== 'all') {
            memories = memories.filter((m) => m.type === cleanCategory)
          }
        } else {
          const res = await listRepositoryMemories(
            repoId,
            cleanCategory === 'all' ? undefined : cleanCategory,
            50
          )
          memories = res.memories || []
        }

        const now = Date.now()
        setMemoryCache((prev) => ({ ...prev, [cacheKey]: memories }))
        setMemoryRequestState((prev) => ({ ...prev, [cacheKey]: 'success' }))
        setMemoryError((prev) => ({ ...prev, [cacheKey]: null }))
        setMemoryLastFetched((prev) => ({ ...prev, [cacheKey]: now }))
        return memories
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Memory unavailable: failed to query repository memory'
        setMemoryRequestState((prev) => ({ ...prev, [cacheKey]: 'error' }))
        setMemoryError((prev) => ({ ...prev, [cacheKey]: msg }))
        throw err
      } finally {
        delete inFlightMemoriesRef.current[cacheKey]
      }
    })()

    inFlightMemoriesRef.current[cacheKey] = fetchPromise
    return fetchPromise
  }, [])

  // Invalidate review and memory caches for a repository after a review completes
  const invalidateRepoReviewState = useCallback(async (repoId: string) => {
    if (!repoId) return
    // Invalidate cached reviews for this repo
    setReviewsByRepo((prev) => {
      const next = { ...prev }
      delete next[repoId]
      return next
    })
    delete inFlightReviewsRef.current[repoId]

    // Invalidate cached memory for this repo
    setMemoryCache((prev) => {
      const next = { ...prev }
      for (const k of Object.keys(next)) {
        if (k.startsWith(`${repoId}:`)) {
          delete next[k]
        }
      }
      return next
    })

    // Fetch fresh reviews and PR counts for dashboard
    await Promise.all([
      fetchReviews(repoId, true).catch(() => []),
      fetchRepoPRCount(repoId, true).catch(() => 0),
    ])
  }, [fetchReviews, fetchRepoPRCount])

  const invalidateRepoMemory = useCallback((repoId: string) => {
    if (!repoId) return
    setMemoryCache((prev) => {
      const next = { ...prev }
      for (const k of Object.keys(next)) {
        if (k.startsWith(`${repoId}:`)) {
          delete next[k]
        }
      }
      return next
    })
  }, [])

  const toggleSidebar = useCallback(() => {
    setSidebarOpen((prev) => !prev)
  }, [])

  // Synchronize repositories on mount and when authentication session changes
  useEffect(() => {
    let isMounted = true

    if (!sessionToken) {
      queueMicrotask(() => {
        if (!isMounted) return
        setRepositories([])
        setActiveRepoState(null)
        setPullRequestsByRepo({})
        setPullRequestsByRepoState({})
        setPrMetaByRepo({})
        setReviewsByRepo({})
        setRepoPRCounts({})
        setMemoryCache({})
        try {
          localStorage.removeItem(ACTIVE_REPO_STORAGE_KEY)
        } catch {
          // Ignore localStorage error
        }
        setIsLoading(false)
      })
      return () => {
        isMounted = false
      }
    }

    queueMicrotask(() => {
      if (isMounted) setIsLoading(true)
    })
    inFlightReposRef.current = listRepositories().finally(() => {
      inFlightReposRef.current = null
    })

    inFlightReposRef.current
      .then((repos) => {
        if (!isMounted) return
        setRepositories(repos)
        if (repos.length > 0) {
          let selected = repos[0]
          try {
            const savedId = localStorage.getItem(ACTIVE_REPO_STORAGE_KEY)
            if (savedId) {
              const found = repos.find((r) => r.id === savedId)
              if (found) selected = found
            }
          } catch {
            // Ignore storage access error
          }
          setActiveRepoState(selected)
          fetchReviews(selected.id, false).catch(() => [])
          fetchRepoPRCount(selected.id, false).catch(() => 0)
        } else {
          setActiveRepoState(null)
        }
      })
      .catch((err) => {
        if (!isMounted) return
        setError(err instanceof Error ? err.message : 'Failed to load repositories')
      })
      .finally(() => {
        if (isMounted) {
          setIsLoading(false)
        }
      })

    return () => {
      isMounted = false
    }
  }, [sessionToken, fetchReviews, fetchRepoPRCount])

  return (
    <RepoContext.Provider
      value={{
        repositories,
        activeRepo,
        setActiveRepo,
        isLoading,
        isLoadingRepositories: isLoading,
        isSyncing,
        error,
        activePR,
        setActivePR,
        pullRequestsByRepo,
        pullRequestsByRepoState,
        prMetaByRepo,
        fetchPullRequests,
        reviewsByRepo,
        fetchReviews,
        repoPRCounts,
        fetchRepoPRCount,
        syncRepository,
        syncingRepos,
        refreshRepos,
        addRepository,
        removeRepository,
        memoryCache,
        memoryRequestState,
        memoryError,
        memoryLastFetched,
        fetchRepositoryMemories,
        invalidateRepoReviewState,
        invalidateRepoMemory,
        isSidebarOpen,
        toggleSidebar,
        setSidebarOpen,
      }}
    >
      {children}
    </RepoContext.Provider>
  )
}
