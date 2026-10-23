import React, { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Button, Spinner } from '@primer/react'
import {
  RepoIcon,
  GitPullRequestIcon,
  PlusIcon,
  SyncIcon,
  GitBranchIcon,
  CheckIcon,
  TrashIcon,
  SearchIcon,
  AlertIcon,
} from '@primer/octicons-react'
import { useRepo } from '@/context/useRepo'
import { connectRepository, disconnectRepository } from '@/services/api'

export const RepositoriesPage: React.FC = () => {
  const location = useLocation()
  const {
    repositories,
    activeRepo,
    setActiveRepo,
    isLoadingRepositories,
    error: repoError,
    refreshRepos,
    addRepository,
    removeRepository,
    pullRequestsByRepo,
    syncRepository,
    syncingRepos,
    repoPRCounts,
  } = useRepo()

  const [repoInput, setRepoInput] = useState('')
  const [filterQuery, setFilterQuery] = useState('')
  const [isConnectingRepository, setIsConnectingRepository] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)
  const [connectSuccess, setConnectSuccess] = useState<string | null>(null)
  const [deletingRepositoryId, setDeletingRepositoryId] = useState<string | null>(null)

  const handleConnect = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!repoInput.trim() || isConnectingRepository) return

    let cleaned = repoInput.trim()
    if (cleaned.includes('github.com/')) {
      cleaned = cleaned.split('github.com/')[1]
    }
    cleaned = cleaned.replace(/^\/+|\/+$/g, '').replace(/\.git$/, '')

    if (!cleaned.includes('/') || cleaned.split('/').length !== 2) {
      setConnectError("Enter a repository in owner/repo format (e.g. 'facebook/react' or 'octocat/Hello-World')")
      return
    }

    // Check if repository is already connected
    const alreadyConnected = repositories.find((r) => r.full_name.toLowerCase() === cleaned.toLowerCase())
    if (alreadyConnected) {
      setActiveRepo(alreadyConnected)
      setConnectSuccess(`Repository ${alreadyConnected.full_name} is already connected.`)
      setRepoInput('')
      return
    }

    setIsConnectingRepository(true)
    setConnectError(null)
    setConnectSuccess(null)

    try {
      const connected = await connectRepository(cleaned)
      // 1. Immediately register in local repository state and set active
      addRepository(connected)
      setActiveRepo(connected)
      setConnectSuccess(`Connected ${connected.full_name} successfully.`)
      setRepoInput('')
      // 2. Trigger background sync without awaiting so connection button clears immediately
      refreshRepos(false).catch((err) => {
        console.warn('Background repo refresh completed with notice:', err)
      })
    } catch (err) {
      setConnectError(err instanceof Error ? err.message : 'Failed to connect repository')
    } finally {
      setIsConnectingRepository(false)
    }
  }

  const handleDisconnect = async (repoId: string, fullName: string) => {
    if (!window.confirm(`Are you sure you want to disconnect ${fullName} from engineering review?`)) {
      return
    }

    setDeletingRepositoryId(repoId)
    setConnectError(null)
    setConnectSuccess(null)
    try {
      await disconnectRepository(repoId)
      // Immediately remove from context state, active repo, and isolate caches
      removeRepository(repoId)
      setConnectSuccess(`Disconnected ${fullName}.`)
      // Do NOT trigger refreshRepos here - it can reintroduce stale state and prevents clean empty state
    } catch (err) {
      setConnectError(err instanceof Error ? err.message : 'Failed to disconnect repository')
    } finally {
      setDeletingRepositoryId(null)
    }
  }

  const handleSyncRepo = async (repoId: string) => {
    try {
      await syncRepository(repoId)
    } catch (err) {
      console.error('Failed to sync repo PRs:', err)
    }
  }

  const filteredRepos = repositories.filter((r) =>
    r.full_name.toLowerCase().includes(filterQuery.toLowerCase().trim())
  )

  return (
    <div className="space-y-5">
      {/* 1. Header Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-[#30363d]">
        <div>
          <h1 className="text-xl font-semibold text-[#f0f6fc] tracking-tight">
            Repositories
          </h1>
          <p className="text-xs text-[#9198a1] mt-0.5">
            Manage connected GitHub repositories for continuous engineering decision and architecture drift reviews.
          </p>
        </div>

        <Link
          to="/repositories/select"
          state={{ returnTo: location.pathname + location.search + location.hash }}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] text-xs font-semibold text-[#f0f6fc] transition-colors cursor-pointer self-start sm:self-auto no-underline hover:no-underline"
        >
          <RepoIcon size={14} className="text-[#8b949e]" />
          <span>Browse GitHub Repositories</span>
        </Link>
      </div>

      {/* 2. Connect Repository Form Box (GitHub style) */}
      <div className="rounded-md border border-[#30363d] bg-[#161b22] p-4 space-y-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-[#f0f6fc]">
          <PlusIcon size={14} className="text-[#3fb950]" />
          <span>Connect a GitHub repository</span>
        </div>

        <form onSubmit={handleConnect} className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
          <div className="relative flex-1">
            <RepoIcon size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#656c76] pointer-events-none z-10" />
            <input
              type="text"
              value={repoInput}
              onChange={(e) => setRepoInput(e.target.value)}
              disabled={isConnectingRepository}
              placeholder="owner/repository (e.g. octocat/Spoon-Knife)"
              className="w-full h-8 pl-8.5 pr-3 py-1 rounded-md bg-[#0d1117] border border-[#30363d] text-xs text-[#f0f6fc] placeholder-[#656c76] focus:outline-none focus:border-[#1f6feb] focus:ring-1 focus:ring-[#1f6feb] transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            />
          </div>

          <Button
            type="submit"
            variant="primary"
            size="small"
            disabled={isConnectingRepository || !repoInput.trim()}
            leadingVisual={isConnectingRepository ? () => <Spinner size="small" srText={null} className="text-white" /> : PlusIcon}
            className="shrink-0 font-medium h-8 inline-flex items-center justify-center min-w-[155px]"
          >
            {isConnectingRepository ? 'Adding repository' : 'Connect repository'}
          </Button>
        </form>

        {connectError && (
          <div className="p-2.5 rounded bg-[#f85149]/10 border border-[#f85149]/40 text-xs text-[#f85149] flex items-center gap-2">
            <AlertIcon size={14} className="shrink-0" />
            <span>{connectError}</span>
          </div>
        )}

        {connectSuccess && (
          <div className="p-2.5 rounded bg-[#3fb950]/10 border border-[#3fb950]/40 text-xs text-[#3fb950] flex items-center gap-2">
            <CheckIcon size={14} className="shrink-0" />
            <span>{connectSuccess}</span>
          </div>
        )}
      </div>

      {/* 3. Search & Filter Bar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <SearchIcon size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#656c76]" />
          <input
            type="text"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            placeholder="Find a repository..."
            className="w-full pl-9 pr-3 py-1.5 rounded-md bg-[#0d1117] border border-[#30363d] text-xs text-[#f0f6fc] placeholder-[#656c76] focus:outline-none focus:border-[#1f6feb] transition-colors"
          />
        </div>
        <div className="text-xs text-[#9198a1]">
          {filteredRepos.length} {filteredRepos.length === 1 ? 'repository' : 'repositories'}
        </div>
      </div>

      {/* 4. Repositories List (GitHub list items) */}
      <div className="rounded-md border border-[#30363d] bg-[#0d1117] overflow-hidden divide-y divide-[#21262d]">
        {isLoadingRepositories ? (
          <div className="p-12 text-center text-xs text-[#9198a1] space-y-2">
            <SyncIcon size={20} className="animate-spin text-[#4493f8] mx-auto" />
            <div>Loading repositories...</div>
          </div>
        ) : repoError ? (
          <div className="p-12 text-center space-y-3 text-xs">
            <AlertIcon size={28} className="text-[#f85149] mx-auto mb-1" />
            <div className="text-sm font-semibold text-[#f0f6fc]">
              Failed to load repositories
            </div>
            <p className="text-[#9198a1] max-w-sm mx-auto">
              {repoError}
            </p>
            <button
              type="button"
              onClick={() => refreshRepos(true)}
              className="px-3 py-1.5 rounded-md bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] text-xs font-medium text-[#c9d1d9] hover:text-[#f0f6fc] transition-colors cursor-pointer inline-flex items-center gap-1.5 mx-auto"
            >
              <SyncIcon size={12} />
              <span>Retry</span>
            </button>
          </div>
        ) : repositories.length === 0 ? (
          <div className="p-12 text-center space-y-3 text-xs">
            <div className="w-12 h-12 rounded-full bg-[#161b22] border border-[#30363d] flex items-center justify-center mx-auto text-[#8b949e]">
              <RepoIcon size={24} />
            </div>
            <div className="space-y-1">
              <div className="text-sm font-semibold text-[#f0f6fc]">
                No repositories connected
              </div>
              <p className="text-[#8b949e] max-w-sm mx-auto">
                Connect a GitHub repository to start reviewing pull requests.
              </p>
            </div>
          </div>
        ) : filteredRepos.length === 0 ? (
          <div className="p-12 text-center space-y-2 text-xs">
            <SearchIcon size={28} className="text-[#656c76] mx-auto mb-1" />
            <div className="text-sm font-semibold text-[#f0f6fc]">
              No repositories match your search.
            </div>
            <p className="text-[#9198a1] max-w-sm mx-auto">
              Try adjusting or clearing your search query.
            </p>
          </div>
        ) : (
          filteredRepos.map((repo) => {
            const isActive = activeRepo?.id === repo.id
            const prList = pullRequestsByRepo[repo.id] || []
            const loadedOpenPrs = prList.filter((p) => p.state === 'open').length
            const openPrs = repoPRCounts[repo.id] ?? repo.open_pr_count ?? repo.pull_requests_count ?? loadedOpenPrs

            return (
              <div
                key={repo.id}
                className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 hover:bg-[#161b22]/50 transition-colors"
              >
                <div className="space-y-1.5 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Link
                      to={`/pull-requests?repoId=${repo.id}`}
                      className="text-base font-semibold text-[#4493f8] hover:underline cursor-pointer truncate"
                    >
                      {repo.full_name}
                    </Link>

                    <span className="px-2 py-0.5 rounded-full text-[11px] font-medium border border-[#30363d] text-[#9198a1] bg-[#161b22]">
                      Public
                    </span>

                    {isActive && (
                      <span className="px-2 py-0.5 rounded-full text-[11px] font-medium border border-[#3fb950]/40 bg-[#3fb950]/15 text-[#3fb950] flex items-center gap-1">
                        <CheckIcon size={12} />
                        <span>Active</span>
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-3 text-xs text-[#9198a1] flex-wrap">
                    <span className="flex items-center gap-1">
                      <span className="w-2.5 h-2.5 rounded-full bg-[#3178c6] inline-block" />
                      <span>TypeScript</span>
                    </span>

                    <span className="flex items-center gap-1">
                      <GitBranchIcon size={13} />
                      <span>{repo.default_branch || 'main'}</span>
                    </span>

                    <Link
                      to={`/pull-requests?repoId=${repo.id}`}
                      className="flex items-center gap-1 hover:text-[#4493f8]"
                    >
                      <GitPullRequestIcon size={13} />
                      <span>{openPrs} open PRs</span>
                    </Link>
                  </div>
                </div>

                {/* Actions: Set Active, Sync, Disconnect */}
                <div className="flex items-center gap-2 shrink-0">
                  {!isActive && (
                    <button
                      type="button"
                      onClick={() => setActiveRepo(repo)}
                      className="px-2.5 py-1 rounded-md bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] text-xs font-medium text-[#c9d1d9] hover:text-[#f0f6fc] transition-colors cursor-pointer"
                    >
                      Make active
                    </button>
                  )}

                  <button
                    type="button"
                    onClick={() => handleSyncRepo(repo.id)}
                    disabled={!!syncingRepos[repo.id]}
                    className="p-1.5 rounded-md bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] text-[#9198a1] hover:text-[#f0f6fc] disabled:opacity-60 transition-colors cursor-pointer"
                    title="Sync pull requests for this repository"
                  >
                    <SyncIcon
                      size={14}
                      className={syncingRepos[repo.id] ? 'animate-spin text-[#4493f8]' : ''}
                    />
                  </button>

                  <button
                    type="button"
                    onClick={() => handleDisconnect(repo.id, repo.full_name)}
                    disabled={deletingRepositoryId === repo.id}
                    className="p-1.5 rounded-md bg-[#21262d] hover:bg-[#da3633]/20 hover:border-[#f85149]/40 border border-[#30363d] text-[#8b949e] hover:text-[#f85149] disabled:opacity-60 transition-colors cursor-pointer inline-flex items-center justify-center min-w-[28px] min-h-[28px]"
                    title="Disconnect repository"
                  >
                    {deletingRepositoryId === repo.id ? (
                      <Spinner size="small" srText={null} className="text-[#f85149]" />
                    ) : (
                      <TrashIcon size={14} />
                    )}
                  </button>
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
