import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  GitPullRequestIcon,
  GitMergeIcon,
  GitPullRequestClosedIcon,
  GitBranchIcon,
  CheckCircleIcon,
  XCircleIcon,
  AlertIcon,
  ClockIcon,
  CommentDiscussionIcon,
  FileDiffIcon,
  FileCodeIcon,
  CopyIcon,
  CheckIcon,
  SyncIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ShieldCheckIcon,
  InfoIcon,
  HistoryIcon,
  LinkExternalIcon,
  BookIcon,
  ZapIcon,
  PlayIcon,
  StopIcon,
  GitCommitIcon,
  ArrowRightIcon,
} from '@primer/octicons-react'
import {
  getPullRequestDetail,
  getPRReview,
  getPRReviewHistory,
  triggerReview,
  getPRWalkthrough,
  getPRFixes,
  generatePRFixes,
  validatePRFix,
  approvePRFix,
  applyPRFix,
  rejectPRFix,
  submitDeveloperFeedback,
  listPRLearningCandidates,
  confirmPRLearningCandidate,
  rejectPRLearningCandidate,
  getPREvidence,
} from '@/services/api'
import { useRepo } from '@/context/useRepo'
import type {
  GitHubPR,
  PRDetailResponse,
  PRWalkthrough,
  RecentReview,
  ReviewAnalysisOutput,
  ReviewFinding,
  ReviewVerdict,
  ChangeGroupImpact,
  FixCandidate,
  LearningCandidate,
  FeedbackType,
  PREvidenceResponse,
} from '@/types'

export const PullRequestReviewPage: React.FC = () => {
  const { repoId, prNumber } = useParams<{ repoId: string; prNumber: string }>()
  const parsedPrNumber = prNumber ? parseInt(prNumber, 10) : 0
  const {
    repositories,
    activeRepo,
    setActiveRepo,
    activePR,
    setActivePR,
    pullRequestsByRepoState,
    pullRequestsByRepo,
    invalidateRepoReviewState,
  } = useRepo()

  useEffect(() => {
    if (!repoId || !repositories || repositories.length === 0) return
    if (activeRepo?.id !== repoId) {
      const matched = repositories.find((r) => r.id === repoId)
      if (matched) {
        setActiveRepo(matched)
      }
    }
  }, [repoId, repositories, activeRepo, setActiveRepo])

  const [detailRaw, setDetailRaw] = useState<PRDetailResponse | null>(null)
  const [review, setReview] = useState<ReviewAnalysisOutput | null>(null)
  const [reviewHistory, setReviewHistory] = useState<RecentReview[]>([])
  const [isLoadingDetail, setIsLoadingDetail] = useState(true)
  const [isReviewing, setIsReviewing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reviewError, setReviewError] = useState<string | null>(null)

  // Attempt to resolve PR from existing frontend cache / in-memory list
  const cachedPR: GitHubPR | undefined = useMemo(() => {
    if (!parsedPrNumber) return undefined
    return (
      (activePR && activePR.number === parsedPrNumber ? activePR : undefined) ||
      (repoId ? pullRequestsByRepoState[`${repoId}:open`]?.find((p) => p.number === parsedPrNumber) : undefined) ||
      (repoId ? pullRequestsByRepoState[`${repoId}:closed`]?.find((p) => p.number === parsedPrNumber) : undefined) ||
      (repoId ? pullRequestsByRepo[repoId]?.find((p) => p.number === parsedPrNumber) : undefined)
    )
  }, [activePR, parsedPrNumber, repoId, pullRequestsByRepoState, pullRequestsByRepo])

  // Deterministically derive active detail: instant cached preview if available, full details when fetched
  const detail: PRDetailResponse | null = useMemo(() => {
    if (detailRaw && detailRaw.pr.number === parsedPrNumber) {
      return detailRaw
    }
    if (cachedPR) {
      return {
        pr: cachedPR,
        files: [],
        commits: [],
        comments: [],
        review_comments: [],
        issue_comments: [],
      }
    }
    return null
  }, [detailRaw, parsedPrNumber, cachedPR])

  // Active view tab: 'conversation' | 'files' | 'walkthrough'
  const [activeTab, setActiveTab] = useState<'conversation' | 'files' | 'walkthrough'>('conversation')
  const [copiedFixIndex, setCopiedFixIndex] = useState<number | null>(null)
  const [collapsedFiles, setCollapsedFiles] = useState<Record<string, boolean>>({})
  const [expandedTraces, setExpandedTraces] = useState<Record<number, boolean>>({})
  const [showHistoryModal, setShowHistoryModal] = useState(false)

  // Phase 7: walkthrough state
  const [walkthrough, setWalkthrough] = useState<PRWalkthrough | null>(null)
  const [isLoadingWalkthrough, setIsLoadingWalkthrough] = useState(false)
  const [walkthroughError, setWalkthroughError] = useState<string | null>(null)
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({})

  // Phase 8: Context-Aware Fixes state
  const [fixes, setFixes] = useState<FixCandidate[]>([])
  const [generatingFindingId, setGeneratingFindingId] = useState<string | null>(null)
  const [fixActionInProgress, setFixActionInProgress] = useState<Record<string, string>>({})
  const [fixErrors, setFixErrors] = useState<Record<string, string>>({})
  const [expandedFixDiffs, setExpandedFixDiffs] = useState<Record<string, boolean>>({})

  // Phase 9: Controlled Learning & Feedback state
  const [learningCandidates, setLearningCandidates] = useState<LearningCandidate[]>([])
  const [submittingFeedbackForFindingId, setSubmittingFeedbackForFindingId] = useState<string | null>(null)
  const [activeConfirmationCandidate, setActiveConfirmationCandidate] = useState<LearningCandidate | null>(null)
  const [editStatement, setEditStatement] = useState('')
  const [editRationale, setEditRationale] = useState('')
  const [editScope, setEditScope] = useState('')
  const [isConfirmingCandidate, setIsConfirmingCandidate] = useState(false)
  const [feedbackSuccessMessage, setFeedbackSuccessMessage] = useState<string | null>(null)
  const [feedbackErrorMessage, setFeedbackErrorMessage] = useState<string | null>(null)

  // Phase 10: Deep PR Evidence & History state
  const [evidence, setEvidence] = useState<PREvidenceResponse | null>(null)
  const [evidenceExpandedSection, setEvidenceExpandedSection] = useState<'commits' | 'conversation' | 'checks' | 'history' | null>('commits')
  const [isEvidencePanelOpen, setIsEvidencePanelOpen] = useState(true)

  // Map review finding with structured explainability fields
  const mapReviewFinding = (
    pf: ReviewFinding & {
      historical_evidence_snapshot?: {
        claim?: string
        rationale?: string
        scope?: string
        source_pr_ref?: string | null
        source_pr_number?: number | null
        memory_text?: string | null
        memory_category?: string | null
        provenance_status?: string
      } | null
    }
  ): ReviewFinding => {
    const snap = pf.historical_evidence_snapshot
    const evidencePrs = snap?.source_pr_ref
      ? [snap.source_pr_ref]
      : pf.historical_evidence?.evidence_prs || []

    return {
      ...pf,
      id: pf.id || `${pf.file_path}:${pf.line_number || 0}`,
      decision: pf.decision || snap?.claim || pf.title,
      rationale: pf.rationale || snap?.rationale || pf.historical_evidence?.rationale || null,
      scope: pf.scope || snap?.scope || null,
      source: pf.source || snap?.source_pr_ref || pf.historical_pr_ref || null,
      action: pf.action || pf.suggested_fix || (pf.category === 'approved_exception' ? 'No architectural correction required.' : 'Review against documented precedent.'),
      historical_evidence: snap
        ? {
            claim: snap.claim || pf.title,
            rationale: snap.rationale || undefined,
            scope: snap.scope || undefined,
            source_pr_ref: snap.source_pr_ref || undefined,
            source_pr_number: snap.source_pr_number || undefined,
            evidence_prs: evidencePrs,
            matched_code: null,
            reasoning: null,
            exception_notes: null,
            provenance_status: snap.provenance_status,
          }
        : pf.historical_evidence ?? null,
      historical_pr_ref: snap?.source_pr_ref || pf.historical_pr_ref || null,
      provenance_status:
        (pf as { provenance_status?: string }).provenance_status ||
        snap?.provenance_status ||
        'none',
    }
  }

  const [retryTrigger, setRetryTrigger] = useState(0)
  const handleRetryDetail = useCallback(() => {
    setRetryTrigger((prev) => prev + 1)
  }, [])

  useEffect(() => {
    if (!repoId || !parsedPrNumber || isNaN(parsedPrNumber)) return
    let isMounted = true
    const currentFetchRepoId = repoId
    const currentFetchPrNumber = parsedPrNumber

    queueMicrotask(() => {
      if (isMounted) {
        setIsLoadingDetail(true)
        setError(null)
        setReviewError(null)
        setReview(null)
        setDetailRaw(null)
        setFixes([])
        setFixErrors({})
        setWalkthrough(null)
        setWalkthroughError(null)
        setLearningCandidates([])
        setEvidence(null)
      }
    })

    // 1. Fetch primary PR detail immediately so UI is responsive and independent
    getPullRequestDetail(repoId, parsedPrNumber)
      .then((prData) => {
        if (!isMounted || currentFetchRepoId !== repoId || currentFetchPrNumber !== parsedPrNumber) return
        setDetailRaw(prData)
        if (prData?.pr) {
          setActivePR(prData.pr)
        }
        setIsLoadingDetail(false)
        setError(null)
      })
      .catch((err) => {
        if (!isMounted || currentFetchRepoId !== repoId || currentFetchPrNumber !== parsedPrNumber) return
        setError(err instanceof Error ? err.message : 'Unable to retrieve pull request details')
        setIsLoadingDetail(false)
      })

    // 2. Fetch secondary context, review history, fixes, candidates, and evidence non-blockingly
    Promise.all([
      getPRReview(repoId, parsedPrNumber).catch(() => null),
      getPRReviewHistory(repoId, parsedPrNumber).catch(() => []),
      getPRFixes(repoId, parsedPrNumber).catch(() => ({ fixes: [] })),
      listPRLearningCandidates(repoId, parsedPrNumber).catch(() => []),
      getPREvidence(repoId, parsedPrNumber).catch(() => null),
    ])
      .then(([existing, historyList, fixesResp, candidatesResp, evidenceResp]) => {
        if (!isMounted || currentFetchRepoId !== repoId || currentFetchPrNumber !== parsedPrNumber) return

        if (Array.isArray(historyList)) {
          setReviewHistory(historyList)
        }
        if (fixesResp && Array.isArray(fixesResp.fixes)) {
          setFixes(fixesResp.fixes)
        }
        if (Array.isArray(candidatesResp)) {
          setLearningCandidates(candidatesResp)
        }
        if (evidenceResp) {
          setEvidence(evidenceResp)
        }
        if (existing) {
          const persistedFindings = (existing.findings || []).map(mapReviewFinding)
          const computedVerdict: ReviewVerdict =
            (existing.verdict as ReviewVerdict) ||
            (existing.drift_detected
              ? 'DRIFT'
              : existing.exception_approved
              ? 'APPROVED_EXCEPTION'
              : persistedFindings.length > 0
              ? 'CONFORMING'
              : 'NO_PRECEDENT')

          setReview({
            summary: existing.summary || 'Architecture review complete.',
            drift_detected: existing.drift_detected,
            exception_approved: existing.exception_approved,
            verdict: computedVerdict,
            confidence_score: typeof (existing as { confidence_score?: number }).confidence_score === 'number'
              ? (existing as { confidence_score?: number }).confidence_score!
              : 0.94,
            commit_sha: existing.commit_sha || null,
            historical_context_snapshot: existing.historical_context_snapshot || null,
            findings: persistedFindings,
          })
        }
      })
      .catch(() => {
        // Non-blocking secondary sidecars do not fail the review page
      })

    return () => {
      isMounted = false
    }
  }, [repoId, parsedPrNumber, retryTrigger, setActivePR])

  const handleRunReview = async () => {
    if (!repoId || !parsedPrNumber || isReviewing) return
    setIsReviewing(true)
    setReviewError(null)
    try {
      const output = await triggerReview(repoId, parsedPrNumber)
      const mappedFindings = (output.findings || []).map((f) => mapReviewFinding(f as any))
      const finalOutput: ReviewAnalysisOutput = {
        ...output,
        findings: mappedFindings,
      }
      setReview(finalOutput)
      // Refresh history list
      const updatedHistory = await getPRReviewHistory(repoId, parsedPrNumber).catch(() => [])
      setReviewHistory(updatedHistory)
      // Invalidate repo review & memory caches in context so dashboard updates immediately
      await invalidateRepoReviewState(repoId).catch(() => {})
    } catch (err) {
      setReviewError(err instanceof Error ? err.message : 'Review execution failed')
    } finally {
      setIsReviewing(false)
    }
  }

  const handleGenerateFix = async (findingId?: string) => {
    if (!repoId || !parsedPrNumber || generatingFindingId) return
    setGeneratingFindingId(findingId || 'all')
    try {
      const resp = await generatePRFixes(repoId, parsedPrNumber, findingId)
      if (resp && Array.isArray(resp.fixes)) {
        setFixes((prev) => {
          const map = new Map<string, FixCandidate>()
          prev.forEach((f: FixCandidate) => map.set(f.id, f))
          resp.fixes.forEach((f: FixCandidate) => map.set(f.id, f))
          return Array.from(map.values())
        })
      }

      // If backend returned unavailable_reasons, surface them immediately
      if (resp?.unavailable_reasons && findingId && resp.unavailable_reasons[findingId]) {
        setFixErrors((prev) => ({
          ...prev,
          [findingId]: resp.unavailable_reasons![findingId],
        }))
      } else if (findingId && (!resp?.fixes || resp.fixes.length === 0)) {
        setFixErrors((prev) => ({
          ...prev,
          [findingId]: 'Fix unavailable: No safe automated fix pattern exists for this architectural rule.',
        }))
      } else if (findingId) {
        // Clear any previous fix error for this finding upon successful candidate generation
        setFixErrors((prev) => {
          const next = { ...prev }
          delete next[findingId]
          return next
        })
      }
    } catch (err) {
      if (findingId) {
        setFixErrors((prev) => ({
          ...prev,
          [findingId]: err instanceof Error ? err.message : 'Failed to generate fix',
        }))
      }
    } finally {
      setGeneratingFindingId(null)
    }
  }

  const handleValidateFix = async (fixId: string) => {
    if (!repoId || !parsedPrNumber) return
    setFixActionInProgress((prev) => ({ ...prev, [fixId]: 'validating' }))
    setFixErrors((prev) => {
      const next = { ...prev }
      delete next[fixId]
      return next
    })
    try {
      const updated = await validatePRFix(repoId, parsedPrNumber, fixId)
      setFixes((prev) => prev.map((f) => (f.id === fixId ? updated : f)))
    } catch (err) {
      setFixErrors((prev) => ({
        ...prev,
        [fixId]: err instanceof Error ? err.message : 'Validation failed',
      }))
    } finally {
      setFixActionInProgress((prev) => {
        const next = { ...prev }
        delete next[fixId]
        return next
      })
    }
  }

  const handleApproveFix = async (fixId: string) => {
    if (!repoId || !parsedPrNumber) return
    setFixActionInProgress((prev) => ({ ...prev, [fixId]: 'approving' }))
    try {
      const updated = await approvePRFix(repoId, parsedPrNumber, fixId)
      setFixes((prev) => prev.map((f) => (f.id === fixId ? updated : f)))
    } catch (err) {
      setFixErrors((prev) => ({
        ...prev,
        [fixId]: err instanceof Error ? err.message : 'Approval failed',
      }))
    } finally {
      setFixActionInProgress((prev) => {
        const next = { ...prev }
        delete next[fixId]
        return next
      })
    }
  }

  const handleApplyFix = async (fixId: string) => {
    if (!repoId || !parsedPrNumber) return
    setFixActionInProgress((prev) => ({ ...prev, [fixId]: 'applying' }))
    try {
      const updated = await applyPRFix(repoId, parsedPrNumber, fixId)
      setFixes((prev) => prev.map((f) => (f.id === fixId ? updated : f)))
      // Refresh review history / state
      await invalidateRepoReviewState(repoId).catch(() => {})
    } catch (err) {
      setFixErrors((prev) => ({
        ...prev,
        [fixId]: err instanceof Error ? err.message : 'Apply failed',
      }))
    } finally {
      setFixActionInProgress((prev) => {
        const next = { ...prev }
        delete next[fixId]
        return next
      })
    }
  }

  const handleRejectFix = async (fixId: string) => {
    if (!repoId || !parsedPrNumber) return
    setFixActionInProgress((prev) => ({ ...prev, [fixId]: 'rejecting' }))
    try {
      const updated = await rejectPRFix(repoId, parsedPrNumber, fixId)
      setFixes((prev) => prev.map((f) => (f.id === fixId ? updated : f)))
    } catch (err) {
      setFixErrors((prev) => ({
        ...prev,
        [fixId]: err instanceof Error ? err.message : 'Reject failed',
      }))
    } finally {
      setFixActionInProgress((prev) => {
        const next = { ...prev }
        delete next[fixId]
        return next
      })
    }
  }

  // =========================================================================
  // PHASE 9: DEVELOPER FEEDBACK & CONTROLLED LEARNING ACTIONS
  // =========================================================================

  const handleSubmitFeedback = async (
    finding: ReviewFinding,
    feedbackType: FeedbackType,
    customStatement?: string
  ) => {
    if (!repoId || !parsedPrNumber || submittingFeedbackForFindingId) return
    const findingKey = finding.id || `${finding.file_path}:${finding.line_number || 0}`
    setSubmittingFeedbackForFindingId(`${findingKey}:${feedbackType}`)
    setFeedbackErrorMessage(null)
    setFeedbackSuccessMessage(null)

    try {
      const candidate = await submitDeveloperFeedback(repoId, parsedPrNumber, {
        feedback_type: feedbackType,
        finding_id: finding.id || undefined,
        finding_title: finding.title,
        statement: customStatement || finding.decision || finding.title,
        rationale: finding.rationale || finding.message,
        scope: finding.scope || finding.file_path,
        file_path: finding.file_path,
        commit_sha: detail?.pr.head_sha,
      })

      setLearningCandidates((prev) => [candidate, ...prev.filter((c) => c.candidate_id !== candidate.candidate_id)])

      if (candidate.status === 'REQUIRES_CONFIRMATION') {
        // Automatically open confirmation modal for developer to review and confirm
        setActiveConfirmationCandidate(candidate)
        setEditStatement(candidate.statement)
        setEditRationale(candidate.rationale)
        setEditScope(candidate.scope)
      } else if (candidate.status === 'CONFLICTING') {
        setFeedbackErrorMessage(
          `Conflict detected: ${candidate.validation_reasons.join(' ')}`
        )
      } else if (candidate.status === 'FAILED') {
        setFeedbackErrorMessage(
          `Candidate failed validation: ${candidate.validation_reasons.join(' ')}`
        )
      }
    } catch (err) {
      setFeedbackErrorMessage(err instanceof Error ? err.message : 'Feedback submission failed')
    } finally {
      setSubmittingFeedbackForFindingId(null)
    }
  }

  const handleOpenConfirmModal = (candidate: LearningCandidate) => {
    setActiveConfirmationCandidate(candidate)
    setEditStatement(candidate.statement)
    setEditRationale(candidate.rationale)
    setEditScope(candidate.scope)
    setFeedbackErrorMessage(null)
  }

  const handleConfirmCandidate = async () => {
    if (!repoId || !parsedPrNumber || !activeConfirmationCandidate || isConfirmingCandidate) return
    setIsConfirmingCandidate(true)
    setFeedbackErrorMessage(null)

    try {
      const resp = await confirmPRLearningCandidate(
        repoId,
        parsedPrNumber,
        activeConfirmationCandidate.candidate_id,
        {
          confirmed_statement: editStatement,
          confirmed_rationale: editRationale,
          confirmed_scope: editScope,
        }
      )

      setLearningCandidates((prev) =>
        prev.map((c) => (c.candidate_id === resp.candidate.candidate_id ? resp.candidate : c))
      )
      setFeedbackSuccessMessage(
        activeConfirmationCandidate.proposed_authority === 'ENFORCEABLE'
          ? 'Architectural memory confirmed and recorded! Future reviews in this repository will apply this guidance.'
          : activeConfirmationCandidate.proposed_authority === 'EXCEPTION'
          ? 'Architectural exception confirmed and recorded for this scoped pattern.'
          : 'Review observation precedent confirmed and recorded for contextual reference.'
      )
      setActiveConfirmationCandidate(null)
      // Invalidate memory caches
      await invalidateRepoReviewState(repoId).catch(() => {})
    } catch (err) {
      setFeedbackErrorMessage(err instanceof Error ? err.message : 'Confirmation failed')
    } finally {
      setIsConfirmingCandidate(false)
    }
  }

  const handleRejectCandidate = async (candidateId: string) => {
    if (!repoId || !parsedPrNumber) return
    try {
      const rejected = await rejectPRLearningCandidate(repoId, parsedPrNumber, candidateId, {
        reason: 'Declined by developer in PR review workspace',
      })
      setLearningCandidates((prev) =>
        prev.map((c) => (c.candidate_id === candidateId ? rejected : c))
      )
      if (activeConfirmationCandidate?.candidate_id === candidateId) {
        setActiveConfirmationCandidate(null)
      }
    } catch (err) {
      setFeedbackErrorMessage(err instanceof Error ? err.message : 'Rejection failed')
    }
  }

  const toggleFixDiff = (fixId: string) => {
    setExpandedFixDiffs((prev) => ({ ...prev, [fixId]: !prev[fixId] }))
  }

  const handleCopyFix = (text: string, index: number) => {
    navigator.clipboard.writeText(text)
    setCopiedFixIndex(index)
    setTimeout(() => setCopiedFixIndex(null), 2000)
  }

  const toggleFileCollapse = (filename: string) => {
    setCollapsedFiles((prev) => ({ ...prev, [filename]: !prev[filename] }))
  }

  const handleWalkthroughTabClick = async () => {
    setActiveTab('walkthrough')
    if (walkthrough || isLoadingWalkthrough || !repoId || !parsedPrNumber) return
    setIsLoadingWalkthrough(true)
    setWalkthroughError(null)
    try {
      const wt = await getPRWalkthrough(repoId, parsedPrNumber)
      setWalkthrough(wt)
      // Expand the first group by default
      if (wt.groups.length > 0) {
        setExpandedGroups({ [wt.groups[0].id]: true })
      }
    } catch (err) {
      setWalkthroughError(err instanceof Error ? err.message : 'Failed to load walkthrough')
    } finally {
      setIsLoadingWalkthrough(false)
    }
  }

  const toggleGroupExpand = (groupId: string) => {
    setExpandedGroups((prev) => ({ ...prev, [groupId]: !prev[groupId] }))
  }

  if (isLoadingDetail) {
    return (
      <div className="p-8 space-y-4" role="status" aria-label="Loading review">
        <div className="h-7 bg-[#161b22] rounded w-1/3 animate-pulse" />
        <div className="h-16 bg-[#161b22] rounded-md animate-pulse" />
        <div className="h-64 bg-[#161b22] rounded-md animate-pulse" />
      </div>
    )
  }

  if (error || !detail) {
    const isRepoNotFound = error && (
      error.toLowerCase().includes('not found in connected repositories') ||
      error.toLowerCase().includes('repository no longer connected') ||
      error.toLowerCase().includes('repository with id')
    )

    if (isRepoNotFound) {
      return (
        <div className="p-8 bg-[#161b22] border border-[#30363d] rounded-md text-center space-y-4 max-w-lg mx-auto mt-8">
          <AlertIcon size={32} className="text-[#d29922] mx-auto" />
          <div className="space-y-1">
            <h2 className="text-base font-semibold text-[#f0f6fc]">
              Repository no longer connected
            </h2>
            <p className="text-xs text-[#8b949e]">
              The repository associated with this pull request is not found in your connected repositories.
            </p>
          </div>
          <div className="flex items-center justify-center gap-3 pt-2">
            <Link
              to="/repositories"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[#238636] text-white text-xs font-medium hover:bg-[#2ea043] transition-colors focus:ring-2 focus:ring-[#58a6ff] outline-none"
            >
              Reconnect repository
            </Link>
            <Link
              to="/pull-requests"
              className="inline-block px-3 py-1.5 rounded-md bg-[#21262d] border border-[#30363d] text-xs font-medium text-[#c9d1d9] hover:bg-[#30363d] transition-colors focus:ring-2 focus:ring-[#58a6ff] outline-none"
            >
              Back to Pull Requests
            </Link>
          </div>
        </div>
      )
    }

    return (
      <div className="p-8 bg-[#161b22] border border-[#30363d] rounded-md text-center space-y-3">
        <AlertIcon size={28} className="text-[#f85149] mx-auto" />
        <div className="text-sm font-semibold text-[#f0f6fc]">
          {error || 'Pull request not found'}
        </div>
        <div className="flex items-center justify-center gap-3 pt-2">
          <button
            type="button"
            onClick={handleRetryDetail}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[#238636] text-white text-xs font-medium hover:bg-[#2ea043] transition-colors focus:ring-2 focus:ring-[#58a6ff] outline-none cursor-pointer"
          >
            Retry Fetch
          </button>
          <Link
            to="/pull-requests"
            className="inline-block px-3 py-1.5 rounded-md bg-[#21262d] border border-[#30363d] text-xs font-medium text-[#c9d1d9] hover:bg-[#30363d] focus:ring-2 focus:ring-[#58a6ff] outline-none"
          >
            Back to Pull Requests
          </Link>
        </div>
      </div>
    )
  }

  const pr = detail.pr
  const isOpen = pr.state === 'open'
  const isMerged = pr.state === 'merged' || pr.is_merged === true

  // Check commit SHA freshness / outdated state
  const isOutdated = Boolean(
    review &&
    review.commit_sha &&
    pr.head_sha &&
    review.commit_sha !== pr.head_sha
  )

  // Canonical verdict
  const verdict: ReviewVerdict =
    review?.verdict ||
    (review?.drift_detected
      ? 'DRIFT'
      : review?.exception_approved
      ? 'APPROVED_EXCEPTION'
      : (review?.findings?.length || 0) > 0
      ? 'CONFORMING'
      : 'NO_PRECEDENT')

  return (
    <div className="space-y-4">
      {/* 1. GITHUB PR TITLE & NUMBER */}
      <div className="space-y-2 pb-3 border-b border-[#30363d]">
        <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
          <div className="space-y-1 min-w-0">
            <h1 className="text-2xl sm:text-3xl font-normal text-[#f0f6fc] tracking-tight leading-snug break-words">
              <span>{pr.title} </span>
              <span className="text-[#8b949e] font-light">#{pr.number}</span>
            </h1>

            {/* GitHub State Badge & Branch Sentence */}
            <div className="flex items-center gap-2 text-xs text-[#8b949e] flex-wrap pt-1">
              <span
                className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold ${
                  isMerged
                    ? 'bg-[#8957e5] text-white'
                    : isOpen
                    ? 'bg-[#238636] text-white'
                    : 'bg-[#8b949e] text-white'
                }`}
              >
                {isMerged ? (
                  <GitMergeIcon size={14} />
                ) : isOpen ? (
                  <GitPullRequestIcon size={14} />
                ) : (
                  <GitPullRequestClosedIcon size={14} />
                )}
                <span className="capitalize">{pr.state}</span>
              </span>

              <span>
                <strong className="text-[#f0f6fc]">@{pr.author}</strong> wants to merge{' '}
                {detail.commits?.length || 1} commit into{' '}
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#21262d] text-[#4493f8] font-mono text-[11px] border border-[#30363d]">
                  <GitBranchIcon size={12} />
                  <span>{pr.base_branch || 'main'}</span>
                </span>{' '}
                from{' '}
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#21262d] text-[#4493f8] font-mono text-[11px] border border-[#30363d]">
                  <GitBranchIcon size={12} />
                  <span>{pr.head_sha ? pr.head_sha.slice(0, 7) : 'head'}</span>
                </span>
              </span>

              <span>•</span>
              <span className="flex items-center gap-1">
                <ClockIcon size={14} />
                <span>{new Date(pr.created_at).toLocaleDateString()}</span>
              </span>

              {/* Review History Trigger */}
              {reviewHistory.length > 1 && (
                <>
                  <span>•</span>
                  <button
                    type="button"
                    onClick={() => setShowHistoryModal(true)}
                    className="inline-flex items-center gap-1 text-[#58a6ff] hover:underline cursor-pointer focus:ring-2 focus:ring-[#58a6ff] outline-none"
                    aria-label="View review history generations"
                  >
                    <HistoryIcon size={13} />
                    <span>{reviewHistory.length} review generations</span>
                  </button>
                </>
              )}
            </div>
          </div>

          {/* Action Trigger: Review Changes Button (Green) */}
          <div className="shrink-0 flex items-center gap-2 pt-1">
            <button
              type="button"
              onClick={handleRunReview}
              disabled={isReviewing}
              className="px-3.5 py-1.5 rounded-md bg-[#238636] hover:bg-[#2ea043] disabled:opacity-60 text-white font-medium text-xs flex items-center gap-2 transition-colors cursor-pointer border border-[rgba(240,246,252,0.1)] shadow-xs focus:ring-2 focus:ring-[#58a6ff] outline-none"
            >
              <SyncIcon size={14} className={isReviewing ? 'animate-spin' : ''} />
              <span>
                {isReviewing
                  ? 'Evaluating architectural decisions...'
                  : review
                  ? 'Re-evaluate architecture'
                  : 'Run architecture review'}
              </span>
            </button>
          </div>
        </div>

        {/* OUTDATED REVIEW BANNER */}
        {isOutdated && (
          <div
            role="alert"
            className="p-3 bg-[#d29922]/10 border border-[#d29922]/40 rounded-md text-xs text-[#d29922] flex items-center justify-between gap-3 mt-2"
          >
            <div className="flex items-center gap-2">
              <AlertIcon size={16} className="shrink-0 text-[#d29922]" />
              <span>
                <strong>Review outdated:</strong> PR head has advanced to{' '}
                <code className="px-1 py-0.5 rounded bg-[#21262d] font-mono text-[#f0f6fc]">
                  {pr.head_sha.slice(0, 7)}
                </code>{' '}
                (reviewed commit was{' '}
                <code className="px-1 py-0.5 rounded bg-[#21262d] font-mono text-[#c9d1d9]">
                  {review?.commit_sha ? review.commit_sha.slice(0, 7) : 'previous'}
                </code>
                ).
              </span>
            </div>
            <button
              type="button"
              onClick={handleRunReview}
              disabled={isReviewing}
              className="shrink-0 px-2.5 py-1 rounded bg-[#21262d] hover:bg-[#30363d] text-[#f0f6fc] text-[11px] font-medium border border-[#30363d] transition-colors cursor-pointer focus:ring-2 focus:ring-[#58a6ff] outline-none"
            >
              Review latest commit
            </button>
          </div>
        )}

        {reviewError && (
          <div role="alert" className="p-3 bg-[rgba(248,81,73,0.1)] border border-[#f85149]/40 rounded-md text-xs text-[#f85149] flex items-center gap-2 mt-2">
            <AlertIcon size={16} className="shrink-0" />
            <span>{reviewError}</span>
          </div>
        )}

        {feedbackSuccessMessage && (
          <div role="alert" className="p-3 bg-[#238636]/10 border border-[#238636]/40 rounded-md text-xs text-[#3fb950] flex items-center justify-between gap-2 mt-2">
            <div className="flex items-center gap-2">
              <CheckCircleIcon size={16} className="shrink-0 text-[#3fb950]" />
              <span>{feedbackSuccessMessage}</span>
            </div>
            <button
              type="button"
              onClick={() => setFeedbackSuccessMessage(null)}
              className="text-[#8b949e] hover:text-[#f0f6fc] text-xs cursor-pointer"
            >
              Dismiss
            </button>
          </div>
        )}

        {feedbackErrorMessage && (
          <div role="alert" className="p-3 bg-[#f85149]/10 border border-[#f85149]/40 rounded-md text-xs text-[#f85149] flex items-center justify-between gap-2 mt-2">
            <div className="flex items-center gap-2">
              <AlertIcon size={16} className="shrink-0 text-[#f85149]" />
              <span>{feedbackErrorMessage}</span>
            </div>
            <button
              type="button"
              onClick={() => setFeedbackErrorMessage(null)}
              className="text-[#8b949e] hover:text-[#f0f6fc] text-xs cursor-pointer"
            >
              Dismiss
            </button>
          </div>
        )}
      </div>

      {/* 2. GITHUB UNDERLINE TABS (Conversation / Files Changed / Changes) */}
      <div className="flex items-center gap-3 border-b border-[#30363d] text-xs">
        <button
          type="button"
          onClick={() => setActiveTab('conversation')}
          className={`pb-2.5 px-2 font-medium transition-colors cursor-pointer flex items-center gap-2 relative focus:ring-2 focus:ring-[#58a6ff] outline-none ${
            activeTab === 'conversation'
              ? 'text-[#f0f6fc] border-b-2 border-[#f78166] font-semibold'
              : 'text-[#9198a1] hover:text-[#f0f6fc]'
          }`}
        >
          <CommentDiscussionIcon size={16} />
          <span>Conversation</span>
          {review && (
            <span className="px-1.5 py-0.2 rounded-full bg-[#21262d] text-[#c9d1d9] text-[10px] font-semibold border border-[#30363d]">
              {review.findings.length}
            </span>
          )}
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('files')}
          className={`pb-2.5 px-2 font-medium transition-colors cursor-pointer flex items-center gap-2 relative focus:ring-2 focus:ring-[#58a6ff] outline-none ${
            activeTab === 'files'
              ? 'text-[#f0f6fc] border-b-2 border-[#f78166] font-semibold'
              : 'text-[#9198a1] hover:text-[#f0f6fc]'
          }`}
        >
          <FileDiffIcon size={16} />
          <span>Files changed</span>
          <span className="px-1.5 py-0.2 rounded-full bg-[#21262d] text-[#c9d1d9] text-[10px] font-semibold border border-[#30363d]">
            {detail.files.length}
          </span>
        </button>

        <button
          type="button"
          onClick={handleWalkthroughTabClick}
          className={`pb-2.5 px-2 font-medium transition-colors cursor-pointer flex items-center gap-2 relative focus:ring-2 focus:ring-[#58a6ff] outline-none ${
            activeTab === 'walkthrough'
              ? 'text-[#f0f6fc] border-b-2 border-[#f78166] font-semibold'
              : 'text-[#9198a1] hover:text-[#f0f6fc]'
          }`}
          aria-label="Semantic PR walkthrough — grouped view of changes"
        >
          <FileCodeIcon size={16} />
          <span>Changes</span>
          {walkthrough && (
            <span className="px-1.5 py-0.2 rounded-full bg-[#21262d] text-[#c9d1d9] text-[10px] font-semibold border border-[#30363d]">
              {walkthrough.groups_count}
            </span>
          )}
        </button>
      </div>

      {/* 3. TAB CONTENT */}
      {activeTab === 'conversation' && (
        <div className="space-y-4 max-w-4xl">
          {/* PR Description Box (Native GitHub Timeline Item) */}
          <div className="flex items-start gap-3">
            <div className="w-8 h-8 rounded-full bg-[#21262d] border border-[#30363d] flex items-center justify-center text-xs font-semibold text-[#4493f8] shrink-0 mt-0.5">
              {pr.author.slice(0, 2).toUpperCase()}
            </div>

            <div className="flex-1 rounded-md border border-[#30363d] bg-[#0d1117] overflow-hidden">
              <div className="bg-[#161b22] px-3.5 py-2 border-b border-[#30363d] text-xs text-[#8b949e] flex items-center justify-between">
                <span>
                  <strong className="text-[#f0f6fc]">@{pr.author}</strong> commented{' '}
                  {new Date(pr.created_at).toLocaleDateString()}
                </span>
                <span className="px-1.5 py-0.2 rounded-full border border-[#30363d] text-[10px] text-[#8b949e]">
                  Author
                </span>
              </div>
              <div className="p-4 text-xs text-[#f0f6fc] leading-relaxed whitespace-pre-wrap">
                {pr.body ? pr.body : <span className="text-[#8b949e] italic">No description provided.</span>}
              </div>
            </div>
          </div>

          {/* CANONICAL VERDICT SUITE CARD */}
          <div className="flex items-start gap-3">
            <div
              className={`w-8 h-8 rounded-full border flex items-center justify-center shrink-0 mt-0.5 ${
                verdict === 'DRIFT'
                  ? 'bg-[#f85149]/15 border-[#f85149]/40 text-[#f85149]'
                  : verdict === 'APPROVED_EXCEPTION'
                  ? 'bg-[#8957e5]/15 border-[#8957e5]/40 text-[#a371f7]'
                  : verdict === 'CONFORMING'
                  ? 'bg-[#3fb950]/15 border-[#3fb950]/40 text-[#3fb950]'
                  : verdict === 'CONFLICTING_GUIDANCE'
                  ? 'bg-[#d29922]/15 border-[#d29922]/40 text-[#d29922]'
                  : 'bg-[#21262d] border-[#30363d] text-[#8b949e]'
              }`}
            >
              {verdict === 'DRIFT' && <XCircleIcon size={16} />}
              {verdict === 'APPROVED_EXCEPTION' && <CheckCircleIcon size={16} />}
              {verdict === 'CONFORMING' && <CheckCircleIcon size={16} />}
              {verdict === 'CONFLICTING_GUIDANCE' && <AlertIcon size={16} />}
              {verdict === 'NO_PRECEDENT' && <InfoIcon size={16} />}
            </div>

            <div className="flex-1 rounded-md border border-[#30363d] bg-[#0d1117] overflow-hidden">
              {/* Verdict Header Banner */}
              <div
                className={`px-4 py-3 border-b border-[#30363d] flex items-center justify-between flex-wrap gap-2 ${
                  verdict === 'DRIFT'
                    ? 'bg-[#f85149]/10'
                    : verdict === 'APPROVED_EXCEPTION'
                    ? 'bg-[#8957e5]/10'
                    : verdict === 'CONFORMING'
                    ? 'bg-[#3fb950]/10'
                    : verdict === 'CONFLICTING_GUIDANCE'
                    ? 'bg-[#d29922]/10'
                    : 'bg-[#161b22]'
                }`}
              >
                <div className="flex items-center gap-2">
                  {verdict === 'DRIFT' && <XCircleIcon size={18} className="text-[#f85149]" />}
                  {verdict === 'APPROVED_EXCEPTION' && <CheckCircleIcon size={18} className="text-[#a371f7]" />}
                  {verdict === 'CONFORMING' && <CheckCircleIcon size={18} className="text-[#3fb950]" />}
                  {verdict === 'CONFLICTING_GUIDANCE' && <AlertIcon size={18} className="text-[#d29922]" />}
                  {verdict === 'NO_PRECEDENT' && <InfoIcon size={18} className="text-[#8b949e]" />}

                  <span className="font-semibold text-xs text-[#f0f6fc]">
                    {!review
                      ? 'Architecture Review Pending'
                      : verdict === 'DRIFT'
                      ? 'Architecture Drift Detected — Review Required'
                      : verdict === 'APPROVED_EXCEPTION'
                      ? 'Architectural Conformance Verified — Approved Exception'
                      : verdict === 'CONFORMING'
                      ? 'Architectural Conformance'
                      : verdict === 'CONFLICTING_GUIDANCE'
                      ? 'Conflicting Architectural Guidance'
                      : 'No Historical Precedent'}
                  </span>
                </div>

                <div className="flex items-center gap-2 text-[11px] text-[#8b949e]">
                  {review?.commit_sha && (
                    <span className="font-mono">
                      commit {review.commit_sha.slice(0, 7)}
                    </span>
                  )}
                  <span>•</span>
                  <span>
                    {review ? `${review.findings.length} ${review.findings.length === 1 ? 'finding' : 'findings'} evaluated` : 'Ready to evaluate'}
                  </span>
                </div>
              </div>

              {/* Verdict Summary & Neutral Explanations */}
              <div className="p-4 space-y-4">
                {review ? (
                  <div className="space-y-2">
                    <p className="text-xs text-[#c9d1d9] leading-relaxed">
                      {review.summary}
                    </p>

                    {/* Explicit NO_PRECEDENT Neutral Explanation */}
                    {verdict === 'NO_PRECEDENT' && (
                      <div className="p-3 rounded border border-[#30363d] bg-[#161b22] text-xs text-[#8b949e] space-y-1">
                        <div className="flex items-center gap-1.5 font-semibold text-[#c9d1d9]">
                          <InfoIcon size={13} className="text-[#8b949e]" />
                          <span>Precedent Evaluation</span>
                        </div>
                        <p className="leading-relaxed">
                          This change does not match an established repository rule. This does not mean the implementation is incorrect or approved. It means PRism found insufficient applicable institutional evidence to make an architectural determination. Review against current project requirements or establish a documented architectural decision if this pattern should become a repository rule.
                        </p>
                      </div>
                    )}

                    {/* Explicit CONFLICTING_GUIDANCE Explanation */}
                    {verdict === 'CONFLICTING_GUIDANCE' && (
                      <div className="p-3 rounded border border-[#d29922]/40 bg-[#d29922]/10 text-xs text-[#d29922] space-y-1">
                        <div className="flex items-center gap-1.5 font-semibold text-[#f0f6fc]">
                          <AlertIcon size={14} className="text-[#d29922]" />
                          <span>Multiple Active Authoritative Rules In Conflict</span>
                        </div>
                        <p className="leading-relaxed text-[#c9d1d9]">
                          Multiple active authoritative sources apply to this change but conflict. PRism has not selected a winner. Please review and clarify the governing repository standard.
                        </p>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-xs text-[#8b949e]">
                    Click &ldquo;Run architecture review&rdquo; above to evaluate this pull request against team architectural decisions and precedents.
                  </div>
                )}

              </div>
            </div>
          </div>

          {/* Quick Jump to Changes & Review Intelligence */}
          <div className="p-4 rounded-md border border-[#30363d] bg-[#161b22] flex items-center justify-between gap-4">
            <div className="space-y-1">
              <div className="text-xs font-semibold text-[#f0f6fc] flex items-center gap-2">
                <FileCodeIcon size={14} className="text-[#58a6ff]" />
                <span>Review Intelligence & Changes</span>
              </div>
              <p className="text-xs text-[#8b949e]">
                {review
                  ? `${review.findings.length} findings evaluated. View semantic walkthrough, institutional precedents, context-aware fixes, and developer actions.`
                  : "Run architecture review to evaluate changes and generate fixes."}
              </p>
            </div>
            <button
              type="button"
              onClick={handleWalkthroughTabClick}
              className="px-3.5 py-1.5 rounded-md bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-medium shrink-0 flex items-center gap-1.5 cursor-pointer shadow-xs focus:ring-2 focus:ring-[#58a6ff] outline-none"
            >
              <span>View Changes</span>
              <ArrowRightIcon size={14} />
            </button>
          </div>

          {/* GitHub Conversation / Discussion Items from Evidence */}
          {evidence?.conversation && evidence.conversation.length > 0 && (
            <div className="space-y-3 pt-2">
              <div className="text-xs font-semibold text-[#8b949e] uppercase tracking-wider">
                Conversation Activity ({evidence.conversation.length})
              </div>
              <div className="space-y-2">
                {evidence.conversation.map((item) => (
                  <div
                    key={item.id}
                    className="p-3 rounded-md border border-[#30363d] bg-[#0d1117] text-xs space-y-1.5"
                  >
                    <div className="flex items-center justify-between text-[11px] text-[#8b949e]">
                      <div className="flex items-center gap-2">
                        <strong className="text-[#f0f6fc]">@{item.author}</strong>
                        <span className="px-1.5 py-0.2 rounded bg-[#161b22] border border-[#30363d] text-[10px] text-[#8b949e]">
                          {item.source_type}
                        </span>
                        {item.file_path && (
                          <span className="font-mono text-[#c9d1d9] text-[10px] truncate max-w-xs">
                            {item.file_path}{item.line ? `:${item.line}` : ""}
                          </span>
                        )}
                      </div>
                      <span>{new Date(item.created_at).toLocaleDateString()}</span>
                    </div>
                    <div className="text-xs text-[#c9d1d9] whitespace-pre-wrap leading-relaxed">
                      {item.body}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* 4. FILES CHANGED TAB */}
      {activeTab === 'files' && (
        <div className="space-y-4 max-w-5xl">
          <div className="text-xs text-[#8b949e] flex items-center justify-between pb-1 border-b border-[#21262d]">
            <span>
              Showing <strong className="text-[#f0f6fc]">{detail.files.length}</strong> changed files
            </span>
          </div>

          {detail.files.map((file, fileIdx) => {
            const isCollapsed = collapsedFiles[file.filename]
            const findingsForFile = review?.findings.filter((f) => f.file_path === file.filename) || []

            return (
              <div
                key={file.filename}
                id={`diff-${fileIdx}`}
                className="rounded-md border border-[#30363d] bg-[#0d1117] overflow-hidden text-xs"
              >
                {/* File Header */}
                <div
                  onClick={() => toggleFileCollapse(file.filename)}
                  className="bg-[#161b22] px-3.5 py-2.5 border-b border-[#30363d] flex items-center justify-between cursor-pointer select-none hover:bg-[#21262d]/50 transition-colors"
                >
                  <div className="flex items-center gap-2 font-mono text-xs text-[#f0f6fc]">
                    <span className="text-[#8b949e]">
                      {isCollapsed ? <ChevronRightIcon size={14} /> : <ChevronDownIcon size={14} />}
                    </span>
                    <FileCodeIcon size={14} className="text-[#8b949e]" />
                    <span className="font-semibold">{file.filename}</span>
                    {findingsForFile.length > 0 && (
                      <span className="px-1.5 py-0.2 rounded-full bg-[#f85149]/20 border border-[#f85149]/40 text-[#f85149] text-[10px] font-semibold">
                        {findingsForFile.length} {findingsForFile.length === 1 ? 'issue' : 'issues'}
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-2 font-mono text-[11px]">
                    <span className="text-[#3fb950]">+{file.additions}</span>
                    <span className="text-[#f85149]">-{file.deletions}</span>
                  </div>
                </div>

                {/* Diff Body */}
                {!isCollapsed && (
                  <div>
                    {file.patch ? (
                      <pre className="p-3 bg-[#010409] text-[11px] font-mono leading-relaxed overflow-x-auto m-0 text-[#c9d1d9]">
                        {file.patch.split('\n').map((line, lIdx) => {
                          const isAdd = line.startsWith('+') && !line.startsWith('+++')
                          const isDel = line.startsWith('-') && !line.startsWith('---')
                          const isHunk = line.startsWith('@@')

                          return (
                            <div
                              key={lIdx}
                              className={`px-2 py-0.5 ${
                                isAdd
                                  ? 'bg-[#238636]/20 text-[#7ee787]'
                                  : isDel
                                  ? 'bg-[#da3633]/20 text-[#ffa198]'
                                  : isHunk
                                  ? 'bg-[#161b22] text-[#8b949e] font-semibold'
                                  : 'text-[#c9d1d9]'
                              }`}
                            >
                              {line}
                            </div>
                          )
                        })}
                      </pre>
                    ) : (
                      <div className="p-4 text-center text-xs text-[#8b949e] italic">
                        Binary file or diff not available.
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* WALKTHROUGH TAB PANEL */}
      {activeTab === 'walkthrough' && (
        <div className="space-y-4 max-w-4xl">
          {isLoadingWalkthrough && (
            <div className="space-y-3 animate-pulse" role="status" aria-label="Loading walkthrough">
              <div className="h-10 bg-[#161b22] rounded-md w-2/3" />
              <div className="h-24 bg-[#161b22] rounded-md" />
              <div className="h-24 bg-[#161b22] rounded-md" />
            </div>
          )}

          {walkthroughError && (
            <div role="alert" className="p-3 bg-[rgba(248,81,73,0.1)] border border-[#f85149]/40 rounded-md text-xs text-[#f85149] flex items-center gap-2">
              <AlertIcon size={16} className="shrink-0" />
              <span>{walkthroughError}</span>
            </div>
          )}

          {walkthrough && !isLoadingWalkthrough && (
            <>
              {/* Walkthrough header */}
              <div className="p-4 bg-[#161b22] border border-[#30363d] rounded-md space-y-3">
                <div className="flex items-start justify-between gap-4 flex-wrap">
                  <div className="space-y-1.5 min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-semibold uppercase tracking-wider text-[#58a6ff]">Semantic PR Walkthrough</span>
                      <span className="px-2 py-0.5 rounded-full bg-[#21262d] border border-[#30363d] text-[10px] font-semibold text-[#8b949e] uppercase tracking-wide">
                        {walkthrough.change_type.replace(/_/g, ' ')}
                      </span>
                      {walkthrough.has_schema_changes && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-[#d29922]/15 border border-[#d29922]/40 text-[#d29922]">Schema changes</span>
                      )}
                      {walkthrough.has_api_changes && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-[#58a6ff]/15 border border-[#58a6ff]/40 text-[#4493f8]">API changes</span>
                      )}
                      {walkthrough.has_test_changes && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-[#3fb950]/15 border border-[#3fb950]/40 text-[#3fb950]">Tests included</span>
                      )}
                      {walkthrough.has_config_changes && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-[#8957e5]/15 border border-[#8957e5]/40 text-[#a371f7]">Config changes</span>
                      )}
                      {walkthrough.is_truncated && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-[#d29922]/10 border border-[#d29922]/30 text-[#d29922]">
                          Bounded payload
                        </span>
                      )}
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
                      <div className="bg-[#0d1117] p-2.5 rounded border border-[#21262d] text-xs">
                        <span className="text-[10px] font-semibold text-[#8b949e] uppercase tracking-wider block mb-1">Declared Intent</span>
                        <p className="text-[#c9d1d9] leading-relaxed">{walkthrough.declared_intent || walkthrough.intent_summary}</p>
                      </div>
                      <div className="bg-[#0d1117] p-2.5 rounded border border-[#21262d] text-xs">
                        <span className="text-[10px] font-semibold text-[#8b949e] uppercase tracking-wider block mb-1">Observed Implementation</span>
                        <p className="text-[#c9d1d9] leading-relaxed">{walkthrough.observed_implementation || walkthrough.intent_summary}</p>
                      </div>
                    </div>
                  </div>

                  <div className="shrink-0 flex items-center gap-4 text-xs text-[#8b949e] bg-[#0d1117] px-3 py-2 rounded border border-[#21262d]">
                    <span><strong className="text-[#f0f6fc]">{walkthrough.total_files_changed}</strong> files</span>
                    <span className="text-[#3fb950]">+{walkthrough.total_additions}</span>
                    <span className="text-[#f85149]">−{walkthrough.total_deletions}</span>
                    {walkthrough.surfaced_finding_count > 0 && (
                      <span className="text-[#f85149] font-semibold">
                        {walkthrough.surfaced_finding_count} finding{walkthrough.surfaced_finding_count === 1 ? '' : 's'}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* Change groups */}
              <div className="space-y-3">
                {walkthrough.steps.map((step) => {
                  const group = walkthrough.groups.find((g) => g.id === step.group_id)
                  if (!group) return null
                  const isExpanded = !!expandedGroups[group.id]

                  const impactColor: Record<ChangeGroupImpact, string> = {
                    foundational: 'text-[#f85149] bg-[#f85149]/10 border-[#f85149]/30',
                    cross_cutting: 'text-[#d29922] bg-[#d29922]/10 border-[#d29922]/30',
                    contained: 'text-[#3fb950] bg-[#3fb950]/10 border-[#3fb950]/30',
                  }
                  const impactLabel: Record<ChangeGroupImpact, string> = {
                    foundational: 'Foundational',
                    cross_cutting: 'Cross-cutting',
                    contained: 'Contained',
                  }

                  return (
                    <div
                      key={group.id}
                      className="border border-[#30363d] rounded-md bg-[#0d1117] overflow-hidden"
                    >
                      {/* Group header — always visible, clickable to expand */}
                      <button
                        type="button"
                        onClick={() => toggleGroupExpand(group.id)}
                        className="w-full text-left px-3.5 py-3 bg-[#161b22] flex items-center gap-3 hover:bg-[#1c2128] transition-colors cursor-pointer focus:ring-2 focus:ring-inset focus:ring-[#58a6ff] outline-none"
                        aria-expanded={isExpanded}
                      >
                        <span className="text-xs font-mono text-[#8b949e] shrink-0 w-5">{step.step_number}.</span>
                        <ChevronDownIcon
                          size={14}
                          className={`shrink-0 text-[#8b949e] transition-transform duration-150 ${isExpanded ? 'rotate-0' : '-rotate-90'}`}
                        />
                        <div className="flex-1 min-w-0 space-y-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-sm font-semibold text-[#f0f6fc]">{step.headline}</span>
                            <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold border ${impactColor[step.impact]}`}>
                              {impactLabel[step.impact]}
                            </span>
                            {group.has_test_coverage ? (
                              <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-[#3fb950]/10 border border-[#3fb950]/30 text-[#3fb950]">
                                Tested
                              </span>
                            ) : (
                              group.kind !== 'test' && (
                                <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-[#d29922]/10 border border-[#d29922]/30 text-[#d29922]">
                                  No test coverage
                                </span>
                              )
                            )}
                            {group.related_findings_count > 0 && (
                              <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-[#f85149]/15 border border-[#f85149]/40 text-[#f85149]">
                                {group.related_findings_count} {group.related_findings_count === 1 ? 'finding' : 'findings'}
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-[#8b949e] leading-relaxed">{step.detail}</p>
                        </div>
                        <div className="shrink-0 flex items-center gap-3 text-xs text-[#8b949e] ml-2">
                          <span>{step.files_count} {step.files_count === 1 ? 'file' : 'files'}</span>
                        </div>
                      </button>

                      {/* Expanded semantic details */}
                      {isExpanded && (
                        <div className="border-t border-[#30363d] space-y-3 p-3.5 bg-[#0d1117]">
                          {/* Purpose & Impact Justification */}
                          <div className="space-y-1.5 bg-[#161b22] p-3 rounded border border-[#21262d]">
                            <div className="text-xs">
                              <span className="text-[#8b949e] font-semibold">Purpose: </span>
                              <span className="text-[#c9d1d9]">{group.purpose || group.description}</span>
                            </div>
                            {group.impact_justification && (
                              <div className="text-xs">
                                <span className="text-[#8b949e] font-semibold">Blast Radius Justification: </span>
                                <span className="text-[#c9d1d9]">{group.impact_justification}</span>
                              </div>
                            )}
                          </div>

                          {/* Structural Relationships & Graph Edges */}
                          {group.relationships && group.relationships.length > 0 && (
                            <div className="space-y-1.5">
                              <span className="text-[11px] font-semibold text-[#8b949e] uppercase tracking-wider block">
                                Structural Relationships ({group.relationships.length})
                              </span>
                              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                                {group.relationships.map((rel, rIdx) => (
                                  <div
                                    key={`${rel.source}-${rel.relationship}-${rel.target}-${rIdx}`}
                                    className="p-2 rounded bg-[#161b22] border border-[#21262d] text-xs flex items-center justify-between gap-2"
                                  >
                                    <div className="font-mono text-[#c9d1d9] truncate text-[11px]">
                                      <span className="text-[#58a6ff]">{rel.source}</span>
                                      <span className="text-[#8b949e] px-1.5 font-bold">--[{rel.relationship}]--&gt;</span>
                                      <span className="text-[#f0f6fc]">{rel.target}</span>
                                    </div>
                                    <span className="text-[9px] text-[#8b949e] uppercase shrink-0 px-1 py-0.5 bg-[#21262d] rounded">
                                      {rel.evidence_source || 'ast'}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Changed Symbols */}
                          {group.symbols_changed && group.symbols_changed.length > 0 && (
                            <div className="space-y-1.5">
                              <span className="text-[11px] font-semibold text-[#8b949e] uppercase tracking-wider block">
                                Affected Symbols ({group.symbols_changed.length})
                              </span>
                              <div className="flex items-center gap-1.5 flex-wrap">
                                {group.symbols_changed.map((sym) => (
                                  <span
                                    key={sym}
                                    className="px-2 py-0.5 rounded bg-[#21262d] text-mono text-[11px] text-[#c9d1d9] border border-[#30363d]"
                                  >
                                    {sym}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Relevant Tests */}
                          {group.tests && group.tests.length > 0 && (
                            <div className="space-y-1.5">
                              <span className="text-[11px] font-semibold text-[#8b949e] uppercase tracking-wider block">
                                Verified Tests ({group.tests.length})
                              </span>
                              <div className="space-y-1">
                                {group.tests.map((t, tIdx) => (
                                  <div
                                    key={`${t.test_file_path}-${t.target_symbol_or_file}-${tIdx}`}
                                    className="px-2.5 py-1.5 rounded bg-[#161b22] border border-[#21262d] text-xs flex items-center justify-between gap-2"
                                  >
                                    <div className="font-mono text-[11px] text-[#c9d1d9]">
                                      <span className="text-[#3fb950]">{t.test_file_path}</span>
                                      <span className="text-[#8b949e] px-1.5">tests</span>
                                      <span className="text-[#f0f6fc]">{t.target_symbol_or_file}</span>
                                    </div>
                                    {t.is_in_pr_diff && (
                                      <span className="text-[9px] text-[#3fb950] bg-[#3fb950]/10 border border-[#3fb950]/30 px-1.5 py-0.5 rounded">
                                        In PR Diff
                                      </span>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Review Findings in this group */}
                          {group.findings && group.findings.length > 0 && (
                            <div className="space-y-1.5">
                              <span className="text-[11px] font-semibold text-[#f85149] uppercase tracking-wider block">
                                Review Findings ({group.findings.length})
                              </span>
                              <div className="space-y-2">
                                {group.findings.map((f, fIdx) => (
                                  <div
                                    key={f.id || `${f.file_path}-${fIdx}`}
                                    className="p-2.5 rounded bg-[#161b22] border border-[#f85149]/30 text-xs space-y-1"
                                  >
                                    <div className="flex items-center justify-between gap-2">
                                      <div className="flex items-center gap-2">
                                        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-[#f85149]/15 text-[#f85149] uppercase">
                                          {f.severity}
                                        </span>
                                        <span className="font-semibold text-[#f0f6fc]">{f.title}</span>
                                      </div>
                                      {f.historical_pr_ref && (
                                        <span className="text-[10px] font-mono text-[#58a6ff] bg-[#58a6ff]/10 px-1.5 py-0.5 rounded border border-[#58a6ff]/30">
                                          {f.historical_pr_ref}
                                        </span>
                                      )}
                                    </div>
                                    <p className="text-[#c9d1d9] leading-relaxed text-[11px]">{f.message}</p>
                                    <span className="text-[10px] font-mono text-[#8b949e] block">
                                      {f.file_path}{f.line_number ? `:${f.line_number}` : ''}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Changed Files */}
                          <div className="space-y-1.5 pt-1">
                            <span className="text-[11px] font-semibold text-[#8b949e] uppercase tracking-wider block">
                              Files ({group.files.length})
                            </span>
                            <div className="border border-[#21262d] rounded divide-y divide-[#21262d] bg-[#161b22] overflow-hidden">
                              {group.files.map((f) => (
                                <div
                                  key={f.path}
                                  className="px-3 py-2 flex items-center gap-2 hover:bg-[#1c2128] transition-colors"
                                >
                                  <FileCodeIcon size={13} className="shrink-0 text-[#8b949e]" />
                                  <span className="flex-1 font-mono text-xs text-[#c9d1d9] min-w-0 truncate">{f.path}</span>
                                  <div className="shrink-0 flex items-center gap-2 font-mono text-[11px]">
                                    <span className="text-[#3fb950]">+{f.additions}</span>
                                    <span className="text-[#f85149]">−{f.deletions}</span>
                                  </div>
                                  {f.note && (
                                    <span className="shrink-0 text-[10px] text-[#8b949e] italic">{f.note}</span>
                                  )}
                                </div>
                              ))}
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </>
          )}

          {!walkthrough && !isLoadingWalkthrough && !walkthroughError && (
            <div className="p-8 text-center text-xs text-[#8b949e] border border-[#30363d] rounded-md bg-[#0d1117]">
              <FileCodeIcon size={24} className="mx-auto mb-2 text-[#30363d]" />
              <p>No walkthrough data available.</p>
            </div>
          )}

                {/* REVIEW FINDINGS: STRUCTURED EXPLAINABILITY HIERARCHY */}
                {review && review.findings.length > 0 && (
                  <div className="space-y-4 pt-2">
                    <div className="text-xs font-semibold text-[#f0f6fc] border-b border-[#21262d] pb-1.5 flex items-center justify-between">
                      <span>Review Findings ({review.findings.length})</span>
                      <span className="text-[11px] text-[#8b949e] font-normal">
                        DECISION &rarr; RATIONALE &rarr; PRECEDENT &rarr; SCOPE &rarr; EVIDENCE &rarr; ACTION
                      </span>
                    </div>

                    {review.findings.map((f, idx) => {
                      const fAny = f as ReviewFinding & { provenance_status?: string }
                      const provStatus = fAny.provenance_status || (f.historical_evidence ? 'unverified' : 'none')
                      const evidencePrs = f.historical_evidence?.evidence_prs?.filter(Boolean) || []
                      if (f.historical_pr_ref && evidencePrs.length === 0) {
                        evidencePrs.push(f.historical_pr_ref)
                      }
                      const isVerified = provStatus === 'verified'
                      const isUnverified = provStatus === 'unverified'
                      const isUnavailable = provStatus === 'unavailable'

                      return (
                        <div
                          key={idx}
                          className="rounded-md border border-[#30363d] bg-[#0d1117] overflow-hidden text-xs"
                        >
                          {/* File Header */}
                          <div className="bg-[#161b22] px-3.5 py-2 border-b border-[#30363d] flex items-center justify-between">
                            <div className="flex items-center gap-2 font-mono text-[11px] text-[#f0f6fc]">
                              <FileCodeIcon size={14} className="text-[#8b949e]" />
                              <Link
                                to={`#diff-${idx}`}
                                onClick={(e) => {
                                  e.preventDefault()
                                  setActiveTab('files')
                                }}
                                className="hover:underline hover:text-[#58a6ff] cursor-pointer focus:ring-1 focus:ring-[#58a6ff] outline-none"
                              >
                                {f.file_path}
                              </Link>
                              {f.line_number && <span className="text-[#8b949e]">:{f.line_number}</span>}
                            </div>

                            <span
                              className={`px-2 py-0.5 rounded-full text-[10px] font-medium border ${
                                f.severity === 'error'
                                  ? 'border-[#f85149]/40 bg-[#f85149]/15 text-[#f85149]'
                                  : f.severity === 'warning'
                                  ? 'border-[#d29922]/40 bg-[#d29922]/15 text-[#d29922]'
                                  : 'border-[#30363d] bg-[#21262d] text-[#8b949e]'
                              }`}
                            >
                              {f.severity}
                            </span>
                          </div>

                          {/* Finding Body with Canonical Hierarchy */}
                          <div className="p-4 space-y-3">
                            {/* 1. DECISION */}
                            <div>
                              <div className="text-[10px] uppercase font-bold tracking-wider text-[#8b949e] mb-0.5">
                                Decision
                              </div>
                              <p className="text-xs font-semibold text-[#f0f6fc] leading-snug">
                                {f.decision || f.title || f.message}
                              </p>
                              {f.message && f.message !== f.decision && (
                                <p className="text-xs text-[#c9d1d9] mt-1 leading-relaxed">
                                  {f.message}
                                </p>
                              )}
                            </div>

                            {/* 2. RATIONALE */}
                            {(f.rationale || f.historical_evidence?.rationale) && (
                              <div className="pt-1">
                                <div className="text-[10px] uppercase font-bold tracking-wider text-[#8b949e] mb-0.5">
                                  Rationale
                                </div>
                                <p className="text-xs text-[#8b949e] leading-relaxed">
                                  {f.rationale || f.historical_evidence?.rationale}
                                </p>
                              </div>
                            )}

                            {/* 3. HISTORICAL CONTEXT / PRECEDENT */}
                            {provStatus !== 'none' && (
                              <div className="rounded-md border border-[#1f6feb]/30 bg-[#0d1117] overflow-hidden text-xs mt-2">
                                <div className="px-3 py-2 bg-[#161b22] border-b border-[#30363d] flex items-center justify-between">
                                  <div className="flex items-center gap-1.5 font-semibold text-[#58a6ff]">
                                    <ShieldCheckIcon size={14} />
                                    <span>Historical Precedent</span>
                                    {isVerified && (
                                      <span className="px-1.5 py-0.5 rounded text-[9px] font-semibold bg-[#238636]/20 border border-[#238636]/40 text-[#3fb950] ml-1">
                                        Verified source
                                      </span>
                                    )}
                                    {isUnverified && (
                                      <span className="px-1.5 py-0.5 rounded text-[9px] font-semibold bg-[#d29922]/15 border border-[#d29922]/40 text-[#d29922] ml-1">
                                        Unverified source
                                      </span>
                                    )}
                                    {isUnavailable && (
                                      <span className="px-1.5 py-0.5 rounded text-[9px] font-semibold bg-[#30363d] border border-[#30363d] text-[#8b949e] ml-1">
                                        No source reference
                                      </span>
                                    )}
                                  </div>
                                  <button
                                    type="button"
                                    onClick={() => setExpandedTraces((prev) => ({ ...prev, [idx]: !prev[idx] }))}
                                    className="text-[11px] text-[#8b949e] hover:text-[#f0f6fc] flex items-center gap-1 cursor-pointer focus:ring-1 focus:ring-[#58a6ff] outline-none"
                                  >
                                    <span>Details</span>
                                    {expandedTraces[idx] ? <ChevronDownIcon size={12} /> : <ChevronRightIcon size={12} />}
                                  </button>
                                </div>

                                <div className="p-3 space-y-2 text-[11px]">
                                  {/* Source references with navigable links where available */}
                                  {evidencePrs.length > 0 && (
                                    <div className="flex items-center gap-2 flex-wrap">
                                      <span className="font-semibold text-[#8b949e]">Precedent source:</span>
                                      {evidencePrs.map((ev, i) => {
                                        const prNumMatch = ev.match(/#?(\d+)/)
                                        const targetPrNum = prNumMatch ? prNumMatch[1] : null
                                        return targetPrNum ? (
                                          <Link
                                            key={i}
                                            to={`/repositories/${encodeURIComponent(repoId || '')}/pulls/${targetPrNum}`}
                                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-[#21262d] border border-[#30363d] text-[#58a6ff] hover:text-[#79c0ff] hover:border-[#58a6ff]/50 font-mono text-[10px] transition-colors focus:ring-1 focus:ring-[#58a6ff] outline-none"
                                            title={`Navigate to historical ${ev}`}
                                          >
                                            <span>{ev}</span>
                                            <LinkExternalIcon size={10} />
                                          </Link>
                                        ) : (
                                          <span key={i} className="px-2 py-0.5 rounded bg-[#21262d] border border-[#30363d] text-[#58a6ff] font-mono text-[10px]">
                                            {ev}
                                          </span>
                                        )
                                      })}
                                    </div>
                                  )}

                                  {isUnavailable && evidencePrs.length === 0 && (
                                    <div className="text-[#8b949e] italic">
                                      A historical engineering rule was matched in organizational memory, but no specific PR or commit reference could be verified.
                                    </div>
                                  )}

                                  {/* 4. SCOPE & APPLICABILITY */}
                                  {(f.scope || f.historical_evidence?.scope) && (
                                    <div className="pt-1 flex items-start gap-1.5 text-[#8b949e]">
                                      <span className="font-semibold text-[#c9d1d9] shrink-0">Scope:</span>
                                      <code className="px-1.5 py-0.2 rounded bg-[#161b22] border border-[#30363d] text-[#7ee787] font-mono text-[10px]">
                                        {f.scope || f.historical_evidence?.scope}
                                      </code>
                                    </div>
                                  )}

                                  {/* Expandable details */}
                                  {expandedTraces[idx] && (
                                    <div className="pt-2 border-t border-[#21262d] space-y-1.5 text-[#8b949e]">
                                      <div className="font-semibold text-[#f0f6fc] text-[10px] uppercase tracking-wider mb-1">
                                        Evidence & Provenance
                                      </div>
                                      <div className="space-y-1 pl-2 border-l-2 border-[#1f6feb]">
                                        <div>
                                          <strong className="text-[#c9d1d9]">Current change:</strong>{' '}
                                          <code className="text-[#7ee787] font-mono">{f.file_path}{f.line_number ? `:${f.line_number}` : ''}</code>
                                        </div>
                                        {evidencePrs.length > 0 && (
                                          <div>
                                            <strong className="text-[#c9d1d9]">Historical precedent:</strong>{' '}
                                            {evidencePrs.join(', ')}
                                          </div>
                                        )}
                                        {f.category === 'approved_exception' && (
                                          <div>
                                            <strong className="text-[#a371f7]">Status:</strong> Approved scoped exception applies to this context.
                                          </div>
                                        )}
                                      </div>
                                    </div>
                                  )}
                                </div>
                              </div>
                            )}

                            {/* 5. ACTION */}
                            <div className="pt-2 border-t border-[#21262d] flex items-start gap-2">
                              <div className="text-[10px] uppercase font-bold tracking-wider text-[#3fb950] shrink-0 mt-0.5">
                                Action:
                              </div>
                              <div className="text-xs text-[#f0f6fc] leading-relaxed flex-1">
                                {f.action || (f.suggested_fix ? 'Apply the recommended architectural correction below.' : 'Review against project requirements.')}
                              </div>
                            </div>

                            {/* GitHub Suggested Change Box */}
                            {f.suggested_fix && (
                              <div className="rounded border border-[#30363d] overflow-hidden mt-2">
                                <div className="bg-[#161b22] px-3 py-1.5 border-b border-[#30363d] flex items-center justify-between text-[11px] text-[#8b949e]">
                                  <span>Suggested fix</span>
                                  <button
                                    type="button"
                                    onClick={() => handleCopyFix(f.suggested_fix!, idx)}
                                    className="flex items-center gap-1 text-[#4493f8] hover:underline cursor-pointer focus:ring-1 focus:ring-[#58a6ff] outline-none"
                                  >
                                    {copiedFixIndex === idx ? (
                                      <>
                                        <CheckIcon size={12} className="text-[#3fb950]" />
                                        <span className="text-[#3fb950]">Copied</span>
                                      </>
                                    ) : (
                                      <>
                                        <CopyIcon size={12} />
                                        <span>Copy suggestion</span>
                                      </>
                                    )}
                                  </button>
                                </div>
                                <pre className="p-3 bg-[#010409] text-xs font-mono text-[#7ee787] overflow-x-auto m-0">
                                  <code>{f.suggested_fix}</code>
                                </pre>
                              </div>
                            )}

                            {/* ================================================================= */}
                            {/* PHASE 8: CONTEXT-AWARE ARCHITECTURAL FIX PROPOSAL                */}
                            {/* ================================================================= */}
                            {(() => {
                              const findingIdStr = f.id || `${f.file_path}:${f.line_number || 0}`
                              const findingFixes = fixes.filter(
                                (fx) =>
                                  fx.finding_id === f.id ||
                                  fx.finding_id === findingIdStr ||
                                  fx.finding_id === f.file_path ||
                                  (Boolean(f.file_path) && Boolean(fx.affected_files?.includes(f.file_path)))
                              )
                              const isGenerating = generatingFindingId === f.id || generatingFindingId === findingIdStr || generatingFindingId === 'all'
                              const errorMsg = (f.id && fixErrors[f.id]) || fixErrors[findingIdStr]

                              return (
                                <div className="mt-3 pt-3 border-t border-[#30363d] space-y-3">
                                  <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-1.5 text-xs font-semibold text-[#f0f6fc]">
                                      <ZapIcon size={13} className="text-[#58a6ff]" />
                                      <span>Context-Aware Architectural Fix</span>
                                      {findingFixes.length > 0 && (
                                        <span className="text-[10px] text-[#8b949e] font-normal">
                                          ({findingFixes.length} available)
                                        </span>
                                      )}
                                    </div>
                                    <button
                                      type="button"
                                      disabled={isGenerating || f.category === 'approved_exception'}
                                      onClick={() => handleGenerateFix(f.id || findingIdStr)}
                                      className="px-2.5 py-1 text-[11px] font-medium rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] text-[#f0f6fc] disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 focus:ring-1 focus:ring-[#58a6ff] outline-none"
                                    >
                                      {isGenerating ? (
                                        <>
                                          <SyncIcon size={11} className="animate-spin text-[#58a6ff]" />
                                          <span>Synthesizing fix...</span>
                                        </>
                                      ) : (
                                        <>
                                          <ZapIcon size={11} className="text-[#58a6ff]" />
                                          <span>{findingFixes.length > 0 ? 'Regenerate' : 'Suggest Fix'}</span>
                                        </>
                                      )}
                                    </button>
                                  </div>

                                  {errorMsg && (
                                    <div className="p-2.5 rounded border border-[#f85149]/40 bg-[#f85149]/10 text-xs text-[#f85149] flex items-start gap-2">
                                      <AlertIcon size={13} className="shrink-0 mt-0.5" />
                                      <span>{errorMsg}</span>
                                    </div>
                                  )}

                                  {findingFixes.map((candidate) => {
                                    const actionState = fixActionInProgress[candidate.id]
                                    const candErr = fixErrors[candidate.id]
                                    const isDiffOpen = expandedFixDiffs[candidate.id] ?? true
                                    const isApplied = candidate.status === 'APPLIED'
                                    const isApproved = candidate.is_approved
                                    const isValidated = candidate.validation_status === 'PASSED'
                                    const isStale = (detail?.pr.head_sha && candidate.base_sha !== detail.pr.head_sha)

                                    return (
                                      <div
                                        key={candidate.id}
                                        className="rounded-md border border-[#30363d] bg-[#0d1117] p-3 space-y-3"
                                      >
                                        <div className="flex items-start justify-between gap-2">
                                          <div>
                                            <div className="text-xs font-semibold text-[#f0f6fc] flex items-center gap-2">
                                              <span>{candidate.title}</span>
                                              <span
                                                className={`px-1.5 py-0.2 rounded text-[10px] font-semibold border ${
                                                  isApplied
                                                    ? 'border-[#238636] bg-[#238636]/15 text-[#3fb950]'
                                                    : isApproved
                                                    ? 'border-[#1f6feb] bg-[#1f6feb]/15 text-[#58a6ff]'
                                                    : isValidated
                                                    ? 'border-[#8957e5] bg-[#8957e5]/15 text-[#a371f7]'
                                                    : 'border-[#30363d] bg-[#21262d] text-[#8b949e]'
                                                }`}
                                              >
                                                {candidate.status}
                                              </span>
                                            </div>
                                            <p className="text-xs text-[#8b949e] mt-1 leading-relaxed">
                                              {candidate.proposed_change}
                                            </p>
                                          </div>
                                        </div>

                                        {/* Constraints & Precedents */}
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px] bg-[#161b22] p-2.5 rounded border border-[#21262d]">
                                          <div>
                                            <span className="font-semibold text-[#c9d1d9]">Architectural constraint:</span>
                                            <div className="text-[#8b949e] font-mono mt-0.5">
                                              {candidate.applicable_constraint || 'Repository structural convention'}
                                            </div>
                                          </div>
                                          <div>
                                            <span className="font-semibold text-[#c9d1d9]">Historical precedent:</span>
                                            <div className="text-[#8b949e] font-mono mt-0.5">
                                              {candidate.precedent_source || 'Verified code graph relationships'}
                                            </div>
                                          </div>
                                        </div>

                                        {/* Affected Files & Symbols */}
                                        <div className="flex flex-wrap items-center gap-2 text-[11px] text-[#8b949e]">
                                          <span className="font-semibold text-[#c9d1d9]">Files:</span>
                                          {candidate.affected_files.map((file, fIdx) => (
                                            <code
                                              key={fIdx}
                                              className="px-1.5 py-0.5 rounded bg-[#161b22] border border-[#30363d] text-[#7ee787] font-mono text-[10px]"
                                            >
                                              {file}
                                            </code>
                                          ))}
                                          {candidate.affected_symbols.length > 0 && (
                                            <>
                                              <span className="font-semibold text-[#c9d1d9] ml-2">Symbols:</span>
                                              {candidate.affected_symbols.map((sym, sIdx) => (
                                                <code
                                                  key={sIdx}
                                                  className="px-1.5 py-0.5 rounded bg-[#161b22] border border-[#30363d] text-[#79c0ff] font-mono text-[10px]"
                                                >
                                                  {sym}
                                                </code>
                                              ))}
                                            </>
                                          )}
                                        </div>

                                        {/* Validation Checklist */}
                                        <div className="space-y-1 bg-[#161b22] p-2.5 rounded border border-[#21262d]">
                                          <div className="text-[10px] uppercase font-bold tracking-wider text-[#8b949e] mb-1">
                                            Evidence & Constraint Validation
                                          </div>
                                          {candidate.validation_checks.map((chk) => (
                                            <div
                                              key={chk.check_id}
                                              className="flex items-center gap-1.5 text-[11px]"
                                            >
                                              {chk.status === 'PASSED' ? (
                                                <CheckCircleIcon size={12} className="text-[#3fb950] shrink-0" />
                                              ) : chk.status === 'FAILED' ? (
                                                <XCircleIcon size={12} className="text-[#f85149] shrink-0" />
                                              ) : (
                                                <AlertIcon size={12} className="text-[#d29922] shrink-0" />
                                              )}
                                              <span className={chk.status === 'PASSED' ? 'text-[#c9d1d9]' : 'text-[#f85149]'}>
                                                {chk.name}: {chk.message}
                                              </span>
                                            </div>
                                          ))}
                                        </div>

                                        {/* Stale Warning */}
                                        {isStale && (
                                          <div className="p-2 rounded border border-[#d29922]/40 bg-[#d29922]/10 text-xs text-[#d29922] flex items-center gap-2">
                                            <AlertIcon size={13} className="shrink-0" />
                                            <span>
                                              Fix is outdated because the repository changed after this suggestion was generated.
                                            </span>
                                          </div>
                                        )}

                                        {candErr && (
                                          <div className="p-2 rounded border border-[#f85149]/40 bg-[#f85149]/10 text-xs text-[#f85149]">
                                            {candErr}
                                          </div>
                                        )}

                                        {/* Post-Apply Resolution Box */}
                                        {candidate.post_apply_validation && (
                                          <div className="p-2.5 rounded border border-[#238636]/40 bg-[#238636]/10 text-xs space-y-1">
                                            <div className="font-semibold text-[#3fb950] flex items-center gap-1.5">
                                              <CheckCircleIcon size={13} />
                                              <span>Post-Apply Verification</span>
                                            </div>
                                            <div className="grid grid-cols-3 gap-2 text-[11px] text-[#c9d1d9] pt-1">
                                              <div>
                                                Original finding:{' '}
                                                <strong className="text-[#3fb950]">
                                                  {candidate.post_apply_validation.original_finding_status}
                                                </strong>
                                              </div>
                                              <div>
                                                Validation:{' '}
                                                <strong className="text-[#3fb950]">
                                                  {candidate.post_apply_validation.validation_status}
                                                </strong>
                                              </div>
                                              <div>
                                                New findings:{' '}
                                                <strong className="text-[#3fb950]">
                                                  {candidate.post_apply_validation.new_findings_count}
                                                </strong>
                                              </div>
                                            </div>
                                          </div>
                                        )}

                                        {/* Unified Diff Box */}
                                        {candidate.patch_diff && (
                                          <div className="rounded border border-[#30363d] overflow-hidden">
                                            <button
                                              type="button"
                                              onClick={() => toggleFixDiff(candidate.id)}
                                              className="w-full bg-[#161b22] px-3 py-1.5 border-b border-[#30363d] flex items-center justify-between text-[11px] text-[#8b949e] hover:bg-[#21262d] text-left cursor-pointer outline-none"
                                            >
                                              <span className="flex items-center gap-1.5">
                                                <FileDiffIcon size={12} />
                                                <span>Patch Diff</span>
                                              </span>
                                              <span>{isDiffOpen ? 'Collapse diff' : 'Expand diff'}</span>
                                            </button>
                                            {isDiffOpen && (
                                              <pre className="p-3 bg-[#010409] text-xs font-mono overflow-x-auto m-0 leading-relaxed">
                                                {candidate.patch_diff.split('\n').map((line, lIdx) => {
                                                  let lineClass = 'text-[#8b949e]'
                                                  if (line.startsWith('+') && !line.startsWith('+++')) {
                                                    lineClass = 'text-[#7ee787] bg-[#238636]/10'
                                                  } else if (line.startsWith('-') && !line.startsWith('---')) {
                                                    lineClass = 'text-[#f85149] bg-[#f85149]/10'
                                                  } else if (line.startsWith('@@')) {
                                                    lineClass = 'text-[#79c0ff]'
                                                  }
                                                  return (
                                                    <div key={lIdx} className={lineClass}>
                                                      {line}
                                                    </div>
                                                  )
                                                })}
                                              </pre>
                                            )}
                                          </div>
                                        )}

                                        {/* Action Buttons: Explicit Human Approval Flow */}
                                        <div className="flex items-center justify-end gap-2 pt-1">
                                          {!isApplied && candidate.status !== 'REJECTED' && (
                                            <>
                                              {/* Validate button if in DRAFT or FAILED */}
                                              {(candidate.status === 'DRAFT' || candidate.status === 'FAILED') && (
                                                <button
                                                  type="button"
                                                  disabled={!!actionState || Boolean(isStale)}
                                                  onClick={() => handleValidateFix(candidate.id)}
                                                  className="px-2.5 py-1 text-xs font-medium rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] text-[#f0f6fc] disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 focus:ring-1 focus:ring-[#58a6ff] outline-none"
                                                >
                                                  {actionState === 'validating' ? (
                                                    <SyncIcon size={12} className="animate-spin text-[#58a6ff]" />
                                                  ) : (
                                                    <CheckIcon size={12} />
                                                  )}
                                                  <span>Validate</span>
                                                </button>
                                              )}

                                              {/* Approve button if VALIDATED or READY_FOR_APPROVAL */}
                                              {(candidate.status === 'VALIDATED' || candidate.status === 'READY_FOR_APPROVAL') && !isApproved && (
                                                <button
                                                  type="button"
                                                  disabled={!!actionState || Boolean(isStale)}
                                                  onClick={() => handleApproveFix(candidate.id)}
                                                  className="px-2.5 py-1 text-xs font-medium rounded border border-[#1f6feb] bg-[#1f6feb] hover:bg-[#388bfd] text-[#ffffff] disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 focus:ring-1 focus:ring-[#58a6ff] outline-none"
                                                >
                                                  {actionState === 'approving' ? (
                                                    <SyncIcon size={12} className="animate-spin" />
                                                  ) : (
                                                    <ShieldCheckIcon size={12} />
                                                  )}
                                                  <span>Approve</span>
                                                </button>
                                              )}

                                              {/* Apply button if APPROVED */}
                                              {isApproved && (
                                                <button
                                                  type="button"
                                                  disabled={!!actionState || Boolean(isStale)}
                                                  onClick={() => handleApplyFix(candidate.id)}
                                                  className="px-3 py-1 text-xs font-semibold rounded border border-[#238636] bg-[#238636] hover:bg-[#2ea043] text-[#ffffff] disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 focus:ring-1 focus:ring-[#3fb950] outline-none"
                                                >
                                                  {actionState === 'applying' ? (
                                                    <SyncIcon size={12} className="animate-spin" />
                                                  ) : (
                                                    <PlayIcon size={12} />
                                                  )}
                                                  <span>Apply Patch</span>
                                                </button>
                                              )}

                                              {/* Reject button */}
                                              <button
                                                type="button"
                                                disabled={!!actionState}
                                                onClick={() => handleRejectFix(candidate.id)}
                                                className="px-2.5 py-1 text-xs font-medium rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] text-[#8b949e] hover:text-[#f85149] disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1 focus:ring-1 focus:ring-[#f85149] outline-none"
                                              >
                                                <StopIcon size={12} />
                                                <span>Reject</span>
                                              </button>
                                            </>
                                          )}

                                          {isApplied && (
                                            <div className="flex items-center gap-1.5 text-xs text-[#3fb950] font-semibold">
                                              <CheckCircleIcon size={13} />
                                              <span>Patch Applied & Verified</span>
                                            </div>
                                          )}
                                        </div>
                                      </div>
                                    )
                                  })}
                                </div>
                              )
                            })()}

                            {/* ================================================================= */}
                            {/* PHASE 9: DEVELOPER FEEDBACK & CONTROLLED LEARNING CONTROLS        */}
                            {/* ================================================================= */}
                            <div className="mt-3 pt-3 border-t border-[#30363d] space-y-2">
                              <div className="flex items-center justify-between text-xs">
                                <div className="flex items-center gap-1.5 font-semibold text-[#f0f6fc]">
                                  <ShieldCheckIcon size={13} className="text-[#a371f7]" />
                                  <span>Developer Feedback & Institutional Learning</span>
                                </div>
                                <span className="text-[10px] text-[#8b949e]">
                                  Signals learning candidate • Confirmation required
                                </span>
                              </div>

                              {/* Feedback action buttons */}
                              <div className="flex flex-wrap items-center gap-1.5">
                                <button
                                  type="button"
                                  disabled={!!submittingFeedbackForFindingId}
                                  onClick={() => handleSubmitFeedback(f, 'CORRECT')}
                                  className="px-2.5 py-1 text-[11px] font-medium rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] text-[#7ee787] hover:border-[#238636] disabled:opacity-50 transition-colors cursor-pointer outline-none focus:ring-1 focus:ring-[#238636]"
                                  title="Confirm finding is correct to reinforce review observation"
                                >
                                  {submittingFeedbackForFindingId === `${f.id || `${f.file_path}:${f.line_number || 0}`}:CORRECT` ? (
                                    <SyncIcon size={11} className="animate-spin inline mr-1" />
                                  ) : (
                                    <CheckIcon size={11} className="inline mr-1" />
                                  )}
                                  Correct
                                </button>

                                <button
                                  type="button"
                                  disabled={!!submittingFeedbackForFindingId}
                                  onClick={() => handleSubmitFeedback(f, 'NOT_A_VIOLATION')}
                                  className="px-2.5 py-1 text-[11px] font-medium rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] text-[#79c0ff] hover:border-[#1f6feb] disabled:opacity-50 transition-colors cursor-pointer outline-none focus:ring-1 focus:ring-[#1f6feb]"
                                  title="Indicate this is not a violation; creates learning candidate"
                                >
                                  {submittingFeedbackForFindingId === `${f.id || `${f.file_path}:${f.line_number || 0}`}:NOT_A_VIOLATION` ? (
                                    <SyncIcon size={11} className="animate-spin inline mr-1" />
                                  ) : null}
                                  Not a Violation
                                </button>

                                <button
                                  type="button"
                                  disabled={!!submittingFeedbackForFindingId}
                                  onClick={() => handleSubmitFeedback(f, 'APPROVED_EXCEPTION')}
                                  className="px-2.5 py-1 text-[11px] font-medium rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] text-[#d2a8ff] hover:border-[#8957e5] disabled:opacity-50 transition-colors cursor-pointer outline-none focus:ring-1 focus:ring-[#8957e5]"
                                  title="Intentionally allow this change as an approved, narrow exception"
                                >
                                  {submittingFeedbackForFindingId === `${f.id || `${f.file_path}:${f.line_number || 0}`}:APPROVED_EXCEPTION` ? (
                                    <SyncIcon size={11} className="animate-spin inline mr-1" />
                                  ) : null}
                                  Approved Exception
                                </button>

                                <button
                                  type="button"
                                  disabled={!!submittingFeedbackForFindingId}
                                  onClick={() => handleSubmitFeedback(f, 'ARCHITECTURE_CHANGED')}
                                  className="px-2.5 py-1 text-[11px] font-medium rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] text-[#ffa657] hover:border-[#bd561d] disabled:opacity-50 transition-colors cursor-pointer outline-none focus:ring-1 focus:ring-[#bd561d]"
                                  title="Intentional repository architecture evolution; creates supersession candidate"
                                >
                                  {submittingFeedbackForFindingId === `${f.id || `${f.file_path}:${f.line_number || 0}`}:ARCHITECTURE_CHANGED` ? (
                                    <SyncIcon size={11} className="animate-spin inline mr-1" />
                                  ) : null}
                                  Architecture Changed
                                </button>

                                <button
                                  type="button"
                                  disabled={!!submittingFeedbackForFindingId}
                                  onClick={() => handleSubmitFeedback(f, 'REMEMBER_THIS_DECISION')}
                                  className="px-2.5 py-1 text-[11px] font-medium rounded border border-[#388bfd]/40 bg-[#388bfd]/10 hover:bg-[#388bfd]/20 text-[#58a6ff] disabled:opacity-50 transition-colors cursor-pointer outline-none focus:ring-1 focus:ring-[#58a6ff]"
                                  title="Explicitly remember this architectural decision through controlled validation"
                                >
                                  {submittingFeedbackForFindingId === `${f.id || `${f.file_path}:${f.line_number || 0}`}:REMEMBER_THIS_DECISION` ? (
                                    <SyncIcon size={11} className="animate-spin inline mr-1" />
                                  ) : null}
                                  Remember This Decision
                                </button>
                              </div>
                            </div>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}

                {/* HISTORICAL CONTEXT & INSTITUTIONAL MEMORY SNAPSHOT PANEL */}
                {review?.historical_context_snapshot && review.historical_context_snapshot.some((s) => (s.claim || s.statement || (s as any).memory_text)?.trim()) && (
                  <div className="pt-4 border-t border-[#30363d] space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 text-xs font-semibold text-[#f0f6fc]">
                        <BookIcon size={14} className="text-[#8b949e]" />
                        <span>
                          Institutional Precedents Evaluated ({review.historical_context_snapshot.filter((s) => (s.claim || s.statement || (s as any).memory_text)?.trim()).length})
                        </span>
                      </div>
                      <Link
                        to={`/repositories/${encodeURIComponent(repoId || '')}/memory`}
                        className="text-[11px] text-[#58a6ff] hover:underline inline-flex items-center gap-1 focus:ring-1 focus:ring-[#58a6ff] outline-none"
                      >
                        <span>View in Repository Memory</span>
                        <LinkExternalIcon size={10} />
                      </Link>
                    </div>

                    <div className="space-y-2">
                      {review.historical_context_snapshot.map((snap, sIdx) => {
                        const snapAny = snap as any
                        const claimText = snap.claim || snap.statement || snapAny.memory_text
                        if (!claimText || !claimText.trim()) return null

                        const isAuth = (snap.memory_category || '').toLowerCase().includes('decision') || (snap.memory_category || '').toLowerCase().includes('rejected')
                        const isExc = (snap.memory_category || '').toLowerCase().includes('exception')
                        const rationaleText = snap.rationale || (snapAny.memory_text && snapAny.memory_text !== claimText ? snapAny.memory_text : null)

                        return (
                          <div
                            key={sIdx}
                            className="p-3 rounded-md border border-[#30363d] bg-[#161b22] text-xs space-y-1.5"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <div className="flex items-center gap-2">
                                <span
                                  className={`px-1.5 py-0.2 rounded text-[10px] font-semibold border ${
                                    isExc
                                      ? 'border-[#8957e5]/40 bg-[#8957e5]/15 text-[#a371f7]'
                                      : isAuth
                                      ? 'border-[#1f6feb]/40 bg-[#1f6feb]/15 text-[#58a6ff]'
                                      : 'border-[#30363d] bg-[#21262d] text-[#8b949e]'
                                  }`}
                                >
                                  {isExc ? 'Approved Exception' : isAuth ? 'Authoritative Rule' : 'Contextual Reference'}
                                </span>
                                <span className="font-semibold text-[#f0f6fc]">
                                  {claimText}
                                </span>
                              </div>

                              {snap.source_pr_ref && (
                                <span className="font-mono text-[10px] text-[#58a6ff] px-1.5 py-0.5 rounded bg-[#21262d] border border-[#30363d]">
                                  {snap.source_pr_ref}
                                </span>
                              )}
                            </div>

                            {rationaleText && (
                              <p className="text-[11px] text-[#8b949e] leading-relaxed">
                                {rationaleText}
                              </p>
                            )}

                            {snap.scope && (
                              <div className="text-[10px] text-[#8b949e] font-mono">
                                Scope: {snap.scope}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* ================================================================= */}
                {/* PHASE 9: LEARNING CANDIDATES AUDIT & CONFIRMATION PANEL           */}
                {/* ================================================================= */}
                {learningCandidates.length > 0 && (
                  <div className="pt-4 border-t border-[#30363d] space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 text-xs font-semibold text-[#f0f6fc]">
                        <ShieldCheckIcon size={14} className="text-[#a371f7]" />
                        <span>Controlled Learning Pipeline Candidates ({learningCandidates.length})</span>
                      </div>
                      <span className="text-[11px] text-[#8b949e]">
                        Requires explicit human confirmation before canonical enforcement
                      </span>
                    </div>

                    <div className="space-y-2">
                      {learningCandidates.map((cand) => {
                        const isPending = cand.status === 'REQUIRES_CONFIRMATION' || cand.status === 'DRAFT' || cand.status === 'PENDING_VALIDATION'
                        const isPersisted = cand.status === 'PERSISTED' || cand.status === 'CONFIRMED'
                        const isConflict = cand.status === 'CONFLICTING'
                        const isRejected = cand.status === 'REJECTED'

                        return (
                          <div
                            key={cand.candidate_id}
                            className="p-3 rounded-md border border-[#30363d] bg-[#161b22] text-xs space-y-2"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <div className="flex items-center gap-2">
                                <span
                                  className={`px-1.5 py-0.2 rounded text-[10px] font-semibold border ${
                                    isPersisted
                                      ? 'border-[#238636]/40 bg-[#238636]/15 text-[#3fb950]'
                                      : isConflict
                                      ? 'border-[#d29922]/40 bg-[#d29922]/15 text-[#d29922]'
                                      : isRejected
                                      ? 'border-[#f85149]/40 bg-[#f85149]/15 text-[#f85149]'
                                      : 'border-[#1f6feb]/40 bg-[#1f6feb]/15 text-[#58a6ff]'
                                  }`}
                                >
                                  {cand.status}
                                </span>
                                <span className="font-semibold text-[#f0f6fc]">
                                  {cand.statement}
                                </span>
                              </div>

                              <div className="flex items-center gap-2">
                                {isPending && (
                                  <button
                                    type="button"
                                    onClick={() => handleOpenConfirmModal(cand)}
                                    className="px-2 py-0.5 rounded text-[11px] font-semibold bg-[#238636] hover:bg-[#2ea043] text-white flex items-center gap-1 cursor-pointer"
                                  >
                                    <CheckIcon size={11} />
                                    <span>Review & Confirm</span>
                                  </button>
                                )}
                                {!isRejected && !isPersisted && (
                                  <button
                                    type="button"
                                    onClick={() => handleRejectCandidate(cand.candidate_id)}
                                    className="px-2 py-0.5 rounded text-[11px] font-medium border border-[#30363d] bg-[#21262d] text-[#8b949e] hover:text-[#f85149] cursor-pointer"
                                  >
                                    Dismiss
                                  </button>
                                )}
                              </div>
                            </div>

                            <p className="text-[11px] text-[#8b949e] leading-relaxed">
                              {cand.rationale}
                            </p>

                            <div className="flex flex-wrap items-center gap-3 text-[10px] text-[#8b949e] pt-1 border-t border-[#21262d]">
                              <span>Type: <strong className="text-[#c9d1d9]">{cand.proposed_memory_type}</strong></span>
                              <span>Authority: <strong className="text-[#c9d1d9]">{cand.proposed_authority}</strong></span>
                              <span>Scope: <code className="text-[#7ee787] font-mono">{cand.scope}</code></span>
                              {cand.evidence_refs?.length > 0 && (
                                <span>Evidence: {cand.evidence_refs.join(', ')}</span>
                              )}
                            </div>

                            {cand.validation_reasons?.length > 0 && (
                              <div className="text-[10px] text-[#8b949e] bg-[#0d1117] p-2 rounded border border-[#21262d] font-mono">
                                {cand.validation_reasons.map((vr, vrIdx) => (
                                  <div key={vrIdx}>{vr}</div>
                                ))}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* ================================================================= */}
                {/* PHASE 10: DEEP PR EVIDENCE & HISTORY PANEL                       */}
                {/* ================================================================= */}
                <div className="pt-4 border-t border-[#30363d] space-y-3">
                  <div
                    onClick={() => setIsEvidencePanelOpen(!isEvidencePanelOpen)}
                    className="flex items-center justify-between cursor-pointer select-none group"
                  >
                    <div className="flex items-center gap-2 text-xs font-semibold text-[#f0f6fc]">
                      <span className="text-[#8b949e] group-hover:text-[#f0f6fc] transition-colors">
                        {isEvidencePanelOpen ? <ChevronDownIcon size={14} /> : <ChevronRightIcon size={14} />}
                      </span>
                      <ShieldCheckIcon size={14} className="text-[#58a6ff]" />
                      <span>PR Evidence & History</span>
                      {evidence && (
                        <div className="flex items-center gap-1.5 ml-2 text-[10px]">
                          <span className="px-1.5 py-0.2 rounded-full bg-[#21262d] border border-[#30363d] text-[#c9d1d9] font-mono">
                            {evidence.commits?.length || 0} commits
                          </span>
                          <span className="px-1.5 py-0.2 rounded-full bg-[#21262d] border border-[#30363d] text-[#c9d1d9] font-mono">
                            {evidence.conversation?.length || 0} comments
                          </span>
                          <span className="px-1.5 py-0.2 rounded-full bg-[#21262d] border border-[#30363d] text-[#c9d1d9] font-mono">
                            {evidence.checks?.length || 0} checks
                          </span>
                          {evidence.historical_prs?.length > 0 && (
                            <span className="px-1.5 py-0.2 rounded-full bg-[#21262d] border border-[#30363d] text-[#a371f7] font-mono">
                              {evidence.historical_prs.length} related PRs
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                    <span className="text-[11px] text-[#8b949e]">
                      Read-only GitHub evidence • Non-authoritative
                    </span>
                  </div>

                  {isEvidencePanelOpen && (
                    <div className="space-y-3 pt-1">
                      {/* Sub-navigation tabs for Evidence */}
                      <div className="flex items-center gap-2 border-b border-[#21262d] pb-2 text-[11px]">
                        <button
                          type="button"
                          onClick={() => setEvidenceExpandedSection('commits')}
                          className={`px-2.5 py-1 rounded-md font-medium transition-colors cursor-pointer flex items-center gap-1.5 ${
                            evidenceExpandedSection === 'commits'
                              ? 'bg-[#21262d] text-[#f0f6fc] font-semibold border border-[#30363d]'
                              : 'text-[#8b949e] hover:text-[#f0f6fc]'
                          }`}
                        >
                          <GitCommitIcon size={13} />
                          <span>Commits ({evidence?.commits?.length || 0})</span>
                        </button>

                        <button
                          type="button"
                          onClick={() => setEvidenceExpandedSection('conversation')}
                          className={`px-2.5 py-1 rounded-md font-medium transition-colors cursor-pointer flex items-center gap-1.5 ${
                            evidenceExpandedSection === 'conversation'
                              ? 'bg-[#21262d] text-[#f0f6fc] font-semibold border border-[#30363d]'
                              : 'text-[#8b949e] hover:text-[#f0f6fc]'
                          }`}
                        >
                          <CommentDiscussionIcon size={13} />
                          <span>Discussion ({evidence?.conversation?.length || 0})</span>
                        </button>

                        <button
                          type="button"
                          onClick={() => setEvidenceExpandedSection('checks')}
                          className={`px-2.5 py-1 rounded-md font-medium transition-colors cursor-pointer flex items-center gap-1.5 ${
                            evidenceExpandedSection === 'checks'
                              ? 'bg-[#21262d] text-[#f0f6fc] font-semibold border border-[#30363d]'
                              : 'text-[#8b949e] hover:text-[#f0f6fc]'
                          }`}
                        >
                          <CheckCircleIcon size={13} />
                          <span>Checks ({evidence?.checks?.length || 0})</span>
                        </button>

                        <button
                          type="button"
                          onClick={() => setEvidenceExpandedSection('history')}
                          className={`px-2.5 py-1 rounded-md font-medium transition-colors cursor-pointer flex items-center gap-1.5 ${
                            evidenceExpandedSection === 'history'
                              ? 'bg-[#21262d] text-[#f0f6fc] font-semibold border border-[#30363d]'
                              : 'text-[#8b949e] hover:text-[#f0f6fc]'
                          }`}
                        >
                          <HistoryIcon size={13} />
                          <span>Related PRs ({evidence?.historical_prs?.length || 0})</span>
                        </button>
                      </div>

                      {/* Commits Sub-view */}
                      {evidenceExpandedSection === 'commits' && (
                        <div className="space-y-2">
                          {!evidence?.commits || evidence.commits.length === 0 ? (
                            <div className="p-4 text-center text-xs text-[#8b949e] bg-[#161b22] rounded border border-[#30363d]">
                              No commit evolution recorded for this PR.
                            </div>
                          ) : (
                            <div className="space-y-1.5 max-h-80 overflow-y-auto pr-1">
                              {evidence.commits.map((c) => (
                                <div
                                  key={c.sha}
                                  className="p-2.5 rounded border border-[#30363d] bg-[#161b22] text-xs flex items-center justify-between gap-3"
                                >
                                  <div className="flex items-center gap-2 min-w-0">
                                    <code className="px-1.5 py-0.5 rounded bg-[#0d1117] text-[#58a6ff] font-mono text-[11px] border border-[#30363d] shrink-0">
                                      {c.short_sha}
                                    </code>
                                    <span className="text-[#f0f6fc] truncate font-medium">
                                      {c.message}
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-3 shrink-0 text-[11px] text-[#8b949e]">
                                    {c.author && <span>@{c.author}</span>}
                                    {(c.additions !== null && c.deletions !== null) && (
                                      <span className="font-mono">
                                        <span className="text-[#3fb950]">+{c.additions}</span>{' '}
                                        <span className="text-[#f85149]">-{c.deletions}</span>
                                      </span>
                                    )}
                                    {c.committed_at && (
                                      <span>{new Date(c.committed_at).toLocaleDateString()}</span>
                                    )}
                                  </div>
                                </div>
                              ))}
                              {evidence.summary?.commits_truncated && (
                                <div className="text-[11px] text-[#d29922] italic text-center py-1">
                                  Bounded to latest {evidence.commits.length} commits.
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Discussion Sub-view */}
                      {evidenceExpandedSection === 'conversation' && (
                        <div className="space-y-2">
                          {!evidence?.conversation || evidence.conversation.length === 0 ? (
                            <div className="p-4 text-center text-xs text-[#8b949e] bg-[#161b22] rounded border border-[#30363d]">
                              No review comments or conversation items found.
                            </div>
                          ) : (
                            <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
                              {evidence.conversation.map((item) => (
                                <div
                                  key={item.id}
                                  className="p-3 rounded border border-[#30363d] bg-[#161b22] text-xs space-y-1.5"
                                >
                                  <div className="flex items-center justify-between text-[11px] text-[#8b949e]">
                                    <div className="flex items-center gap-2">
                                      <strong className="text-[#f0f6fc]">@{item.author}</strong>
                                      <span className="px-1.5 py-0.2 rounded bg-[#0d1117] border border-[#30363d] text-[10px] text-[#8b949e]">
                                        {item.source_type}
                                      </span>
                                      {item.file_path && (
                                        <span className="font-mono text-[#c9d1d9] text-[10px] truncate max-w-xs">
                                          {item.file_path}{item.line ? `:${item.line}` : ''}
                                        </span>
                                      )}
                                    </div>
                                    <span>{new Date(item.created_at).toLocaleDateString()}</span>
                                  </div>
                                  <div className="text-xs text-[#c9d1d9] whitespace-pre-wrap leading-relaxed">
                                    {item.body}
                                  </div>
                                </div>
                              ))}
                              {evidence.summary?.conversation_truncated && (
                                <div className="text-[11px] text-[#d29922] italic text-center py-1">
                                  Bounded to {evidence.conversation.length} conversation items.
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Checks Sub-view */}
                      {evidenceExpandedSection === 'checks' && (
                        <div className="space-y-2">
                          {!evidence?.checks || evidence.checks.length === 0 ? (
                            <div className="p-4 text-center text-xs text-[#8b949e] bg-[#161b22] rounded border border-[#30363d]">
                              No CI checks or status runs reported for this commit SHA.
                            </div>
                          ) : (
                            <div className="space-y-1.5 max-h-80 overflow-y-auto pr-1">
                              {evidence.checks.map((chk) => {
                                const isSuccess = chk.conclusion === 'success' || chk.conclusion === 'neutral'
                                const isFailure = chk.conclusion === 'failure' || chk.conclusion === 'timed_out' || chk.conclusion === 'action_required'
                                const isPending = chk.status === 'in_progress' || chk.status === 'queued' || chk.status === 'pending' || !chk.conclusion

                                return (
                                  <div
                                    key={chk.id}
                                    className="p-2.5 rounded border border-[#30363d] bg-[#161b22] text-xs flex items-center justify-between gap-3"
                                  >
                                    <div className="flex items-center gap-2 min-w-0">
                                      <span className="shrink-0">
                                        {isSuccess && <CheckCircleIcon size={14} className="text-[#3fb950]" />}
                                        {isFailure && <XCircleIcon size={14} className="text-[#f85149]" />}
                                        {isPending && <ClockIcon size={14} className="text-[#d29922]" />}
                                        {!isSuccess && !isFailure && !isPending && <InfoIcon size={14} className="text-[#8b949e]" />}
                                      </span>
                                      <span className="font-medium text-[#f0f6fc] truncate">
                                        {chk.name}
                                      </span>
                                      {chk.workflow_name && (
                                        <span className="text-[10px] text-[#8b949e]">
                                          ({chk.workflow_name})
                                        </span>
                                      )}
                                    </div>

                                    <div className="flex items-center gap-2 shrink-0">
                                      <span
                                        className={`px-1.5 py-0.2 rounded text-[10px] font-semibold border ${
                                          isSuccess
                                            ? 'bg-[#238636]/15 border-[#238636]/40 text-[#3fb950]'
                                            : isFailure
                                            ? 'bg-[#f85149]/15 border-[#f85149]/40 text-[#f85149]'
                                            : isPending
                                            ? 'bg-[#d29922]/15 border-[#d29922]/40 text-[#d29922]'
                                            : 'bg-[#21262d] border-[#30363d] text-[#8b949e]'
                                        }`}
                                      >
                                        {chk.conclusion || chk.status}
                                      </span>
                                      {chk.url && (
                                        <a
                                          href={chk.url}
                                          target="_blank"
                                          rel="noreferrer"
                                          className="text-[#58a6ff] hover:underline"
                                        >
                                          <LinkExternalIcon size={11} />
                                        </a>
                                      )}
                                    </div>
                                  </div>
                                )
                              })}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Historical PRs Sub-view */}
                      {evidenceExpandedSection === 'history' && (
                        <div className="space-y-2">
                          {!evidence?.historical_prs || evidence.historical_prs.length === 0 ? (
                            <div className="p-4 text-center text-xs text-[#8b949e] bg-[#161b22] rounded border border-[#30363d]">
                              No overlapping historical PRs discovered in repository scope.
                            </div>
                          ) : (
                            <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
                              {evidence.historical_prs.map((hpr) => (
                                <div
                                  key={hpr.pr_number}
                                  className="p-3 rounded border border-[#30363d] bg-[#161b22] text-xs space-y-1.5"
                                >
                                  <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                      <span className="font-semibold text-[#f0f6fc]">
                                        #{hpr.pr_number} {hpr.title}
                                      </span>
                                      <span
                                        className={`px-1.5 py-0.2 rounded text-[10px] font-semibold border ${
                                          hpr.merged
                                            ? 'bg-[#8957e5]/15 border-[#8957e5]/40 text-[#a371f7]'
                                            : hpr.state === 'open'
                                            ? 'bg-[#238636]/15 border-[#238636]/40 text-[#3fb950]'
                                            : 'bg-[#21262d] border-[#30363d] text-[#8b949e]'
                                        }`}
                                      >
                                        {hpr.merged ? 'merged' : hpr.state}
                                      </span>
                                    </div>
                                    {hpr.source_url && (
                                      <a
                                        href={hpr.source_url}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="text-[#58a6ff] hover:underline text-[11px] inline-flex items-center gap-1"
                                      >
                                        <span>View</span>
                                        <LinkExternalIcon size={10} />
                                      </a>
                                    )}
                                  </div>

                                  <div className="text-[11px] text-[#c9d1d9]">
                                    <strong className="text-[#8b949e]">Relevance: </strong>
                                    <span>{hpr.relevance_reason}</span>
                                  </div>

                                  {hpr.relevant_files?.length > 0 && (
                                    <div className="text-[10px] font-mono text-[#8b949e] flex items-center gap-1 truncate">
                                      <span>Overlapping files:</span>
                                      <span className="text-[#7ee787]">{hpr.relevant_files.join(', ')}</span>
                                    </div>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
        </div>
      )}


      {showHistoryModal && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Review Generations History"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
        >
          <div className="bg-[#0d1117] border border-[#30363d] rounded-lg max-w-lg w-full p-4 space-y-3 shadow-xl">
            <div className="flex items-center justify-between pb-2 border-b border-[#30363d]">
              <div className="flex items-center gap-2 font-semibold text-sm text-[#f0f6fc]">
                <HistoryIcon size={16} className="text-[#58a6ff]" />
                <span>Review History Generations</span>
              </div>
              <button
                type="button"
                onClick={() => setShowHistoryModal(false)}
                className="text-[#8b949e] hover:text-[#f0f6fc] text-xs font-semibold px-2 py-1 rounded hover:bg-[#21262d] cursor-pointer focus:ring-1 focus:ring-[#58a6ff] outline-none"
              >
                Close
              </button>
            </div>

            <p className="text-xs text-[#8b949e]">
              PRism preserves every review generation per commit SHA. Old reviews remain intact as historical records when new commits are pushed.
            </p>

            <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
              {reviewHistory.map((rev) => {
                const isCurrentHead = rev.commit_sha === pr.head_sha
                const rVerdict: ReviewVerdict =
                  (rev.verdict as ReviewVerdict) ||
                  (rev.drift_detected
                    ? 'DRIFT'
                    : rev.exception_approved
                    ? 'APPROVED_EXCEPTION'
                    : (rev.findings?.length || 0) > 0
                    ? 'CONFORMING'
                    : 'NO_PRECEDENT')

                return (
                  <div
                    key={rev.id}
                    className="p-3 rounded border border-[#30363d] bg-[#161b22] text-xs space-y-1.5"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[#58a6ff] font-semibold text-[11px]">
                          {rev.commit_sha ? rev.commit_sha.slice(0, 7) : 'commit'}
                        </span>
                        {isCurrentHead ? (
                          <span className="px-1.5 py-0.2 rounded text-[10px] font-semibold bg-[#238636]/20 border border-[#238636]/40 text-[#3fb950]">
                            Latest HEAD
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.2 rounded text-[10px] font-semibold bg-[#30363d] text-[#8b949e]">
                            Outdated
                          </span>
                        )}
                      </div>
                      <span className="text-[10px] text-[#8b949e]">
                        {new Date(rev.created_at).toLocaleDateString()}
                      </span>
                    </div>

                    <div className="text-[11px] text-[#c9d1d9] flex items-center gap-2">
                      <span className="font-semibold text-[#f0f6fc]">Verdict:</span>
                      <span>{rVerdict}</span>
                      <span>•</span>
                      <span>{rev.findings?.length || rev.findings_count || 0} findings</span>
                    </div>

                    {rev.summary && (
                      <p className="text-[10px] text-[#8b949e] line-clamp-2 leading-relaxed">
                        {rev.summary}
                      </p>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {/* ================================================================= */}
      {/* PHASE 9: HUMAN CONFIRMATION MODAL                                 */}
      {/* ================================================================= */}
      {activeConfirmationCandidate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4 overflow-y-auto">
          <div className="bg-[#0d1117] border border-[#30363d] rounded-lg max-w-xl w-full p-5 space-y-4 shadow-2xl animate-fade-in my-8">
            <div className="flex items-center justify-between pb-3 border-b border-[#30363d]">
              <div className="flex items-center gap-2">
                <ShieldCheckIcon size={18} className="text-[#a371f7]" />
                <h3 className="text-sm font-semibold text-[#f0f6fc]">
                  Proposed Engineering Memory
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setActiveConfirmationCandidate(null)}
                className="text-[#8b949e] hover:text-[#f0f6fc] text-xs cursor-pointer p-1"
              >
                ✕
              </button>
            </div>

            <p className="text-xs text-[#8b949e] leading-relaxed">
              {activeConfirmationCandidate.proposed_authority === 'ENFORCEABLE'
                ? 'Confirming this candidate will record durable, canonical engineering memory in Supabase and sync to Hindsight semantic memory. It will actively inform and enforce architectural rules in future PR reviews for this repository.'
                : activeConfirmationCandidate.proposed_authority === 'EXCEPTION'
                ? 'Confirming this candidate records an explicit architectural exception in canonical memory. It permits this documented pattern for its defined scope without breaking existing overarching rules.'
                : 'Confirming this candidate records a contextual observation and review precedent in Supabase and Hindsight. It provides historical context for future PR reviews, but does not establish an enforceable architectural constraint.'}
            </p>

            {/* Candidate Metadata Summary */}
            <div className="grid grid-cols-2 gap-2 text-xs bg-[#161b22] p-3 rounded border border-[#21262d]">
              <div>
                <span className="text-[#8b949e] font-semibold block text-[11px]">Proposed Knowledge Type:</span>
                <span className="text-[#f0f6fc] font-mono text-[11px]">
                  {activeConfirmationCandidate.proposed_memory_type}
                </span>
              </div>
              <div>
                <span className="text-[#8b949e] font-semibold block text-[11px]">Proposed Authority:</span>
                <span className={`font-mono text-[11px] font-semibold ${
                  activeConfirmationCandidate.proposed_authority === 'ENFORCEABLE'
                    ? 'text-[#58a6ff]'
                    : activeConfirmationCandidate.proposed_authority === 'EXCEPTION'
                    ? 'text-[#a371f7]'
                    : 'text-[#8b949e]'
                }`}>
                  {activeConfirmationCandidate.proposed_authority}
                </span>
              </div>
              <div className="col-span-2 pt-1 border-t border-[#30363d]/50">
                <span className="text-[#8b949e] font-semibold block text-[11px]">Source Provenance:</span>
                <span className="text-[#7ee787] font-mono text-[11px]">
                  {activeConfirmationCandidate.source_refs?.join(', ') || `PR #${parsedPrNumber}`}
                  {activeConfirmationCandidate.commit_sha ? ` • ${activeConfirmationCandidate.commit_sha.slice(0, 7)}` : ''}
                </span>
              </div>
            </div>

            {/* Editable Fields for Developer Refinement */}
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-semibold text-[#c9d1d9] mb-1">
                  Architectural Rule / Decision Statement
                </label>
                <textarea
                  value={editStatement}
                  onChange={(e) => setEditStatement(e.target.value)}
                  rows={2}
                  className="w-full bg-[#161b22] border border-[#30363d] rounded p-2 text-xs text-[#f0f6fc] focus:border-[#58a6ff] outline-none"
                  placeholder="e.g. Controllers must use the service layer for persistence access."
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-[#c9d1d9] mb-1">
                  Documented Rationale
                </label>
                <textarea
                  value={editRationale}
                  onChange={(e) => setEditRationale(e.target.value)}
                  rows={2}
                  className="w-full bg-[#161b22] border border-[#30363d] rounded p-2 text-xs text-[#f0f6fc] focus:border-[#58a6ff] outline-none"
                  placeholder="e.g. Maintains transaction boundaries and prevents persistence coupling."
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-[#c9d1d9] mb-1">
                  Applicability Scope
                </label>
                <input
                  type="text"
                  value={editScope}
                  onChange={(e) => setEditScope(e.target.value)}
                  className="w-full bg-[#161b22] border border-[#30363d] rounded p-2 text-xs text-[#f0f6fc] focus:border-[#58a6ff] outline-none"
                  placeholder="e.g. backend/controllers/* or Repository architecture"
                />
              </div>
            </div>

            {feedbackErrorMessage && (
              <div role="alert" className="p-2.5 bg-[#f85149]/10 border border-[#f85149]/40 rounded text-xs text-[#f85149] flex items-center gap-2">
                <AlertIcon size={14} className="shrink-0" />
                <span>{feedbackErrorMessage}</span>
              </div>
            )}

            {/* Confirmation Action Buttons */}
            <div className="flex items-center justify-end gap-2 pt-2 border-t border-[#30363d]">
              <button
                type="button"
                disabled={isConfirmingCandidate}
                onClick={() => handleRejectCandidate(activeConfirmationCandidate.candidate_id)}
                className="px-3 py-1.5 rounded text-xs font-medium border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] text-[#8b949e] hover:text-[#f85149] cursor-pointer"
              >
                Reject Candidate
              </button>

              <button
                type="button"
                disabled={isConfirmingCandidate}
                onClick={() => setActiveConfirmationCandidate(null)}
                className="px-3 py-1.5 rounded text-xs font-medium border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] text-[#c9d1d9] cursor-pointer"
              >
                Cancel
              </button>

              <button
                type="button"
                disabled={isConfirmingCandidate || !editStatement.trim()}
                onClick={handleConfirmCandidate}
                className="px-3.5 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50 cursor-pointer shadow-xs"
              >
                {isConfirmingCandidate ? (
                  <SyncIcon size={12} className="animate-spin" />
                ) : (
                  <CheckIcon size={12} />
                )}
                <span>Confirm & Remember</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
