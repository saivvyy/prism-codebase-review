import React, { useState } from 'react'
import { NavLink, useNavigate, useLocation, useSearchParams } from 'react-router-dom'
import {
  GitPullRequestIcon,
  GitMergeIcon,
  RepoIcon,
  SidebarCollapseIcon,
  SidebarExpandIcon,
  PlusIcon,
  CheckCircleIcon,
  XCircleIcon,
  CommentDiscussionIcon,
  GearIcon,
  BookmarkIcon,
  ShieldCheckIcon,
} from '@primer/octicons-react'
import { useRepo } from '@/context/useRepo'

export const Sidebar: React.FC = () => {
  const [collapsed, setCollapsed] = useState(false)
  const { activeRepo, repositories, setActiveRepo, isSidebarOpen, activePR } = useRepo()
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()

  if (!isSidebarOpen) {
    return null
  }

  const pathname = location.pathname
  const isGlobalHome = pathname === '/' || pathname === '/workspace'

  // Active check helpers
  const isPrView = (viewKey: string) => {
    if (!pathname.startsWith('/pull-requests')) return false

    // On PR detail route (/pull-requests/:repoId/:prNumber), derive PR view from activePR lifecycle state
    const isPrDetailRoute = /^\/pull-requests\/[^/]+\/\d+/.test(pathname)
    if (isPrDetailRoute) {
      if (activePR) {
        const isClosedOrMerged =
          activePR.state === 'closed' ||
          activePR.state === 'merged' ||
          activePR.is_merged === true ||
          Boolean(activePR.merged_at)

        if (viewKey === 'closed') {
          return isClosedOrMerged
        }
        if (viewKey === 'open') {
          return !isClosedOrMerged && activePR.state === 'open'
        }
        return false
      }
      // If activePR detail has not loaded yet, do not highlight any PR view erroneously
      return false
    }

    const view = searchParams.get('view')
    const state = searchParams.get('state')
    const workflow = searchParams.get('workflow')

    if (viewKey === 'open') {
      return view === 'open' || state === 'open' || (!view && !state && !workflow)
    }
    if (viewKey === 'closed') {
      return view === 'closed' || state === 'closed'
    }
    if (viewKey === 'drift') {
      return view === 'drift' || workflow === 'drift'
    }
    if (viewKey === 'approved') {
      return view === 'approved' || workflow === 'approved'
    }
    return false
  }

  const isMemoryCat = (catId: string) => {
    if (pathname !== '/memory') return false
    const cat = searchParams.get('category')
    if (catId === 'all') {
      return !cat || cat === 'all'
    }
    return cat === catId
  }

  return (
    <aside
      className={`bg-[#0d1117] border-r border-[#30363d] flex flex-col shrink-0 min-h-screen text-xs select-none transition-all duration-200 ${
        collapsed ? 'w-14' : 'w-60'
      }`}
    >
      {/* Sidebar Header */}
      <div className="h-14 border-b border-[#30363d] px-3.5 flex items-center justify-between">
        {!collapsed && (
          <div className="flex items-center gap-2 min-w-0">
            <RepoIcon size={16} className="text-[#8b949e] shrink-0" />
            <span
              className="font-semibold text-xs text-[#f0f6fc] truncate cursor-pointer hover:underline"
              onClick={() =>
                navigate(
                  activeRepo ? '/overview' : '/repositories/select',
                  activeRepo ? undefined : { state: { returnTo: location.pathname + location.search + location.hash } }
                )
              }
            >
              {activeRepo?.full_name || 'No repository connected'}
            </span>
          </div>
        )}
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          className={`p-1.5 rounded-md text-[#8b949e] hover:text-[#f0f6fc] hover:bg-[#21262d] transition-colors cursor-pointer ${
            collapsed ? 'mx-auto' : ''
          }`}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <SidebarExpandIcon size={16} /> : <SidebarCollapseIcon size={16} />}
        </button>
      </div>

      {/* Navigation body */}
      <div className="flex-1 py-3 px-2 space-y-4 overflow-y-auto sidebar-nav">
        {/* GLOBAL HOME: Cross-Repository Workspace Navigation */}
        {isGlobalHome ? (
          <div className="space-y-0.5">
            {!collapsed && (
              <div className="px-2.5 pb-1.5 text-[11px] font-semibold text-[#8b949e] uppercase tracking-wider">
                Workspace
              </div>
            )}

            <NavLink
              to="/repositories"
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-2.5 py-1.5 rounded-md font-medium transition-colors no-underline hover:no-underline focus:no-underline active:no-underline visited:no-underline ${
                  isActive
                    ? 'bg-[#21262d] text-[#f0f6fc] font-semibold'
                    : 'text-[#8b949e] hover:bg-[#161b22] hover:text-[#f0f6fc]'
                }`
              }
              aria-label="Connected Repositories"
            >
              <RepoIcon size={16} className="text-[#8b949e] shrink-0" />
              {!collapsed && <span>Connected Repositories ({repositories.length})</span>}
            </NavLink>

            <NavLink
              to="/settings"
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-2.5 py-1.5 rounded-md font-medium transition-colors no-underline hover:no-underline focus:no-underline active:no-underline visited:no-underline ${
                  isActive
                    ? 'bg-[#21262d] text-[#f0f6fc] font-semibold'
                    : 'text-[#8b949e] hover:bg-[#161b22] hover:text-[#f0f6fc]'
                }`
              }
              aria-label="Settings"
            >
              <GearIcon size={16} className="text-[#8b949e] shrink-0" />
              {!collapsed && <span>Settings</span>}
            </NavLink>
          </div>
        ) : (
          /* CANONICAL REPOSITORY NAVIGATION */
          <>
            {/* GROUP 1: PULL REQUESTS */}
            <div className="space-y-0.5">
              {!collapsed && (
                <div className="px-2.5 pb-1.5 text-[11px] font-semibold text-[#8b949e] uppercase tracking-wider">
                  Pull Requests
                </div>
              )}

              <NavLink
                to="/pull-requests?view=open"
                className={() => {
                  const isSelected = isPrView('open')
                  return `flex items-center gap-2.5 px-2.5 py-1.5 rounded-md font-medium transition-colors no-underline hover:no-underline focus:no-underline active:no-underline visited:no-underline ${
                    isSelected
                      ? 'bg-[#21262d] text-[#f0f6fc] font-semibold'
                      : 'text-[#8b949e] hover:bg-[#161b22] hover:text-[#f0f6fc]'
                  }`
                }}
                aria-label="Open PRs"
              >
                <GitPullRequestIcon size={16} className="text-[#8b949e] shrink-0" />
                {!collapsed && <span>Open PRs</span>}
              </NavLink>

              <NavLink
                to="/pull-requests?view=closed"
                className={() => {
                  const isSelected = isPrView('closed')
                  return `flex items-center gap-2.5 px-2.5 py-1.5 rounded-md font-medium transition-colors no-underline hover:no-underline focus:no-underline active:no-underline visited:no-underline ${
                    isSelected
                      ? 'bg-[#21262d] text-[#f0f6fc] font-semibold'
                      : 'text-[#8b949e] hover:bg-[#161b22] hover:text-[#f0f6fc]'
                  }`
                }}
                aria-label="Closed & Merged"
              >
                <GitMergeIcon size={16} className="text-[#8b949e] shrink-0" />
                {!collapsed && <span>Closed & Merged</span>}
              </NavLink>

              <NavLink
                to="/pull-requests?view=drift"
                className={() => {
                  const isSelected = isPrView('drift')
                  return `flex items-center gap-2.5 px-2.5 py-1.5 rounded-md font-medium transition-colors no-underline hover:no-underline focus:no-underline active:no-underline visited:no-underline ${
                    isSelected
                      ? 'bg-[#21262d] text-[#f0f6fc] font-semibold'
                      : 'text-[#8b949e] hover:bg-[#161b22] hover:text-[#f0f6fc]'
                  }`
                }}
                aria-label="Drift Detected"
              >
                <XCircleIcon size={16} className="text-[#f85149] shrink-0" />
                {!collapsed && <span>Drift Detected</span>}
              </NavLink>

              <NavLink
                to="/pull-requests?view=approved"
                className={() => {
                  const isSelected = isPrView('approved')
                  return `flex items-center gap-2.5 px-2.5 py-1.5 rounded-md font-medium transition-colors no-underline hover:no-underline focus:no-underline active:no-underline visited:no-underline ${
                    isSelected
                      ? 'bg-[#21262d] text-[#f0f6fc] font-semibold'
                      : 'text-[#8b949e] hover:bg-[#161b22] hover:text-[#f0f6fc]'
                  }`
                }}
                aria-label="Conforming / Approved"
              >
                <CheckCircleIcon size={16} className="text-[#3fb950] shrink-0" />
                {!collapsed && <span>Conforming / Approved</span>}
              </NavLink>
            </div>

            {/* GROUP 2: ENGINEERING MEMORY */}
            <div className="space-y-0.5">
              {!collapsed && (
                <div className="px-2.5 pb-1.5 text-[11px] font-semibold text-[#8b949e] uppercase tracking-wider">
                  Engineering Memory
                </div>
              )}

              {[
                { id: 'all', label: 'All Knowledge', path: '/memory', icon: CommentDiscussionIcon },
                { id: 'architecture_decision', label: 'Architecture Decisions', path: '/memory?category=architecture_decision', icon: BookmarkIcon },
                { id: 'rejected_pattern', label: 'Rejected Patterns', path: '/memory?category=rejected_pattern', icon: XCircleIcon },
                { id: 'approved_exception', label: 'Approved Exceptions', path: '/memory?category=approved_exception', icon: ShieldCheckIcon },
                { id: 'review_decision', label: 'Review Decisions', path: '/memory?category=review_decision', icon: GitPullRequestIcon },
                { id: 'lesson_learned', label: 'Lessons Learned', path: '/memory?category=lesson_learned', icon: BookmarkIcon },
              ].map((item) => {
                const Icon = item.icon
                const isSelected = isMemoryCat(item.id)
                return (
                  <NavLink
                    key={item.id}
                    to={item.path}
                    className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-md font-medium transition-colors no-underline hover:no-underline focus:no-underline active:no-underline visited:no-underline ${
                      isSelected
                        ? 'bg-[#21262d] text-[#f0f6fc] font-semibold'
                        : 'text-[#8b949e] hover:bg-[#161b22] hover:text-[#f0f6fc]'
                    }`}
                    aria-label={item.label}
                  >
                    <Icon size={16} className="text-[#8b949e] shrink-0" />
                    {!collapsed && <span>{item.label}</span>}
                  </NavLink>
                )
              })}
            </div>
          </>
        )}

        {/* Separator */}
        <div className="border-t border-[#30363d] my-1" />

        {/* Connected Repositories Quick Switcher list */}
        {!collapsed && (
          <div>
            <div className="px-2.5 pb-1.5 flex items-center justify-between text-[11px] font-semibold text-[#8b949e]">
              <span>Connected Repositories</span>
              <button
                type="button"
                onClick={() => navigate('/repositories/select', { state: { returnTo: location.pathname + location.search + location.hash } })}
                className="hover:text-[#4493f8] cursor-pointer"
                aria-label="Connect repository"
              >
                <PlusIcon size={14} />
              </button>
            </div>
            <div className="space-y-0.5">
              {repositories.length === 0 ? (
                <div className="px-2.5 py-1 text-[11px] text-[#8b949e] italic">
                  No repositories connected
                </div>
              ) : (
                repositories.slice(0, 5).map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    onClick={() => {
                      setActiveRepo(r)
                      if (pathname === '/') {
                        navigate('/overview')
                      }
                    }}
                    className={`w-full text-left flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs truncate transition-colors cursor-pointer ${
                      activeRepo?.id === r.id
                        ? 'bg-[#21262d] text-[#f0f6fc] font-semibold'
                        : 'text-[#8b949e] hover:text-[#f0f6fc] hover:bg-[#161b22]'
                    }`}
                  >
                    <RepoIcon size={14} className="shrink-0 text-[#8b949e]" />
                    <span className="truncate">{r.full_name}</span>
                  </button>
                ))
              )}
            </div>
          </div>
        )}
      </div>

      {/* Sidebar Footer */}
      <div className="p-3 border-t border-[#30363d] flex items-center justify-between">
        {!collapsed && (
          <div className="flex items-center gap-2 min-w-0">
            <div className={`w-2 h-2 rounded-full ${activeRepo ? 'bg-[#3fb950]' : 'bg-[#656c76]'} shrink-0`} />
            <span className="text-[11px] text-[#8b949e] truncate">
              {activeRepo ? 'Connected' : 'No repo connected'}
            </span>
          </div>
        )}
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          className={`flex items-center gap-1.5 text-[11px] text-[#8b949e] hover:text-[#f0f6fc] cursor-pointer ${
            collapsed ? 'mx-auto' : ''
          }`}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <SidebarExpandIcon size={14} /> : <span>Collapse sidebar</span>}
        </button>
      </div>
    </aside>
  )
}

export default Sidebar
