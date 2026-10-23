import React, { useEffect, useState, useMemo, useRef } from 'react'
import { useSearchParams, useNavigate, useLocation } from 'react-router-dom'
import { Button } from '@primer/react'
import {
  SearchIcon,
  PlusIcon,
  SyncIcon,
  GitPullRequestIcon,
  CheckIcon,
  TagIcon,
  XIcon,
  ShieldCheckIcon,
  AlertIcon,
  LawIcon,
  LinkExternalIcon,
  KebabHorizontalIcon,
} from '@primer/octicons-react'
import { useRepo } from '@/context/useRepo'
import {
  recordRepositoryMemory,
} from '@/services/api'
import type { EngineeringMemory, MemoryCategory, MemoryConfidence, RecordMemoryRequest } from '@/types'

interface CategoryTab {
  id: string
  label: string
  backendType?: MemoryCategory
}

const CATEGORIES: CategoryTab[] = [
  { id: 'all', label: 'All Knowledge' },
  { id: 'architecture_decision', label: 'Architecture Decisions', backendType: 'architecture_decision' },
  { id: 'rejected_pattern', label: 'Rejected Patterns', backendType: 'rejected_pattern' },
  { id: 'approved_exception', label: 'Approved Exceptions', backendType: 'approved_exception' },
  { id: 'review_decision', label: 'Review Decisions', backendType: 'review_decision' },
  { id: 'lesson_learned', label: 'Lessons Learned', backendType: 'lesson_learned' },
]

export const MemoryExplorerPage: React.FC = () => {
  const {
    repositories,
    activeRepo,
    memoryCache,
    memoryRequestState,
    memoryError,
    fetchRepositoryMemories,
  } = useRepo()
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()

  const urlCategory = searchParams.get('category')
  const [internalCategory, setInternalCategory] = useState<string>('all')
  const selectedCategory = urlCategory || internalCategory

  const [searchQuery, setSearchQuery] = useState('')
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // Close the dropdown if the user clicks outside it
  useEffect(() => {
    if (!menuOpen) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [menuOpen])

  const catObj = CATEGORIES.find((c) => c.id === selectedCategory)
  const cleanCategory = catObj?.backendType || 'all'
  const trimmedQuery = searchQuery.trim()

  const currentCacheKey = activeRepo ? `${activeRepo.id}:${cleanCategory}:${trimmedQuery}` : ''
  const baseKey = activeRepo ? `${activeRepo.id}:all:` : ''

  // 1. Resolve memories from cache (direct key or local tab filter from base key)
  const cachedDirect = currentCacheKey ? memoryCache[currentCacheKey] : undefined
  const cachedBase = (!trimmedQuery && cleanCategory !== 'all' && baseKey) ? memoryCache[baseKey] : undefined
  const locallyFiltered = cachedBase
    ? (cleanCategory === 'all' ? cachedBase : cachedBase.filter((m) => m.type === cleanCategory))
    : undefined

  const memories: EngineeringMemory[] = useMemo(
    () => cachedDirect || locallyFiltered || [],
    [cachedDirect, locallyFiltered]
  )

  // 2. Request state & loading tracking
  const currentReqState = currentCacheKey
    ? (memoryRequestState[currentCacheKey] || (locallyFiltered ? 'success' : 'idle'))
    : 'idle'
  const isLoading = Boolean(activeRepo) && (currentReqState === 'loading' || isRefreshing)
  const error = currentCacheKey ? memoryError[currentCacheKey] : null

  // Fetch when repository, category, or search changes (cached calls return immediately with no network request)
  useEffect(() => {
    if (!activeRepo) return
    fetchRepositoryMemories(activeRepo.id, cleanCategory, trimmedQuery, false).catch(() => { })
  }, [activeRepo, cleanCategory, trimmedQuery, fetchRepositoryMemories])

  const handleRefresh = async () => {
    if (!activeRepo) return
    setIsRefreshing(true)
    try {
      await fetchRepositoryMemories(activeRepo.id, cleanCategory, trimmedQuery, true)
    } catch {
      // Error handled via memoryError state
    } finally {
      setIsRefreshing(false)
    }
  }

  // Record Decision Modal State
  const [showModal, setShowModal] = useState(false)
  const [claim, setClaim] = useState('')
  const [rationale, setRationale] = useState('')
  const [scope, setScope] = useState('')
  const [sourceRef, setSourceRef] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [categoryType, setCategoryType] = useState<MemoryCategory>('architecture_decision')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  // Synchronize category change
  const handleCategoryChange = (catId: string) => {
    setInternalCategory(catId)
    const nextParams = new URLSearchParams(searchParams)
    if (catId === 'all') {
      nextParams.delete('category')
    } else {
      nextParams.set('category', catId)
    }
    setSearchParams(nextParams)
  }

  // Handle Record Decision Submission
  const handleRecordSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!activeRepo) {
      setSubmitError('Select an active repository first')
      return
    }
    if (!claim.trim()) {
      setSubmitError('Decision claim is required')
      return
    }

    setIsSubmitting(true)
    setSubmitError(null)

    try {
      const payload: RecordMemoryRequest = {
        claim: claim.trim(),
        type: categoryType,
        rationale: rationale.trim() || undefined,
        scope: scope.trim() || undefined,
        source_ref: sourceRef.trim() || undefined,
        source_url: sourceUrl.trim() || undefined,
        confidence: 'supported',
      }

      await recordRepositoryMemory(activeRepo.id, payload)
      // Force refresh repository cache so newly retained memory appears immediately
      await fetchRepositoryMemories(activeRepo.id, cleanCategory, trimmedQuery, true)

      // Reset form and close
      setClaim('')
      setRationale('')
      setScope('')
      setSourceRef('')
      setSourceUrl('')
      setShowModal(false)
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Failed to record decision')
    } finally {
      setIsSubmitting(false)
    }
  }

  // Type badge styling
  const getTypeBadge = (type: MemoryCategory) => {
    switch (type) {
      case 'DOCUMENTED_DECISION':
      case 'architecture_decision':
        return {
          label: 'Documented Decision',
          className: 'bg-[#1f6feb]/15 text-[#58a6ff] border-[#1f6feb]/30',
        }
      case 'REJECTED_PATTERN':
      case 'rejected_pattern':
        return {
          label: 'Rejected Pattern',
          className: 'bg-[#f85149]/15 text-[#f85149] border-[#f85149]/30',
        }
      case 'APPROVED_EXCEPTION':
      case 'approved_exception':
        return {
          label: 'Approved Exception',
          className: 'bg-[#238636]/15 text-[#3fb950] border-[#238636]/40',
        }
      case 'OBSERVED_CONVENTION':
        return {
          label: 'Observed Convention',
          className: 'bg-[#3fb950]/10 text-[#56d364] border-[#3fb950]/30',
        }
      case 'REVIEW_OBSERVATION':
        return {
          label: 'Review Observation',
          className: 'bg-[#8b949e]/15 text-[#c9d1d9] border-[#8b949e]/30',
        }
      case 'review_decision':
        return {
          label: 'Review Decision',
          className: 'bg-[#8957e5]/15 text-[#bc8cff] border-[#8957e5]/30',
        }
      case 'lesson_learned':
        return {
          label: 'Lesson Learned',
          className: 'bg-[#d29922]/15 text-[#e3b341] border-[#d29922]/30',
        }
      default:
        return {
          label: String(type).replace(/_/g, ' '),
          className: 'bg-[#21262d] text-[#8b949e] border-[#30363d]',
        }
    }
  }

  const getAuthorityBadge = (auth?: string) => {
    if (!auth) return null
    switch (auth.toUpperCase()) {
      case 'ENFORCEABLE':
        return { label: 'Enforceable Rule', className: 'text-[#d2a8ff] bg-[#8957e5]/10 border-[#8957e5]/30' }
      case 'EXCEPTION':
        return { label: 'Scoped Exception', className: 'text-[#e3b341] bg-[#d29922]/10 border-[#d29922]/30' }
      case 'CONTEXTUAL':
        return { label: 'Contextual', className: 'text-[#56d364] bg-[#3fb950]/10 border-[#3fb950]/30' }
      case 'NON_AUTHORITATIVE':
        return { label: 'Non-Authoritative', className: 'text-[#8b949e] bg-[#21262d] border-[#30363d]' }
      case 'REJECTED_PATTERN':
        return { label: 'Rejected Pattern', className: 'text-[#f85149] bg-[#da3633]/10 border-[#da3633]/30' }
      default:
        return null
    }
  }

  const getConfidenceBadge = (conf: MemoryConfidence) => {
    switch (conf) {
      case 'supported':
        return { label: 'Supported', className: 'text-[#3fb950] bg-[#238636]/10 border-[#238636]/30' }
      case 'partially_supported':
        return { label: 'Partially Supported', className: 'text-[#d29922] bg-[#9e6a03]/10 border-[#9e6a03]/30' }
      case 'insufficient_evidence':
        return { label: 'Insufficient Evidence', className: 'text-[#8b949e] bg-[#30363d]/30 border-[#30363d]' }
    }
  }

  const allRepoMemories = useMemo(() => {
    if (baseKey && memoryCache[baseKey]) {
      return memoryCache[baseKey]
    }
    return memories
  }, [baseKey, memoryCache, memories])

  const countsByType = useMemo(() => {
    const counts: Record<string, number> = { all: allRepoMemories.length }
    for (const m of allRepoMemories) {
      counts[m.type] = (counts[m.type] || 0) + 1
    }
    return counts
  }, [allRepoMemories])

  return (
    <div className="space-y-4">
      {/* 1. Header Bar: Title + Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-[#30363d]">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold text-[#f0f6fc] tracking-tight">
              Engineering Memory
            </h1>
            {activeRepo && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-[#161b22] border border-[#30363d] text-[#8b949e] font-mono">
                {activeRepo.full_name}
              </span>
            )}
          </div>
          <p className="text-xs text-[#9198a1] mt-0.5">
            PRism learns engineering knowledge from your code-review history.
          </p>
        </div>

        {/* Action group: Refresh + More options — styled to match Header secondary actions */}
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={handleRefresh}
            disabled={isLoading || !activeRepo}
            title="Refresh memory precedents"
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-[#30363d] bg-transparent text-[#9198a1] hover:text-[#f0f6fc] hover:bg-[#21262d] active:bg-[#161b22] disabled:opacity-40 disabled:cursor-not-allowed transition-colors text-xs font-medium cursor-pointer"
          >
            {isLoading ? (
              <svg
                className="animate-spin"
                width="14"
                height="14"
                viewBox="0 0 16 16"
                fill="currentColor"
                aria-hidden="true"
              >
                <path d="M8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0Zm0 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Z" opacity=".25" />
                <path d="M8 1.5A6.5 6.5 0 0 1 14.5 8h-1.5A5 5 0 0 0 8 3V1.5Z" />
              </svg>
            ) : (
              <SyncIcon size={14} />
            )}
            {isLoading ? 'Refreshing' : 'Refresh'}
          </button>

          {/* Three-dot menu — native dropdown, inherits dark PRism surface */}
          <div ref={menuRef} className="relative">
            <button
              type="button"
              aria-label="More options"
              aria-expanded={menuOpen}
              disabled={!activeRepo}
              title="More options"
              onClick={() => setMenuOpen((o) => !o)}
              className="flex items-center justify-center w-[30px] h-[30px] rounded-md border border-[#30363d] bg-transparent text-[#9198a1] hover:text-[#f0f6fc] hover:bg-[#21262d] active:bg-[#161b22] disabled:opacity-40 disabled:cursor-not-allowed transition-colors cursor-pointer"
            >
              <KebabHorizontalIcon size={14} />
            </button>

            {menuOpen && (
              <div
                role="menu"
                className="absolute right-0 top-full mt-1 z-50 min-w-[180px] rounded-md border border-[#30363d] bg-[#161b22] shadow-[0_8px_24px_rgba(1,4,9,0.8)] py-1"
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false)
                    setSubmitError(null)
                    setShowModal(true)
                  }}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-[#e6edf3] hover:bg-[#21262d] hover:text-[#f0f6fc] transition-colors text-left cursor-pointer"
                >
                  <PlusIcon size={14} className="text-[#8b949e] shrink-0" />
                  Record manual decision
                </button>
              </div>
            )}
          </div>
        </div>

      </div>

      {/* 2. Search & Category Filter Navigation */}
      <div className="space-y-2.5">
        <div className="relative max-w-md">
          <SearchIcon size={14} className="text-[#656c76] absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input
            type="text"
            placeholder="Search decisions, rationale, scope, PR references..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-1.5 rounded-md bg-[#0d1117] border border-[#30363d] focus:border-[#1f6feb] text-xs text-[#f0f6fc] placeholder-[#656c76] focus:outline-none transition-colors"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-[#8b949e] hover:text-[#f0f6fc] cursor-pointer"
            >
              Clear
            </button>
          )}
        </div>

        {/* Category Tabs */}
        <div className="flex items-center gap-1 overflow-x-auto pb-1 text-xs">
          {CATEGORIES.map((cat) => {
            const isActive = selectedCategory === cat.id
            const count = countsByType[cat.backendType || 'all']
            return (
              <button
                key={cat.id}
                type="button"
                onClick={() => handleCategoryChange(cat.id)}
                className={`px-3 py-1.5 rounded-md shrink-0 transition-colors cursor-pointer border text-xs font-medium flex items-center gap-1.5 ${isActive
                  ? 'bg-[#21262d] text-[#f0f6fc] border-[#8b949e] font-semibold'
                  : 'bg-[#161b22] text-[#8b949e] border-[#30363d] hover:text-[#f0f6fc]'
                  }`}
              >
                <span>{cat.label}</span>
                {count !== undefined && count > 0 && (
                  <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-[#30363d]/60 text-[#c9d1d9]">
                    {count}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {/* 3. Main Memory Records List */}
      <div className="border border-[#30363d] rounded-md bg-[#0d1117] overflow-hidden">
        <div className="px-4 py-2.5 border-b border-[#30363d] bg-[#161b22] flex items-center justify-between text-xs text-[#9198a1]">
          <span className="font-semibold text-[#f0f6fc]">
            Persistent Architectural Knowledge {isLoading ? '' : `(${memories.length})`}
          </span>
          <span className="text-[11px]">
            Scope: {activeRepo ? activeRepo.full_name : 'No repository selected'}
          </span>
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-xs text-[#9198a1] space-y-2">
            <SyncIcon size={18} className="animate-spin text-[#4493f8] mx-auto" />
            <p>Querying repository memory...</p>
          </div>
        ) : error ? (
          <div className="p-8 text-center text-xs text-[#f85149] space-y-2">
            <AlertIcon size={20} className="mx-auto" />
            <p className="text-sm font-semibold text-[#f85149]">Memory unavailable</p>
            <p className="text-[#8b949e]">{error}</p>
          </div>
        ) : !activeRepo ? (
          <div className="p-12 text-center text-xs text-[#8b949e] space-y-3">
            <LawIcon size={28} className="text-[#656c76] mx-auto" />
            <p className="text-sm font-semibold text-[#f0f6fc]">
              {repositories.length === 0 ? 'No repositories connected' : 'No active repository selected'}
            </p>
            <p className="max-w-md mx-auto">
              {repositories.length === 0
                ? 'Connect a GitHub repository to start reviewing pull requests.'
                : 'Select a connected repository from the header dropdown to view its engineering memory.'}
            </p>
            {repositories.length === 0 && (
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
            )}
          </div>
        ) : memories.length === 0 ? (
          <div className="p-12 text-center text-xs text-[#8b949e] space-y-3">
            <LawIcon size={28} className="text-[#656c76] mx-auto" />
            <p className="text-sm font-semibold text-[#f0f6fc]">No engineering precedents yet</p>
            <p className="max-w-md mx-auto leading-relaxed">
              PRism learns engineering knowledge from your code-review history. Run an architecture review on a pull request to populate memory precedents.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-[#21262d]">
            {memories.map((mem) => {
              const typeBadge = getTypeBadge(mem.type)
              const confBadge = getConfidenceBadge(mem.confidence)

              return (
                <div key={mem.id} className="p-4 hover:bg-[#161b22]/50 transition-colors space-y-3 text-xs">
                  {/* Top line: Type Badge + Authority + Claim + Confidence */}
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1.5 flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border ${typeBadge.className}`}>
                          {typeBadge.label}
                        </span>
                        {(() => {
                          const authBadge = getAuthorityBadge(mem.authority)
                          if (!authBadge) return null
                          return (
                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border ${authBadge.className}`}>
                              {authBadge.label}
                            </span>
                          )
                        })()}
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium border ${confBadge.className}`}>
                          {confBadge.label}
                        </span>
                        {mem.observation_count && mem.observation_count > 1 && (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-[#1f6feb]/10 text-[#58a6ff] border border-[#1f6feb]/30">
                            Observed {mem.observation_count}x
                          </span>
                        )}
                        <span className="text-[10px] text-[#8b949e]">
                          Status:{' '}
                          <strong className={
                            String(mem.status).toUpperCase() === 'REVOKED'
                              ? 'text-[#f85149]'
                              : String(mem.status).toUpperCase() === 'SUPERSEDED'
                              ? 'text-[#d29922]'
                              : 'text-[#c9d1d9]'
                          }>
                            {mem.status}
                          </strong>
                          {mem.superseded_by && (
                            <span className="text-[10px] text-[#8b949e] ml-1">
                              (superseded by #{mem.superseded_by.slice(0, 8)})
                            </span>
                          )}
                          {mem.supersedes && (
                            <span className="text-[10px] text-[#8b949e] ml-1">
                              (supersedes #{mem.supersedes.slice(0, 8)})
                            </span>
                          )}
                          {mem.sync_state && (
                            <span
                              className={`text-[9px] font-mono px-1.5 py-0.2 rounded border ml-1 ${
                                mem.sync_state === 'SYNCED'
                                  ? 'bg-[#238636]/15 border-[#238636]/30 text-[#3fb950]'
                                  : mem.sync_state === 'FAILED'
                                  ? 'bg-[#f85149]/15 border-[#f85149]/30 text-[#f85149]'
                                  : 'bg-[#d29922]/15 border-[#d29922]/30 text-[#d29922]'
                              }`}
                              title={`Hindsight semantic recall state: ${mem.sync_state}`}
                            >
                              Hindsight: {mem.sync_state}
                            </span>
                          )}
                        </span>
                      </div>

                      <h2 className="text-sm font-semibold text-[#f0f6fc] leading-snug">
                        {mem.claim || mem.statement}
                      </h2>
                    </div>
                  </div>

                  {/* Two columns: Rationale & Scope */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px]">
                    <div className="p-2.5 rounded bg-[#161b22] border border-[#30363d] space-y-1">
                      <span className="text-[#8b949e] font-semibold block flex items-center gap-1">
                        <ShieldCheckIcon size={12} className="text-[#58a6ff]" />
                        <span>Why / Rationale</span>
                      </span>
                      <p className="text-[#c9d1d9] leading-relaxed">
                        {mem.rationale || 'Not explicitly documented in available GitHub evidence.'}
                      </p>
                    </div>

                    <div className="p-2.5 rounded bg-[#161b22] border border-[#30363d] space-y-1">
                      <span className="text-[#8b949e] font-semibold block flex items-center gap-1">
                        <TagIcon size={12} className="text-[#3fb950]" />
                        <span>Scope & Architectural Boundary</span>
                      </span>
                      <p className="text-[#c9d1d9] leading-relaxed">
                        {mem.scope || 'Not explicitly documented.'}
                      </p>
                    </div>
                  </div>

                  {/* Bottom Footer: Evidence, Source & Date */}
                  <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-[#21262d] text-[11px] text-[#8b949e]">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-[#8b949e]">Evidence:</span>
                      {(() => {
                        const rawEvidence = (mem.evidence_items && mem.evidence_items.length > 0)
                          ? mem.evidence_items
                          : (mem.source_ref ? [mem.source_ref] : [])

                        const cleanEvidence = rawEvidence.filter(
                          (ev) => ev && ev !== 'Hindsight memory' && ev !== 'Hindsight' && ev !== 'Repository Decision'
                        )

                        // Direct PR number or regex extraction from evidence
                        const targetPrNum = mem.pr_number || (() => {
                          for (const ev of rawEvidence) {
                            const m = String(ev).match(/#(\d+)/)
                            if (m) return parseInt(m[1], 10)
                          }
                          return null
                        })()

                        // Provenance-backed destination repository ID with canonical connected repo resolution
                        const targetRepoId = (() => {
                          if (mem.repository_id) {
                            const exact = repositories.find((r) => r.id === mem.repository_id)
                            if (exact) return exact.id
                          }
                          if (mem.repository_full_name) {
                            const byName = repositories.find((r) => r.full_name.toLowerCase() === mem.repository_full_name?.toLowerCase())
                            if (byName) return byName.id
                          }
                          if (mem.github_repo_id) {
                            const byGhId = repositories.find((r) => r.github_repo_id === mem.github_repo_id)
                            if (byGhId) return byGhId.id
                          }
                          // Fallback to memory's repository_id directly
                          return mem.repository_id || null
                        })()

                        if (targetPrNum && targetRepoId) {
                          return (
                            <button
                              type="button"
                              onClick={() => {
                                navigate(`/pull-requests/${encodeURIComponent(targetRepoId)}/${targetPrNum}`)
                              }}
                              className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-[#21262d] border border-[#30363d] text-[#58a6ff] hover:border-[#58a6ff] cursor-pointer font-mono text-[10px] transition-colors"
                              title={`Open PR #${targetPrNum} in repository`}
                            >
                              <GitPullRequestIcon size={11} />
                              <span>PR #{targetPrNum}</span>
                            </button>
                          )
                        }

                        if (cleanEvidence.length > 0) {
                          return cleanEvidence.map((ev, i) => (
                            <span
                              key={i}
                              className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-[#21262d] border border-[#30363d] text-[#c9d1d9] font-mono text-[10px]"
                            >
                              <span>{ev}</span>
                            </span>
                          ))
                        }

                        if (mem.source_url) {
                          return (
                            <a
                              href={mem.source_url}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-[#21262d] border border-[#30363d] text-[#58a6ff] hover:border-[#58a6ff] font-mono text-[10px] transition-colors"
                            >
                              <span>Source Reference</span>
                              <LinkExternalIcon size={10} />
                            </a>
                          )
                        }

                        return <span className="text-[#656c76] italic">Provenance unavailable</span>
                      })()}
                    </div>

                    <div className="flex items-center gap-3 text-[10px]">
                      <span>
                        Source: <strong className="text-[#c9d1d9] capitalize">{mem.source_type.replace(/_/g, ' ')}</strong>
                      </span>
                      <span>
                        Established: <strong className="text-[#c9d1d9]">{new Date(mem.created_at).toLocaleDateString()}</strong>
                      </span>
                      {mem.source_url && (
                        <a
                          href={mem.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[#58a6ff] hover:underline flex items-center gap-0.5"
                        >
                          <span>Link</span>
                          <LinkExternalIcon size={10} />
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* 4. Record Decision Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70">
          <div className="bg-[#161b22] border border-[#30363d] rounded-md max-w-lg w-full p-5 space-y-4 shadow-2xl text-xs">
            <div className="flex items-center justify-between pb-2 border-b border-[#30363d]">
              <div>
                <h3 className="text-sm font-semibold text-[#f0f6fc]">
                  Record Engineering Decision
                </h3>
                <p className="text-[11px] text-[#8b949e]">
                  Teaches persistent memory about an architectural rule, approved exception, or convention.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowModal(false)}
                className="text-[#8b949e] hover:text-[#f0f6fc] cursor-pointer"
              >
                <XIcon size={16} />
              </button>
            </div>

            {submitError && (
              <div className="p-2.5 rounded bg-[#f85149]/10 border border-[#f85149]/30 text-[#f85149] text-xs">
                {submitError}
              </div>
            )}

            <form onSubmit={handleRecordSubmit} className="space-y-3">
              <div>
                <label className="block text-[11px] font-semibold text-[#8b949e] mb-1">
                  Category Type *
                </label>
                <select
                  value={categoryType}
                  onChange={(e) => setCategoryType(e.target.value as MemoryCategory)}
                  className="w-full px-3 py-1.5 rounded-md bg-[#0d1117] border border-[#30363d] text-xs text-[#f0f6fc] focus:border-[#1f6feb] outline-none cursor-pointer"
                >
                  <option value="architecture_decision">Architecture Decision</option>
                  <option value="rejected_pattern">Rejected Pattern</option>
                  <option value="approved_exception">Approved Exception</option>
                  <option value="review_decision">Review Decision</option>
                  <option value="lesson_learned">Lesson Learned</option>
                </select>
              </div>

              <div>
                <label className="block text-[11px] font-semibold text-[#8b949e] mb-1">
                  Decision Claim / Rule *
                </label>
                <input
                  type="text"
                  required
                  placeholder="e.g. Controllers must not directly access persistence layer"
                  value={claim}
                  onChange={(e) => setClaim(e.target.value)}
                  className="w-full px-3 py-1.5 rounded-md bg-[#0d1117] border border-[#30363d] text-xs text-[#f0f6fc] focus:border-[#1f6feb] outline-none"
                />
              </div>

              <div>
                <label className="block text-[11px] font-semibold text-[#8b949e] mb-1">
                  Why / Rationale (Optional)
                </label>
                <textarea
                  rows={2}
                  placeholder="Why this was established (leave blank if not documented)"
                  value={rationale}
                  onChange={(e) => setRationale(e.target.value)}
                  className="w-full px-3 py-1.5 rounded-md bg-[#0d1117] border border-[#30363d] text-xs text-[#f0f6fc] focus:border-[#1f6feb] outline-none"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <div>
                  <label className="block text-[11px] font-semibold text-[#8b949e] mb-1">
                    Scope / Layer (Optional)
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. Controllers, src/api/*"
                    value={scope}
                    onChange={(e) => setScope(e.target.value)}
                    className="w-full px-3 py-1.5 rounded-md bg-[#0d1117] border border-[#30363d] text-xs text-[#f0f6fc] focus:border-[#1f6feb] outline-none"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-semibold text-[#8b949e] mb-1">
                    Evidence PR Reference (Optional)
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. PR #142"
                    value={sourceRef}
                    onChange={(e) => setSourceRef(e.target.value)}
                    className="w-full px-3 py-1.5 rounded-md bg-[#0d1117] border border-[#30363d] text-xs text-[#f0f6fc] focus:border-[#1f6feb] outline-none"
                  />
                </div>
              </div>

              <div>
                <label className="block text-[11px] font-semibold text-[#8b949e] mb-1">
                  Source URL (Optional)
                </label>
                <input
                  type="url"
                  placeholder="https://github.com/org/repo/pull/142"
                  value={sourceUrl}
                  onChange={(e) => setSourceUrl(e.target.value)}
                  className="w-full px-3 py-1.5 rounded-md bg-[#0d1117] border border-[#30363d] text-xs text-[#f0f6fc] focus:border-[#1f6feb] outline-none"
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-[#30363d]">
                <Button
                  type="button"
                  variant="default"
                  size="small"
                  onClick={() => setShowModal(false)}
                >
                  Cancel
                </Button>

                <Button
                  type="submit"
                  variant="primary"
                  size="small"
                  disabled={isSubmitting || !claim.trim()}
                  leadingVisual={isSubmitting ? undefined : CheckIcon}
                  loading={isSubmitting}
                >
                  {isSubmitting ? 'Retaining...' : 'Save to Memory'}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
export default MemoryExplorerPage
