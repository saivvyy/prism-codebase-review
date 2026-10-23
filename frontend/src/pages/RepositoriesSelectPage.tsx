import React, { useEffect, useState, useMemo, useCallback, useRef } from 'react'
import { useNavigate, useSearchParams, useLocation } from 'react-router-dom'
import {
  RepoIcon,
  SearchIcon,
  CheckIcon,
  SyncIcon,
  LockIcon,
  ArrowRightIcon,
  SignOutIcon,
  AlertIcon,
  StarIcon,
  XIcon,
} from '@primer/octicons-react'
import { useAuth } from '@/context/useAuth'
import { useRepo } from '@/context/useRepo'
import { PrismLogo } from '@/components/PrismLogo'
import {
  listUserGitHubRepositories,
  searchPublicRepositories,
  connectRepository,
} from '@/services/api'
import type { UserRepoItem } from '@/types'

type ActiveTab = 'user' | 'public'

export const RepositoriesSelectPage: React.FC = () => {
  const { user, logout } = useAuth()
  const { setActiveRepo, refreshRepos, addRepository } = useRepo()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const location = useLocation()

  // Explicit onboarding state: ONLY active immediately after a successful OAuth login handshake
  const isOnboarding = useMemo(() => {
    const hasSearchParam = searchParams.get('onboarding') === 'true' || searchParams.get('onboarding') === '1'
    const hasLocationState = Boolean((location.state as { fromOAuthOnboarding?: boolean } | null)?.fromOAuthOnboarding)
    let hasSessionFlag = false
    try {
      hasSessionFlag = sessionStorage.getItem('prism_oauth_onboarding_active') === 'true'
    } catch {
      hasSessionFlag = false
    }
    return hasSearchParam || hasLocationState || hasSessionFlag
  }, [searchParams, location.state])

  const [activeTab, setActiveTab] = useState<ActiveTab>('user')

  // --- Authenticated User Repositories State ---
  const [userRepos, setUserRepos] = useState<UserRepoItem[]>([])
  const [isUserLoading, setIsUserLoading] = useState<boolean>(true)
  const [userError, setUserError] = useState<string | null>(null)
  const [userPage, setUserPage] = useState<number>(1)
  const [hasMoreUserRepos, setHasMoreUserRepos] = useState<boolean>(true)
  const [isLoadingMoreUser, setIsLoadingMoreUser] = useState<boolean>(false)
  const [userFilterQuery, setUserFilterQuery] = useState<string>('')

  // --- Public Repositories Search State ---
  const [publicQuery, setPublicQuery] = useState<string>('')
  const [debouncedPublicQuery, setDebouncedPublicQuery] = useState<string>('')
  const [publicRepos, setPublicRepos] = useState<UserRepoItem[]>([])
  const [publicTotalCount, setPublicTotalCount] = useState<number | null>(null)
  const [isPublicLoading, setIsPublicLoading] = useState<boolean>(false)
  const [publicError, setPublicError] = useState<string | null>(null)
  const [publicPage, setPublicPage] = useState<number>(1)
  const [isLoadingMorePublic, setIsLoadingMorePublic] = useState<boolean>(false)

  // --- Connecting State Map & Local Cache ---
  const [connectingMap, setConnectingMap] = useState<Record<string, boolean>>({})
  const [avatarErrorMap, setAvatarErrorMap] = useState<Record<string, boolean>>({})
  const [headerAvatarError, setHeaderAvatarError] = useState<boolean>(false)

  // Cache to avoid refetching user repos on tab switches
  const userReposCacheRef = useRef<UserRepoItem[] | null>(null)

  // Debounce public search query by 350ms
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedPublicQuery(publicQuery.trim())
    }, 350)
    return () => clearTimeout(timer)
  }, [publicQuery])

  // --- Fetch Initial User Repositories ---
  const fetchUserRepos = useCallback(async (pageToFetch: number = 1, append: boolean = false) => {
    if (pageToFetch === 1) {
      setIsUserLoading(true)
      setUserError(null)
    } else {
      setIsLoadingMoreUser(true)
    }

    try {
      const items = await listUserGitHubRepositories(pageToFetch, 30)
      if (append) {
        setUserRepos((prev) => {
          const existingIds = new Set(prev.map((r) => r.id))
          const newItems = items.filter((r) => !existingIds.has(r.id))
          const combined = [...prev, ...newItems]
          userReposCacheRef.current = combined
          return combined
        })
      } else {
        setUserRepos(items)
        userReposCacheRef.current = items
      }
      setHasMoreUserRepos(items.length >= 30)
      setUserPage(pageToFetch)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load GitHub repositories.'
      setUserError(msg)
    } finally {
      setIsUserLoading(false)
      setIsLoadingMoreUser(false)
    }
  }, [])

  useEffect(() => {
    if (userReposCacheRef.current) {
      setUserRepos(userReposCacheRef.current)
      setIsUserLoading(false)
    } else {
      fetchUserRepos(1, false)
    }
  }, [fetchUserRepos])

  // --- Fetch Public Repositories on Debounced Query Change ---
  useEffect(() => {
    if (activeTab !== 'public' || !debouncedPublicQuery || debouncedPublicQuery.length < 2) {
      return
    }

    let ignore = false
    queueMicrotask(() => {
      if (!ignore) {
        setIsPublicLoading(true)
        setPublicError(null)
      }
    })

    searchPublicRepositories(debouncedPublicQuery, 1, 30)
      .then((res) => {
        if (!ignore) {
          setPublicRepos(res.items)
          setPublicTotalCount(res.total_count)
          setPublicPage(1)
        }
      })
      .catch((err) => {
        if (!ignore) {
          setPublicError(
            err instanceof Error ? err.message : 'Failed to search public repositories on GitHub.'
          )
        }
      })
      .finally(() => {
        if (!ignore) {
          setIsPublicLoading(false)
        }
      })

    return () => {
      ignore = true
    }
  }, [activeTab, debouncedPublicQuery])

  const handleLoadMorePublic = async () => {
    if (!debouncedPublicQuery || debouncedPublicQuery.length < 2) return
    setIsLoadingMorePublic(true)
    try {
      const nextPage = publicPage + 1
      const res = await searchPublicRepositories(debouncedPublicQuery, nextPage, 30)
      setPublicRepos((prev) => {
        const existingIds = new Set(prev.map((r) => r.id))
        const newItems = res.items.filter((r) => !existingIds.has(r.id))
        return [...prev, ...newItems]
      })
      setPublicTotalCount(res.total_count)
      setPublicPage(nextPage)
    } catch (err) {
      setPublicError(
        err instanceof Error ? err.message : 'Failed to load more public repositories.'
      )
    } finally {
      setIsLoadingMorePublic(false)
    }
  }

  const retryPublicSearch = () => {
    if (!debouncedPublicQuery || debouncedPublicQuery.length < 2) return
    setIsPublicLoading(true)
    setPublicError(null)
    searchPublicRepositories(debouncedPublicQuery, 1, 30)
      .then((res) => {
        setPublicRepos(res.items)
        setPublicTotalCount(res.total_count)
        setPublicPage(1)
      })
      .catch((err) => {
        setPublicError(
          err instanceof Error ? err.message : 'Failed to search public repositories on GitHub.'
        )
      })
      .finally(() => {
        setIsPublicLoading(false)
      })
  }

  // Filter user repos locally by search input
  const filteredUserRepos = useMemo(() => {
    if (!userFilterQuery.trim()) return userRepos
    const q = userFilterQuery.toLowerCase().trim()
    return userRepos.filter(
      (r) =>
        r.full_name.toLowerCase().includes(q) ||
        (r.description && r.description.toLowerCase().includes(q))
    )
  }, [userRepos, userFilterQuery])

  // Connect single repository and navigate directly to existing PRism workspace
  const handleConnect = async (repo: UserRepoItem) => {
    setConnectingMap((prev) => ({ ...prev, [repo.full_name]: true }))
    try {
      // Connects ONLY this single repository using canonical identity
      const connected = await connectRepository(repo.full_name)

      // Update state immediately without needing refresh
      const updateList = (list: UserRepoItem[]) =>
        list.map((r) =>
          r.id === repo.id
            ? { ...r, connected: true, connected_repo_id: connected.id }
            : r
        )

      setUserRepos(updateList)
      setPublicRepos(updateList)
      if (userReposCacheRef.current) {
        userReposCacheRef.current = updateList(userReposCacheRef.current)
      }

      // Add to RepoContext and activate it
      addRepository(connected)
      setActiveRepo(connected)
      await refreshRepos(false)

      // When onboarding, do NOT force immediate navigation to /pull-requests
      // Allow user to connect additional repositories or proceed via "Continue to Workspace"
      if (!isOnboarding) {
        navigate('/pull-requests')
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to connect repository.')
    } finally {
      setConnectingMap((prev) => ({ ...prev, [repo.full_name]: false }))
    }
  }

  // Navigate to existing workspace if already connected
  const handleOpenWorkspace = (repo: UserRepoItem) => {
    if (repo.connected) {
      try {
        sessionStorage.removeItem('prism_oauth_onboarding_active')
      } catch {
        // Ignore
      }
      setActiveRepo({
        id: repo.connected_repo_id || String(repo.id),
        full_name: repo.full_name,
        github_repo_id: repo.id,
        default_branch: repo.default_branch,
        webhook_active: false,
      })
      navigate('/pull-requests')
    }
  }

  // Continue to workspace:
  // Case 1: Fresh OAuth onboarding -> Global Workspace Overview (/workspace)
  // Case 2: Normal authenticated navigation -> preserve current workspace page/location, fallback to /workspace
  const handleContinueToWorkspace = () => {
    if (isOnboarding) {
      try {
        sessionStorage.removeItem('prism_oauth_onboarding_active')
      } catch {
        // Ignore
      }
      navigate('/workspace')
      return
    }

    // Case 2: User is already inside authenticated workspace
    const locationState = location.state as {
      returnTo?: string
      from?: { pathname?: string; search?: string; hash?: string }
    } | null

    const stateTarget =
      locationState?.returnTo ||
      (locationState?.from?.pathname
        ? `${locationState.from.pathname}${locationState.from.search || ''}${locationState.from.hash || ''}`
        : null)

    let savedLocation: string | null = null
    try {
      savedLocation = sessionStorage.getItem('prism_last_workspace_location')
    } catch {
      savedLocation = null
    }

    const candidate = stateTarget || savedLocation

    // Validate that candidate is an internal workspace route and not auth/landing/repo-select itself
    const isValidWorkspaceRoute = (path?: string | null): boolean => {
      if (!path || typeof path !== 'string') return false
      if (!path.startsWith('/')) return false
      if (
        path === '/' ||
        path === '/landing' ||
        path.startsWith('/auth') ||
        path.startsWith('/repositories/select')
      ) {
        return false
      }
      return true
    }

    if (isValidWorkspaceRoute(candidate)) {
      navigate(candidate!)
      return
    }

    // Edge Case: If genuinely no valid previous authenticated location exists, fall back to Global Workspace Overview (NEVER /pull-requests)
    navigate('/workspace')
  }

  const handleSignOut = async () => {
    try {
      sessionStorage.removeItem('prism_oauth_onboarding_active')
    } catch {
      // Ignore
    }
    await logout()
    navigate('/', { replace: true })
  }

  // Format access role badge text
  const formatAccessRole = (role?: string | null) => {
    if (!role) return null
    switch (role.toLowerCase()) {
      case 'owner':
        return 'Owner'
      case 'admin':
        return 'Admin'
      case 'maintainer':
        return 'Maintainer'
      case 'write':
        return 'Write'
      case 'read':
        return 'Read'
      case 'organization_member':
        return 'Org Member'
      case 'collaborator':
        return 'Collaborator'
      default:
        return null
    }
  }

  return (
    <div className="min-h-screen bg-[#0d1117] text-[#c9d1d9] font-sans selection:bg-[#1f6feb]/30 selection:text-[#58a6ff] flex flex-col">
      {/* 1. COMPACT GITHUB-STYLE HEADER */}
      <header className="h-14 border-b border-[#30363d] bg-[#010409] px-4 sm:px-8 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <PrismLogo size="md" />
          <div className="flex items-baseline gap-2">
            <span className="font-semibold text-sm tracking-tight text-[#f0f6fc]">PRism</span>
            <span className="text-xs text-[#8b949e]">/</span>
            <span className="text-xs text-[#8b949e]">Repository Connection</span>
          </div>
        </div>

        {/* Authenticated User Identity & Logout */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 px-2.5 py-1 rounded-full bg-[#161b22] border border-[#30363d]">
            <div className="w-5 h-5 rounded-full overflow-hidden bg-[#21262d] border border-[#30363d] flex items-center justify-center text-[10px] text-[#c9d1d9] font-medium shrink-0">
              {user?.avatar_url && !headerAvatarError ? (
                <img
                  src={user.avatar_url}
                  alt={user.login}
                  className="w-full h-full object-cover"
                  onError={() => setHeaderAvatarError(true)}
                />
              ) : (
                <span>{user?.login ? user.login.substring(0, 2).toUpperCase() : 'GH'}</span>
              )}
            </div>
            <span className="text-xs font-medium text-[#c9d1d9]">
              @{user?.login || 'authenticated'}
            </span>
          </div>

          <button
            type="button"
            onClick={handleSignOut}
            className="p-1.5 rounded-md border border-[#30363d] bg-[#21262d] text-[#8b949e] hover:text-[#f85149] hover:bg-[#30363d] transition-colors cursor-pointer"
            title="Sign out"
            aria-label="Sign out"
          >
            <SignOutIcon size={14} />
          </button>
        </div>
      </header>

      {/* 2. MAIN SELECTION CONTAINER */}
      <main className="flex-1 max-w-4xl w-full mx-auto px-4 sm:px-6 py-8 flex flex-col gap-6">
        {/* Header Block with Optional Skip Action */}
        <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4 pb-2 border-b border-[#21262d]/60">
          <div className="space-y-1">
            <h1 className="text-2xl font-bold tracking-tight text-[#f0f6fc]">
              Choose a repository
            </h1>
            <p className="text-sm text-[#8b949e]">
              Connect a repository to start reviewing pull requests.
            </p>
          </div>

          <div className="flex flex-col sm:items-end gap-1.5 shrink-0">
            <button
              type="button"
              onClick={handleContinueToWorkspace}
              className="inline-flex items-center justify-center gap-2 px-3.5 py-1.5 rounded-md bg-[#238636] hover:bg-[#2ea043] border border-[rgba(240,246,252,0.15)] text-xs font-semibold text-white transition-colors cursor-pointer group shadow-xs focus:outline-none focus:ring-2 focus:ring-[#3fb950]"
            >
              <span>Continue to Workspace</span>
              <ArrowRightIcon size={14} className="text-white/80 group-hover:text-white group-hover:translate-x-0.5 transition-all" />
            </button>
            <span className="text-[11px] text-[#8b949e]">
              You can connect repositories now or do it later from your workspace.
            </span>
          </div>
        </div>

        {/* Section Tabs (Primer style) */}
        <div className="flex items-center border-b border-[#30363d] gap-2">
          <button
            type="button"
            onClick={() => setActiveTab('user')}
            className={`px-4 py-2.5 text-xs font-semibold flex items-center gap-2 border-b-2 transition-colors cursor-pointer ${
              activeTab === 'user'
                ? 'border-[#f78166] text-[#f0f6fc]'
                : 'border-transparent text-[#8b949e] hover:text-[#c9d1d9]'
            }`}
          >
            <span>Your repositories</span>
            {userRepos.length > 0 && (
              <span className="px-1.5 py-0.2 rounded-full text-[11px] bg-[#21262d] text-[#8b949e] border border-[#30363d]">
                {userRepos.length}
              </span>
            )}
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('public')}
            className={`px-4 py-2.5 text-xs font-semibold flex items-center gap-2 border-b-2 transition-colors cursor-pointer ${
              activeTab === 'public'
                ? 'border-[#f78166] text-[#f0f6fc]'
                : 'border-transparent text-[#8b949e] hover:text-[#c9d1d9]'
            }`}
          >
            <span>Public repositories</span>
            {publicTotalCount !== null && (
              <span className="px-1.5 py-0.2 rounded-full text-[11px] bg-[#21262d] text-[#8b949e] border border-[#30363d]">
                {publicTotalCount > 999 ? '999+' : publicTotalCount}
              </span>
            )}
          </button>
        </div>

        {/* SEARCH BAR */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
          <div className="relative flex-1">
            <SearchIcon
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-[#7d8590] pointer-events-none"
            />
            {activeTab === 'user' ? (
              <input
                type="text"
                value={userFilterQuery}
                onChange={(e) => setUserFilterQuery(e.target.value)}
                placeholder="Search repositories…"
                className="w-full h-9 pl-9 pr-8 rounded-md bg-[#161b22] border border-[#30363d] text-xs text-[#f0f6fc] placeholder-[#7d8590] focus:outline-none focus:border-[#1f6feb] transition-colors"
              />
            ) : (
              <input
                type="text"
                value={publicQuery}
                onChange={(e) => setPublicQuery(e.target.value)}
                placeholder="Search GitHub repositories…"
                className="w-full h-9 pl-9 pr-8 rounded-md bg-[#161b22] border border-[#30363d] text-xs text-[#f0f6fc] placeholder-[#7d8590] focus:outline-none focus:border-[#1f6feb] transition-colors"
              />
            )}
            {((activeTab === 'user' && userFilterQuery) || (activeTab === 'public' && publicQuery)) && (
              <button
                type="button"
                onClick={() => (activeTab === 'user' ? setUserFilterQuery('') : setPublicQuery(''))}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#7d8590] hover:text-[#c9d1d9] cursor-pointer"
                title="Clear search"
              >
                <XIcon size={14} />
              </button>
            )}
          </div>

          {activeTab === 'user' && (
            <button
              type="button"
              onClick={() => fetchUserRepos(1, false)}
              disabled={isUserLoading}
              className="px-3 h-9 rounded-md bg-[#21262d] border border-[#30363d] hover:bg-[#30363d] text-xs font-medium text-[#c9d1d9] flex items-center justify-center gap-1.5 cursor-pointer disabled:opacity-50 shrink-0"
              title="Refresh list from GitHub"
            >
              <SyncIcon size={14} className={isUserLoading ? 'animate-spin text-[#58a6ff]' : ''} />
              <span>Refresh</span>
            </button>
          )}
        </div>

        {/* RESULTS METADATA BANNER (FOR PUBLIC SEARCH) */}
        {activeTab === 'public' && publicTotalCount !== null && debouncedPublicQuery && !isPublicLoading && (
          <div className="text-xs text-[#8b949e] px-1 flex items-center justify-between">
            <span>
              Found <strong className="text-[#f0f6fc]">{publicTotalCount.toLocaleString()}</strong> public repositories on GitHub
            </span>
            <span>
              Showing {publicRepos.length} results
            </span>
          </div>
        )}

        {/* REPOSITORY LIST CONTAINER */}
        <div className="bg-[#161b22] border border-[#30363d] rounded-md overflow-hidden shadow-sm">
          {/* TAB 1: YOUR REPOSITORIES */}
          {activeTab === 'user' && (
            <>
              {isUserLoading && userRepos.length === 0 ? (
                <div className="py-16 text-center space-y-3">
                  <SyncIcon size={24} className="animate-spin text-[#58a6ff] mx-auto" />
                  <div className="text-xs text-[#8b949e]">
                    Loading repositories accessible through your GitHub account…
                  </div>
                </div>
              ) : userError ? (
                <div className="py-12 px-6 text-center space-y-3">
                  <AlertIcon size={24} className="text-[#f85149] mx-auto" />
                  <div className="text-sm font-semibold text-[#f0f6fc]">Unable to load repositories</div>
                  <p className="text-xs text-[#8b949e] max-w-md mx-auto">{userError}</p>
                  <button
                    type="button"
                    onClick={() => fetchUserRepos(1, false)}
                    className="mt-2 px-3 py-1.5 rounded-md bg-[#21262d] border border-[#30363d] text-xs font-medium text-[#c9d1d9] hover:bg-[#30363d] cursor-pointer"
                  >
                    Try Again
                  </button>
                </div>
              ) : filteredUserRepos.length === 0 ? (
                <div className="py-16 text-center space-y-2 text-[#8b949e]">
                  <RepoIcon size={24} className="mx-auto text-[#656c76]" />
                  <div className="text-xs font-medium">
                    {userFilterQuery
                      ? 'No repositories match your filter'
                      : 'No repositories found for this account'}
                  </div>
                </div>
              ) : (
                <div className="divide-y divide-[#21262d]">
                  {filteredUserRepos.map((repo) => renderRepoRow(repo))}
                </div>
              )}

              {/* Progressive Pagination for User Repos */}
              {hasMoreUserRepos && !userFilterQuery && !isUserLoading && (
                <div className="p-3 border-t border-[#21262d] bg-[#161b22] text-center">
                  <button
                    type="button"
                    onClick={() => fetchUserRepos(userPage + 1, true)}
                    disabled={isLoadingMoreUser}
                    className="px-4 py-1.5 rounded-md bg-[#21262d] border border-[#30363d] hover:bg-[#30363d] text-xs font-medium text-[#58a6ff] flex items-center justify-center gap-1.5 mx-auto cursor-pointer disabled:opacity-50"
                  >
                    {isLoadingMoreUser ? (
                      <>
                        <SyncIcon size={12} className="animate-spin" />
                        <span>Loading more…</span>
                      </>
                    ) : (
                      <span>Load more repositories</span>
                    )}
                  </button>
                </div>
              )}
            </>
          )}

          {/* TAB 2: PUBLIC REPOSITORIES SEARCH */}
          {activeTab === 'public' && (
            <>
              {!debouncedPublicQuery || debouncedPublicQuery.length < 2 ? (
                <div className="py-16 text-center space-y-2 text-[#8b949e]">
                  <SearchIcon size={24} className="mx-auto text-[#656c76]" />
                  <div className="text-xs font-medium text-[#c9d1d9]">
                    Search public GitHub repositories
                  </div>
                  <div className="text-xs text-[#8b949e]">
                    Enter at least 2 characters to search across open source projects.
                  </div>
                </div>
              ) : isPublicLoading && publicRepos.length === 0 ? (
                <div className="py-16 text-center space-y-3">
                  <SyncIcon size={24} className="animate-spin text-[#58a6ff] mx-auto" />
                  <div className="text-xs text-[#8b949e]">
                    Searching GitHub for &ldquo;{debouncedPublicQuery}&rdquo;…
                  </div>
                </div>
              ) : publicError ? (
                <div className="py-12 px-6 text-center space-y-3">
                  <AlertIcon size={24} className="text-[#f85149] mx-auto" />
                  <div className="text-sm font-semibold text-[#f0f6fc]">GitHub Search Error</div>
                  <p className="text-xs text-[#8b949e] max-w-md mx-auto">{publicError}</p>
                  {publicError.toLowerCase().includes('rate limit') && (
                    <div className="text-xs text-[#d29922] bg-[#d29922]/10 border border-[#d29922]/20 p-2 rounded-md max-w-sm mx-auto">
                      GitHub rate limit encountered. Wait a few moments before trying again.
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={retryPublicSearch}
                    className="mt-2 px-3 py-1.5 rounded-md bg-[#21262d] border border-[#30363d] text-xs font-medium text-[#c9d1d9] hover:bg-[#30363d] cursor-pointer"
                  >
                    Try Again
                  </button>
                </div>
              ) : publicRepos.length === 0 ? (
                <div className="py-16 text-center space-y-2 text-[#8b949e]">
                  <RepoIcon size={24} className="mx-auto text-[#656c76]" />
                  <div className="text-xs font-medium">
                    No public repositories found matching &ldquo;{debouncedPublicQuery}&rdquo;
                  </div>
                </div>
              ) : (
                <div className="divide-y divide-[#21262d]">
                  {publicRepos.map((repo) => renderRepoRow(repo))}
                </div>
              )}

              {/* Progressive Pagination for Public Search */}
              {publicRepos.length > 0 && publicTotalCount !== null && publicRepos.length < publicTotalCount && !isPublicLoading && (
                <div className="p-3 border-t border-[#21262d] bg-[#161b22] text-center">
                  <button
                    type="button"
                    onClick={handleLoadMorePublic}
                    disabled={isLoadingMorePublic}
                    className="px-4 py-1.5 rounded-md bg-[#21262d] border border-[#30363d] hover:bg-[#30363d] text-xs font-medium text-[#58a6ff] flex items-center justify-center gap-1.5 mx-auto cursor-pointer disabled:opacity-50"
                  >
                    {isLoadingMorePublic ? (
                      <>
                        <SyncIcon size={12} className="animate-spin" />
                        <span>Loading more…</span>
                      </>
                    ) : (
                      <span>Load more public results</span>
                    )}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </main>
    </div>
  )

  // --- RENDER COMPACT GITHUB-STYLE REPOSITORY ROW ---
  function renderRepoRow(repo: UserRepoItem) {
    const isConnecting = !!connectingMap[repo.full_name]
    const roleText = formatAccessRole(repo.access_role)
    const isOwnerAvatarBroken = !!avatarErrorMap[repo.full_name]

    return (
      <div
        key={repo.id}
        className="p-3.5 sm:p-4 flex items-center justify-between gap-4 hover:bg-[#1c2128] transition-colors"
      >
        {/* Left: Avatar + Details */}
        <div className="min-w-0 flex items-start gap-3 flex-1">
          {/* Owner Avatar */}
          <div className="w-6 h-6 rounded-md overflow-hidden bg-[#21262d] border border-[#30363d] flex items-center justify-center shrink-0 mt-0.5">
            {repo.owner_avatar_url && !isOwnerAvatarBroken ? (
              <img
                src={repo.owner_avatar_url}
                alt={repo.owner}
                className="w-full h-full object-cover"
                onError={() =>
                  setAvatarErrorMap((prev) => ({ ...prev, [repo.full_name]: true }))
                }
              />
            ) : (
              <RepoIcon size={12} className="text-[#8b949e]" />
            )}
          </div>

          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold text-xs sm:text-sm text-[#58a6ff] hover:underline cursor-pointer truncate" onClick={() => repo.connected ? handleOpenWorkspace(repo) : handleConnect(repo)}>
                {repo.full_name}
              </span>

              {/* Visibility Badge */}
              {repo.private ? (
                <span className="inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.2 rounded-full border border-[#30363d] text-[#8b949e] bg-[#0d1117]">
                  <LockIcon size={10} />
                  <span>Private</span>
                </span>
              ) : (
                <span className="text-[10px] font-medium px-1.5 py-0.2 rounded-full border border-[#30363d] text-[#8b949e] bg-[#0d1117]">
                  Public
                </span>
              )}

              {/* Access Context Badge (when available) */}
              {roleText && (
                <span className="text-[10px] font-medium px-1.5 py-0.2 rounded-full border border-[#388bfd]/30 text-[#58a6ff] bg-[#388bfd]/10">
                  {roleText}
                </span>
              )}
            </div>

            {/* Description */}
            {repo.description && (
              <p className="text-xs text-[#8b949e] line-clamp-1 max-w-xl">
                {repo.description}
              </p>
            )}

            {/* Secondary Metadata */}
            <div className="text-[11px] text-[#656c76] flex items-center gap-3">
              {repo.stargazers_count !== undefined && repo.stargazers_count !== null && repo.stargazers_count > 0 && (
                <span className="inline-flex items-center gap-1 hover:text-[#8b949e]">
                  <StarIcon size={11} />
                  <span>{repo.stargazers_count.toLocaleString()}</span>
                </span>
              )}
              <span>Branch: {repo.default_branch}</span>
            </div>
          </div>
        </div>

        {/* Right: Connect or Connected Action */}
        <div className="shrink-0">
          {repo.connected ? (
            <button
              type="button"
              onClick={() => handleOpenWorkspace(repo)}
              className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md bg-[#238636]/15 hover:bg-[#238636]/25 border border-[#238636]/40 text-xs font-semibold text-[#3fb950] transition-colors cursor-pointer"
              title="Already connected. Open pull requests workspace."
            >
              <CheckIcon size={14} />
              <span>Connected</span>
              <ArrowRightIcon size={12} className="opacity-70" />
            </button>
          ) : (
            <button
              type="button"
              onClick={() => handleConnect(repo)}
              disabled={isConnecting}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[#21262d] hover:bg-[#238636] border border-[#30363d] hover:border-[rgba(240,246,252,0.15)] text-xs font-semibold text-[#f0f6fc] transition-colors cursor-pointer disabled:opacity-50"
            >
              {isConnecting ? (
                <>
                  <SyncIcon size={12} className="animate-spin text-[#58a6ff]" />
                  <span>Connecting…</span>
                </>
              ) : (
                <span>Connect</span>
              )}
            </button>
          )}
        </div>
      </div>
    )
  }
}
