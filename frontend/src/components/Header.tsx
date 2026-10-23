import React, { useState, useEffect, useRef, useMemo } from 'react'
import { NavLink, useNavigate, useLocation } from 'react-router-dom'
import {
  ThreeBarsIcon,
  RepoIcon,
  TriangleDownIcon,
  SearchIcon,
  SyncIcon,
  BellIcon,
  PlusIcon,
  CheckIcon,
  CodeIcon,
  GitPullRequestIcon,
  CommentDiscussionIcon,
  GearIcon,
  XCircleIcon,
  CheckCircleIcon,
  SignOutIcon,
} from '@primer/octicons-react'
import { useRepo } from '@/context/useRepo'
import { useAuth } from '@/context/useAuth'
import { PrismLogo } from '@/components/PrismLogo'

function formatCompactCount(num: number): string {
  if (num >= 1000000) {
    return `${(num / 1000000).toFixed(1).replace(/\.0$/, '')}M`
  }
  if (num >= 1000) {
    return `${(num / 1000).toFixed(1).replace(/\.0$/, '')}k`
  }
  return num.toString()
}

export const Header: React.FC = () => {
  const {
    repositories,
    activeRepo,
    setActiveRepo,
    syncRepository,
    syncingRepos,
    refreshRepos,
    isSyncing,
    pullRequestsByRepo,
    prMetaByRepo,
    repoPRCounts,
    reviewsByRepo,
    toggleSidebar,
  } = useRepo()
  const { user, logout } = useAuth()

  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [repoFilter, setRepoFilter] = useState('')
  const [notifOpen, setNotifOpen] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  const [avatarError, setAvatarError] = useState(false)

  const userDisplayName = user?.name || user?.login || 'engineering-team'
  const userLogin = user?.login ? `@${user.login}` : 'engineering-team'
  const userAvatarUrl = user?.avatar_url || 'https://github.com/github.png'
  const userInitials = user?.login ? user.login.substring(0, 2).toUpperCase() : 'GH'

  // Search state
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)

  const searchInputRef = useRef<HTMLInputElement>(null)
  const searchContainerRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const location = useLocation()

  // Extract owner and repo name from activeRepo
  const repoParts = activeRepo?.full_name ? activeRepo.full_name.split('/') : []
  const repoOwner = repoParts[0] || ''
  const repoName = repoParts[1] || ''

  // Header Pull Requests badge: repository's ACTUAL OPEN GitHub PR count
  // Priority: 1. repoPRCounts map (fetched from GitHub metadata GET /repos/{owner}/{repo}/pulls?state=open&per_page=1 via link header)
  //           2. activeRepo.open_pr_count ?? activeRepo.pull_requests_count (if already provided by repo metadata)
  //           3. null (do NOT fall back to openPRs.length or loaded/cached length)
  const actualPRCount = activeRepo
    ? (repoPRCounts[activeRepo.id] ?? activeRepo.open_pr_count ?? activeRepo.pull_requests_count ?? null)
    : null
  const activePrMeta = activeRepo ? prMetaByRepo[activeRepo.id] : null
  const syncTooltip = isSyncing
    ? 'Syncing repository data...'
    : activePrMeta
      ? `Sync repository pull requests (${activePrMeta.total_fetched} loaded${activePrMeta.truncated ? ', 500 limit reached' : ''})`
      : 'Sync repository and pull requests'

  // Keyboard shortcut listener ('/' to search, 'Escape' to close modals)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes((e.target as HTMLElement).tagName)) {
        e.preventDefault()
        setSearchOpen(true)
        setTimeout(() => searchInputRef.current?.focus(), 50)
      }
      if (e.key === 'Escape') {
        setSearchOpen(false)
        setDropdownOpen(false)
        setNotifOpen(false)
        setProfileOpen(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  // Search results compilation across active repo PRs and connected repositories
  const searchResults = useMemo(() => {
    if (!searchQuery.trim()) return []

    const q = searchQuery.toLowerCase().trim()
    const results: Array<{
      id: string
      type: 'repo' | 'pr' | 'nav'
      title: string
      subtitle?: string
      state?: string
      url: string
      action: () => void
    }> = []

    // 1. Repositories match
    for (const r of repositories) {
      if (r.full_name.toLowerCase().includes(q)) {
        results.push({
          id: `repo-${r.id}`,
          type: 'repo',
          title: r.full_name,
          subtitle: 'Connected repository',
          url: '/pull-requests',
          action: () => {
            setActiveRepo(r)
            navigate('/pull-requests')
          },
        })
      }
    }

    // 2. Pull requests match
    if (activeRepo) {
      const prs = pullRequestsByRepo[activeRepo.id] || []
      for (const pr of prs) {
        if (
          pr.title.toLowerCase().includes(q) ||
          String(pr.number).includes(q) ||
          pr.author.toLowerCase().includes(q)
        ) {
          results.push({
            id: `pr-${pr.number}`,
            type: 'pr',
            title: `#${pr.number} ${pr.title}`,
            subtitle: `by ${pr.author} • ${pr.state}`,
            state: pr.state,
            url: `/pull-requests/${activeRepo.id}/${pr.number}`,
            action: () => {
              navigate(`/pull-requests/${activeRepo.id}/${pr.number}`)
            },
          })
        }
      }
    }

    // 3. Navigation shortcuts
    const navItems = [
      { name: 'Repository Overview', path: '/overview' },
      { name: 'Connected Repositories', path: '/repositories' },
      { name: 'Pull Requests', path: '/pull-requests' },
      { name: 'Engineering Memory Explorer', path: '/memory' },
      { name: 'Settings & Diagnostics', path: '/settings' },
    ]
    for (const item of navItems) {
      if (item.name.toLowerCase().includes(q)) {
        results.push({
          id: `nav-${item.path}`,
          type: 'nav',
          title: item.name,
          subtitle: 'Navigation destination',
          url: item.path,
          action: () => navigate(item.path),
        })
      }
    }

    return results.slice(0, 8)
  }, [searchQuery, repositories, activeRepo, pullRequestsByRepo, navigate, setActiveRepo])

  // Handle arrow key and enter in search box
  const handleSearchKeyDown = (e: React.KeyboardEvent) => {
    if (searchResults.length === 0) return

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIndex((prev) => (prev + 1) % searchResults.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIndex((prev) => (prev - 1 + searchResults.length) % searchResults.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const selected = searchResults[selectedIndex]
      if (selected) {
        selected.action()
        setSearchOpen(false)
        setSearchQuery('')
      }
    }
  }

  // Filter repositories for dropdown
  const filteredRepos = useMemo(() => {
    if (!repoFilter.trim()) return repositories
    return repositories.filter((r) => r.full_name.toLowerCase().includes(repoFilter.toLowerCase().trim()))
  }, [repositories, repoFilter])

  // Notifications from active repository reviews
  const activeReviews = activeRepo ? (reviewsByRepo[activeRepo.id] || []) : []
  const driftReviews = activeReviews.filter((r) => r.drift_detected)

  return (
    <header className="border-b border-[#21262d] bg-[#010409] text-[#f0f6fc] sticky top-0 z-30 select-none">
      {/* 1. TOP GLOBAL HEADER */}
      <div className="h-14 px-4 flex items-center justify-between gap-3 text-sm">
        {/* Left: Hamburger, GitHub Mark, Breadcrumbs & Repo Switcher */}
        <div className="flex items-center gap-2.5 min-w-0">
          <button
            type="button"
            onClick={toggleSidebar}
            className="p-1.5 rounded-md border border-[#30363d] bg-transparent text-[#9198a1] hover:text-[#f0f6fc] hover:bg-[#21262d] transition-colors cursor-pointer"
            aria-label="Toggle navigation sidebar"
            title="Toggle sidebar"
          >
            <ThreeBarsIcon size={16} />
          </button>

          <NavLink
            to="/workspace"
            className="header-brand-link flex items-center gap-2 text-[#f0f6fc] hover:opacity-90 transition-opacity shrink-0 no-underline hover:no-underline"
            title="PRism Workspace Overview"
          >
            <PrismLogo size="md" />
            <span className="font-semibold text-xs text-[#f0f6fc] tracking-tight hidden sm:inline-block">
              PRism
            </span>
          </NavLink>

          {/* Repo Breadcrumb */}
          <div className="flex items-center gap-1.5 text-sm min-w-0 pl-1">
            {activeRepo ? (
              <>
                <span
                  onClick={() => navigate('/overview')}
                  className="text-[#9198a1] hover:underline cursor-pointer truncate"
                  title={`Owner: ${repoOwner}`}
                >
                  {repoOwner}
                </span>
                <span className="text-[#656c76]">/</span>
                <span
                  onClick={() => navigate('/overview')}
                  className="font-semibold text-[#f0f6fc] hover:underline cursor-pointer truncate"
                  title={`Repository: ${repoName}`}
                >
                  {repoName}
                </span>

                {/* Public/Private Badge */}
                <span className="text-[11px] font-medium px-2 py-0.5 rounded-full border border-[#30363d] text-[#9198a1] bg-[#161b22] shrink-0 ml-1">
                  Public
                </span>
              </>
            ) : (
              <span
                onClick={() => navigate('/repositories/select', { state: { returnTo: location.pathname + location.search + location.hash } })}
                className="text-xs text-[#8b949e] hover:text-[#f0f6fc] cursor-pointer"
              >
                No repository connected
              </span>
            )}

            {/* Repository Switcher Dropdown */}
            <div className="relative shrink-0 ml-1">
              <button
                type="button"
                onClick={() => {
                  setDropdownOpen(!dropdownOpen)
                  setNotifOpen(false)
                  setProfileOpen(false)
                }}
                className="p-1 rounded-md text-[#9198a1] hover:text-[#f0f6fc] hover:bg-[#21262d] transition-colors cursor-pointer flex items-center"
                title="Switch repository"
                aria-expanded={dropdownOpen}
              >
                <TriangleDownIcon size={14} />
              </button>

              {dropdownOpen && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setDropdownOpen(false)} />
                  <div className="absolute left-0 mt-1.5 w-80 rounded-md bg-[#161b22] border border-[#30363d] shadow-2xl py-1 z-50 text-xs">
                    <div className="px-3 py-2 text-[11px] font-semibold text-[#9198a1] border-b border-[#30363d] flex items-center justify-between">
                      <span>Switch repository</span>
                    </div>

                    <div className="p-2 border-b border-[#30363d]">
                      <input
                        type="text"
                        value={repoFilter}
                        onChange={(e) => setRepoFilter(e.target.value)}
                        placeholder="Find a repository..."
                        className="w-full px-2.5 py-1 bg-[#0d1117] border border-[#30363d] rounded text-xs text-[#f0f6fc] placeholder-[#656c76] focus:outline-none focus:border-[#1f6feb]"
                        autoFocus
                      />
                    </div>

                    <div className="max-h-60 overflow-y-auto py-1">
                      {filteredRepos.length === 0 ? (
                        <div className="px-3 py-3 text-[#9198a1] text-center italic">
                          No repositories found
                        </div>
                      ) : (
                        filteredRepos.map((repo) => (
                          <button
                            key={repo.id}
                            type="button"
                            onClick={() => {
                              setActiveRepo(repo)
                              setDropdownOpen(false)
                              setRepoFilter('')
                            }}
                            className={`w-full text-left px-3 py-2 flex items-center justify-between hover:bg-[#21262d] transition-colors cursor-pointer ${
                              activeRepo?.id === repo.id
                                ? 'bg-[#21262d] text-[#4493f8] font-semibold'
                                : 'text-[#c9d1d9]'
                            }`}
                          >
                            <div className="flex items-center gap-2 truncate">
                              <RepoIcon size={14} className="text-[#9198a1] shrink-0" />
                              <span className="truncate">{repo.full_name}</span>
                            </div>
                            {activeRepo?.id === repo.id && (
                              <CheckIcon size={14} className="text-[#4493f8] shrink-0" />
                            )}
                          </button>
                        ))
                      )}
                    </div>

                    <div className="border-t border-[#30363d] pt-1 px-1">
                      <button
                        type="button"
                        onClick={() => {
                          setDropdownOpen(false)
                          navigate('/repositories/select', { state: { returnTo: location.pathname + location.search + location.hash } })
                        }}
                        className="w-full text-left px-2.5 py-1.5 rounded flex items-center gap-2 text-[#4493f8] hover:bg-[#21262d] cursor-pointer font-medium"
                      >
                        <PlusIcon size={14} />
                        <span>Connect a new repository</span>
                      </button>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        {/* Center: Global Search Bar */}
        <div ref={searchContainerRef} className="relative hidden md:flex items-center flex-1 max-w-[400px] mx-3 lg:mx-4">
          <div className="w-full relative flex items-center h-8">
            <SearchIcon size={16} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[#7d8590] pointer-events-none shrink-0" />
            <input
              ref={searchInputRef}
              type="text"
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value)
                setSelectedIndex(0)
                setSearchOpen(true)
              }}
              onFocus={() => setSearchOpen(true)}
              onKeyDown={handleSearchKeyDown}
              placeholder="Type / to search"
              className="w-full h-8 bg-[#161b22] hover:bg-[#161b22] border border-[#30363d] hover:border-[#8b949e] rounded-md pl-[34px] pr-8 text-[13px] font-sans text-[#f0f6fc] placeholder-[#7d8590] focus:outline-none focus:border-[#1f6feb] focus:ring-1 focus:ring-[#1f6feb] focus:bg-[#0d1117] transition-all"
            />
            <span className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center justify-center min-w-[18px] h-5 px-1.5 rounded-[4px] border border-[#30363d] bg-[#21262d] text-[11px] font-sans font-medium text-[#7d8590] leading-none select-none pointer-events-none">
              /
            </span>
          </div>

          {/* Search Results Dropdown */}
          {searchOpen && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setSearchOpen(false)} />
              <div className="absolute top-full left-0 right-0 mt-1.5 rounded-md bg-[#161b22] border border-[#30363d] shadow-2xl py-1 z-50 text-xs max-h-80 overflow-y-auto">
                {searchResults.length === 0 ? (
                  <div className="p-3 text-center text-[#8b949e] italic">
                    {searchQuery.trim() ? 'No matching repositories or pull requests' : 'Type to search PRs, repositories, or pages...'}
                  </div>
                ) : (
                  searchResults.map((item, index) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => {
                        item.action()
                        setSearchOpen(false)
                        setSearchQuery('')
                      }}
                      className={`w-full text-left px-3 py-2 flex items-center justify-between transition-colors cursor-pointer ${
                        index === selectedIndex ? 'bg-[#21262d] text-[#f0f6fc]' : 'text-[#c9d1d9] hover:bg-[#21262d]'
                      }`}
                    >
                      <div className="flex items-center gap-2 truncate">
                        {item.type === 'repo' && <RepoIcon size={14} className="text-[#8b949e] shrink-0" />}
                        {item.type === 'pr' && <GitPullRequestIcon size={14} className="text-[#3fb950] shrink-0" />}
                        {item.type === 'nav' && <CodeIcon size={14} className="text-[#4493f8] shrink-0" />}
                        <div className="truncate">
                          <div className="font-medium truncate">{item.title}</div>
                          {item.subtitle && <div className="text-[10px] text-[#8b949e]">{item.subtitle}</div>}
                        </div>
                      </div>
                    </button>
                  ))
                )}
              </div>
            </>
          )}
        </div>

        {/* Right: Actions, Sync, Notification, Avatar */}
        <div className="flex items-center gap-2 shrink-0">
          {/* Single Primary Repository Sync */}
          <button
            type="button"
            onClick={() => {
              if (activeRepo) {
                syncRepository(activeRepo.id).catch(() => {})
              } else {
                refreshRepos(true).catch(() => {})
              }
            }}
            disabled={activeRepo ? !!syncingRepos[activeRepo.id] : isSyncing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[#21262d] border border-[#30363d] hover:bg-[#30363d] hover:border-[#8b949e] text-xs font-medium text-[#f0f6fc] disabled:opacity-60 disabled:cursor-not-allowed transition-colors cursor-pointer"
            title={syncTooltip}
          >
            <SyncIcon
              size={14}
              className={`${(activeRepo ? !!syncingRepos[activeRepo.id] : isSyncing) ? 'animate-spin text-[#4493f8]' : 'text-[#9198a1]'}`}
            />
            <span className="hidden sm:inline font-medium">
              {(activeRepo ? !!syncingRepos[activeRepo.id] : isSyncing) ? 'Syncing...' : 'Sync'}
            </span>
          </button>

          {/* Notifications Popover */}
          <div className="relative">
            <button
              type="button"
              onClick={() => {
                setNotifOpen(!notifOpen)
                setDropdownOpen(false)
                setProfileOpen(false)
              }}
              className="relative p-1.5 rounded-md border border-[#30363d] bg-[#21262d] text-[#9198a1] hover:text-[#f0f6fc] hover:bg-[#30363d] transition-colors cursor-pointer"
              title="Notifications"
              aria-label="View notifications"
            >
              <BellIcon size={14} />
              {driftReviews.length > 0 && (
                <span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-[#f85149]" />
              )}
            </button>

            {notifOpen && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setNotifOpen(false)} />
                <div className="absolute right-0 mt-1.5 w-80 rounded-md bg-[#161b22] border border-[#30363d] shadow-2xl py-1 z-50 text-xs">
                  <div className="px-3 py-2 font-semibold text-[#f0f6fc] border-b border-[#30363d] flex items-center justify-between">
                    <span>Notifications & Activity</span>
                    {driftReviews.length > 0 && (
                      <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-[#f85149]/20 text-[#f85149] font-medium">
                        {driftReviews.length} drift alerts
                      </span>
                    )}
                  </div>

                  <div className="max-h-64 overflow-y-auto divide-y divide-[#21262d]">
                    {activeReviews.length === 0 ? (
                      <div className="p-4 text-center text-[#8b949e] italic">
                        No new notifications for {activeRepo?.full_name || 'this repository'}
                      </div>
                    ) : (
                      activeReviews.slice(0, 5).map((rev) => (
                        <div
                          key={rev.id}
                          onClick={() => {
                            setNotifOpen(false)
                            if (rev.repository_id && rev.pr_number) {
                              navigate(`/pull-requests/${rev.repository_id}/${rev.pr_number}`)
                            }
                          }}
                          className="p-3 hover:bg-[#21262d] cursor-pointer transition-colors"
                        >
                          <div className="flex items-center gap-2">
                            {rev.drift_detected ? (
                              <XCircleIcon size={14} className="text-[#f85149] shrink-0" />
                            ) : (
                              <CheckCircleIcon size={14} className="text-[#3fb950] shrink-0" />
                            )}
                            <span className="font-semibold text-[#f0f6fc] truncate">
                              PR #{rev.pr_number} {rev.drift_detected ? 'Architecture Drift Detected' : 'Review Passing'}
                            </span>
                          </div>
                          <p className="text-[11px] text-[#8b949e] mt-1 line-clamp-1">
                            {rev.summary || 'Architecture conformance review completed.'}
                          </p>
                          <div className="text-[10px] text-[#656c76] mt-1">
                            {new Date(rev.created_at).toLocaleTimeString()}
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </>
            )}
          </div>

          {/* User Profile / Avatar Popover */}
          <div className="relative">
            <button
              type="button"
              onClick={() => {
                setProfileOpen(!profileOpen)
                setDropdownOpen(false)
                setNotifOpen(false)
              }}
              className="w-7 h-7 rounded-full bg-[#21262d] border border-[#30363d] flex items-center justify-center text-[#c9d1d9] font-semibold text-xs cursor-pointer overflow-hidden ml-1"
              title={`Signed in as ${userLogin}`}
              aria-label="User account menu"
            >
              {!avatarError && userAvatarUrl ? (
                <img
                  src={userAvatarUrl}
                  alt={userDisplayName}
                  className="w-full h-full object-cover"
                  onError={() => setAvatarError(true)}
                />
              ) : (
                <span className="text-[11px] font-semibold text-[#c9d1d9] select-none">{userInitials}</span>
              )}
            </button>

            {profileOpen && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setProfileOpen(false)} />
                <div className="absolute right-0 mt-1.5 w-64 rounded-md bg-[#161b22] border border-[#30363d] shadow-2xl py-1 z-50 text-xs">
                  <div className="px-3 py-2 border-b border-[#30363d]">
                    <div className="text-[11px] text-[#8b949e]">Signed in as</div>
                    <div className="font-semibold text-[#f0f6fc] truncate">{userDisplayName}</div>
                    {user?.login && (
                      <div className="text-[11px] text-[#8b949e] font-mono mt-0.5 truncate">
                        @{user.login}
                      </div>
                    )}
                    {activeRepo && (
                      <div className="text-[10px] text-[#4493f8] mt-1 truncate">
                        Active: {activeRepo.full_name}
                      </div>
                    )}
                  </div>

                  <div className="py-1">
                    <button
                      type="button"
                      onClick={() => {
                        setProfileOpen(false)
                        navigate('/repositories/select', { state: { returnTo: location.pathname + location.search + location.hash } })
                      }}
                      className="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-[#21262d] text-[#c9d1d9] cursor-pointer"
                    >
                      <RepoIcon size={14} className="text-[#8b949e]" />
                      <span>Switch / Select Repositories</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setProfileOpen(false)
                        navigate('/repositories')
                      }}
                      className="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-[#21262d] text-[#c9d1d9] cursor-pointer"
                    >
                      <RepoIcon size={14} className="text-[#8b949e]" />
                      <span>Your Repositories ({repositories.length})</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setProfileOpen(false)
                        navigate('/memory')
                      }}
                      className="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-[#21262d] text-[#c9d1d9] cursor-pointer"
                    >
                      <CommentDiscussionIcon size={14} className="text-[#8b949e]" />
                      <span>Engineering Memory</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setProfileOpen(false)
                        navigate('/settings')
                      }}
                      className="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-[#21262d] text-[#c9d1d9] cursor-pointer"
                    >
                      <GearIcon size={14} className="text-[#8b949e]" />
                      <span>Settings & Diagnostics</span>
                    </button>
                  </div>

                  <div className="border-t border-[#30363d] pt-1">
                    <button
                      type="button"
                      onClick={() => {
                        setProfileOpen(false)
                        refreshRepos(true)
                      }}
                      className="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-[#21262d] text-[#c9d1d9] cursor-pointer"
                    >
                      <SyncIcon size={14} className="text-[#8b949e]" />
                      <span>Force Sync All Data</span>
                    </button>

                    <button
                      type="button"
                      onClick={async () => {
                        setProfileOpen(false)
                        await logout()
                        navigate('/')
                      }}
                      className="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-[#21262d] text-[#f85149] cursor-pointer"
                    >
                      <SignOutIcon size={14} className="text-[#f85149]" />
                      <span>Sign out</span>
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* 2. REPOSITORY NAVIGATION TABS (UnderlineNav) */}
      <nav aria-label="Repository navigation" className="repo-nav flex items-center px-4 overflow-x-auto scrollbar-none gap-1 bg-[#010409]">
        <NavLink
          to="/repositories"
          className={({ isActive }) =>
            `flex items-center gap-2 px-3 py-2 text-xs font-medium transition-colors border-b-2 whitespace-nowrap cursor-pointer no-underline hover:no-underline ${
              isActive
                ? 'border-[#f78166] text-[#f0f6fc] font-semibold'
                : 'border-transparent text-[#9198a1] hover:text-[#f0f6fc] hover:border-[#656c76]'
            }`
          }
        >
          <RepoIcon size={16} className={location.pathname === '/repositories' ? 'text-[#f0f6fc]' : 'text-[#656c76]'} />
          <span>Repositories</span>
        </NavLink>


        <NavLink
          to="/pull-requests"
          className={({ isActive }) =>
            `flex items-center gap-2 px-3 py-2 text-xs font-medium transition-colors border-b-2 whitespace-nowrap cursor-pointer no-underline hover:no-underline ${
              isActive
                ? 'border-[#f78166] text-[#f0f6fc] font-semibold'
                : 'border-transparent text-[#9198a1] hover:text-[#f0f6fc] hover:border-[#656c76]'
            }`
          }
        >
          <GitPullRequestIcon size={16} className={location.pathname.startsWith('/pull-requests') ? 'text-[#f0f6fc]' : 'text-[#656c76]'} />
          <span>Pull requests</span>
          {actualPRCount !== null && actualPRCount > 0 && (
            <span className="px-1.5 py-0.2 rounded-full text-[10px] font-semibold bg-[#21262d] text-[#c9d1d9] border border-[#30363d]">
              {formatCompactCount(actualPRCount)}
            </span>
          )}
        </NavLink>

        <NavLink
          to="/memory"
          className={({ isActive }) =>
            `flex items-center gap-2 px-3 py-2 text-xs font-medium transition-colors border-b-2 whitespace-nowrap cursor-pointer no-underline hover:no-underline ${
              isActive
                ? 'border-[#f78166] text-[#f0f6fc] font-semibold'
                : 'border-transparent text-[#9198a1] hover:text-[#f0f6fc] hover:border-[#656c76]'
            }`
          }
        >
          <CommentDiscussionIcon size={16} className={location.pathname === '/memory' ? 'text-[#f0f6fc]' : 'text-[#656c76]'} />
          <span>Engineering Memory</span>
        </NavLink>

        <NavLink
          to="/settings"
          className={({ isActive }) =>
            `flex items-center gap-2 px-3 py-2 text-xs font-medium transition-colors border-b-2 whitespace-nowrap cursor-pointer no-underline hover:no-underline ${
              isActive
                ? 'border-[#f78166] text-[#f0f6fc] font-semibold'
                : 'border-transparent text-[#9198a1] hover:text-[#f0f6fc] hover:border-[#656c76]'
            }`
          }
        >
          <GearIcon size={16} className={location.pathname === '/settings' ? 'text-[#f0f6fc]' : 'text-[#656c76]'} />
          <span>Settings</span>
        </NavLink>
      </nav>
    </header>
  )
}
