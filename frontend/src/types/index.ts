export interface HealthStatus {
  status: string
  version: string
  environment: string
  timestamp?: string
}

export interface ConnectedRepo {
  id: string
  full_name: string
  github_repo_id: number
  default_branch: string
  webhook_active: boolean
  status?: 'active' | 'disconnected' | string
  is_connected?: boolean
  disconnected_at?: string | null
  created_at?: string
  pull_requests_count?: number
  open_pr_count?: number
}

export interface ConnectRepoRequest {
  full_name: string
}

export interface GitHubPR {
  id: number
  number: number
  title: string
  body?: string | null
  state: 'open' | 'closed' | 'merged' | string
  author: string
  html_url?: string
  created_at: string
  updated_at: string
  closed_at?: string | null
  merged_at?: string | null
  head_sha: string
  base_sha?: string
  base_branch?: string
  is_merged?: boolean
}

export interface GitHubPRListResponse {
  items: GitHubPR[]
  total_fetched: number
  pages_fetched: number
  per_page: number
  fetch_limit: number
  complete: boolean
  truncated: boolean
  fetch_duration_ms: number
  last_synced_at?: string | null
  total_repository_prs?: number
  open_pr_count?: number
}


export interface GitHubFileDiff {
  filename: string
  status: 'added' | 'removed' | 'modified' | 'renamed' | string
  additions: number
  deletions: number
  changes: number
  patch?: string | null
}

export interface GitHubCommit {
  sha: string
  message: string
  author: string
  date: string
}

export interface GitHubReviewComment {
  id: number
  path: string
  line?: number | null
  body: string
  user: string
  created_at: string
}

export interface PRDetailResponse {
  pr: GitHubPR
  files: GitHubFileDiff[]
  commits: GitHubCommit[]
  comments: GitHubReviewComment[]
}

export type FindingCategory =
  | 'architecture_drift'
  | 'rejected_pattern'
  | 'approved_exception'
  | 'convention_violation'
  | 'note'
  | 'review_observation'

export type FindingSeverity = 'info' | 'warning' | 'error'

export type ReviewVerdict =
  | 'NO_PRECEDENT'
  | 'CONFORMING'
  | 'DRIFT'
  | 'APPROVED_EXCEPTION'
  | 'CONFLICTING_GUIDANCE'

export interface HistoricalEvidence {
  claim: string
  rationale?: string | null
  evidence_prs?: string[]
  matched_code?: string | null
  reasoning?: string | null
  exception_notes?: string | null
  scope?: string | null
  source_pr_ref?: string | null
  source_pr_number?: number | null
  provenance_status?: 'verified' | 'unverified' | 'unavailable' | 'none' | string
}

export interface ReviewFinding {
  id?: string
  category: FindingCategory
  severity: FindingSeverity
  file_path: string
  line_number?: number | null
  title: string
  message: string
  decision?: string | null
  rationale?: string | null
  action?: string | null
  scope?: string | null
  source?: string | null
  historical_pr_ref?: string | null
  suggested_fix?: string | null
  hindsight_memory_id?: string | null
  provenance_status?: 'verified' | 'unverified' | 'unavailable' | 'none' | string
  historical_evidence?: HistoricalEvidence | null
}

export interface DurableMemoryCandidate {
  claim: string
  type: MemoryCategory
  rationale?: string | null
  scope?: string | null
  confidence?: MemoryConfidence
}

export interface ReviewAnalysisOutput {
  summary: string
  drift_detected: boolean
  exception_approved: boolean
  verdict?: ReviewVerdict
  confidence_score: number
  commit_sha?: string | null
  historical_context_snapshot?: Array<{
    claim?: string
    statement?: string
    rationale?: string
    scope?: string
    source_pr_ref?: string | null
    source_pr_number?: number | null
    provenance_status?: string
    memory_category?: string
    memory_id?: string
  }> | null
  findings: ReviewFinding[]
  durable_memory_candidates?: DurableMemoryCandidate[]
}

export interface RecentReview {
  id: string
  pull_request_id: string
  commit_sha: string
  status: 'pending' | 'in_progress' | 'completed' | 'failed' | string
  summary?: string | null
  drift_detected: boolean
  exception_approved: boolean
  verdict?: ReviewVerdict
  historical_context_snapshot?: Array<{
    claim?: string
    statement?: string
    rationale?: string
    scope?: string
    source_pr_ref?: string | null
    source_pr_number?: number | null
    provenance_status?: string
    memory_category?: string
    memory_id?: string
  }> | null
  created_at: string
  completed_at?: string | null
  pr_number?: number | null
  pr_title?: string | null
  author?: string | null
  repository_id?: string | null
  repository_name?: string | null
  findings_count: number
  findings?: ReviewFinding[]
}

export type MemoryCategory =
  | 'architecture_decision'
  | 'rejected_pattern'
  | 'approved_exception'
  | 'review_decision'
  | 'lesson_learned'
  | 'DOCUMENTED_DECISION'
  | 'APPROVED_EXCEPTION'
  | 'REJECTED_PATTERN'
  | 'OBSERVED_CONVENTION'
  | 'REVIEW_OBSERVATION'

export type MemoryConfidence = 'supported' | 'partially_supported' | 'insufficient_evidence'

export interface EngineeringMemory {
  id: string
  repository_id: string
  type: MemoryCategory
  claim: string
  statement?: string | null
  authority?: 'ENFORCEABLE' | 'EXCEPTION' | 'REJECTED_PATTERN' | 'CONTEXTUAL' | 'NON_AUTHORITATIVE' | string
  rationale?: string | null
  scope?: string | null
  status: 'active' | 'deprecated' | 'superseded' | 'ACTIVE' | 'SUPERSEDED' | 'REVOKED' | string
  source_type: 'manual' | 'pr_review' | 'github_issue' | 'team_agreement' | string
  source_ref?: string | null
  source_url?: string | null
  review_id?: string | null
  pr_number?: number | null
  repository_full_name?: string | null
  github_repo_id?: number | null
  confidence: MemoryConfidence
  evidence_items: string[]
  hindsight_document_id?: string | null
  sync_state?: 'SYNCED' | 'UNAVAILABLE' | 'FAILED' | string
  observation_count?: number
  supersedes?: string | null
  superseded_by?: string | null
  created_at: string
  updated_at: string
}

export interface ListEngineeringMemoriesResponse {
  repository_id: string
  memories: EngineeringMemory[]
  total_count: number
}

export interface RecordMemoryRequest {
  claim: string
  type: MemoryCategory
  rationale?: string
  scope?: string
  source_ref?: string
  source_url?: string
  confidence?: MemoryConfidence
  evidence_items?: string[]
}

export type FeedbackType =
  | 'CORRECT'
  | 'NOT_A_VIOLATION'
  | 'APPROVED_EXCEPTION'
  | 'ARCHITECTURE_CHANGED'
  | 'REMEMBER_THIS_DECISION'

export type CandidateStatus =
  | 'DRAFT'
  | 'PENDING_VALIDATION'
  | 'VALIDATED'
  | 'CONFLICTING'
  | 'REQUIRES_CONFIRMATION'
  | 'CONFIRMED'
  | 'PERSISTED'
  | 'REJECTED'
  | 'SUPERSEDED'
  | 'FAILED'

export interface LearningCandidate {
  candidate_id: string
  repository_id: string
  repository_full_name: string
  pr_number?: number | null
  review_id?: string | null
  commit_sha?: string | null
  feedback_type?: FeedbackType | null
  statement: string
  rationale: string
  proposed_memory_type: MemoryCategory
  proposed_authority: string
  scope: string
  evidence_refs: string[]
  source_refs: string[]
  provenance: Record<string, any>
  conflicting_memory_ids: string[]
  supersedes_memory_ids: string[]
  status: CandidateStatus
  validation_reasons: string[]
  persisted_memory_id?: string | null
  created_at: string
  updated_at: string
}

export interface DeveloperFeedbackRequest {
  feedback_type: FeedbackType
  finding_id?: string | null
  finding_title?: string | null
  statement?: string | null
  rationale?: string | null
  scope?: string | null
  parent_rule_id?: string | null
  supersedes_rule_id?: string | null
  commit_sha?: string | null
  file_path?: string | null
}

export interface ConfirmLearningCandidateRequest {
  confirmed_statement?: string | null
  confirmed_rationale?: string | null
  confirmed_scope?: string | null
  supersedes_rule_id?: string | null
}

export interface RejectLearningCandidateRequest {
  reason?: string | null
}

export interface ConfirmCandidateResponse {
  status: string
  candidate: LearningCandidate
  canonical_memory: EngineeringMemory
  superseded_memory?: EngineeringMemory | null
  hindsight_sync_status: string
}

export interface MemoryItem {
  id?: string
  text: string
  type?: string | null
  score?: number | null
  metadata?: {
    category?: string
    severity?: string
    file_path?: string
    historical_pr_ref?: string
    repository_name?: string
    created_at?: string
    claim?: string
    rationale?: string
    scope?: string
    evidence_items?: string[]
    [key: string]: unknown
  } | null
  tags?: string[] | null
}

export interface RecallMemoryResponse {
  bank_id: string
  query: string
  results_count: number
  items: MemoryItem[]
  prompt_context: string
}

export interface SystemServiceStatus {
  configured: boolean
  [key: string]: unknown
}

export interface SystemStatus {
  version: string
  environment: string
  services: {
    github: {
      configured: boolean
      user?: { login: string; name?: string; avatar_url?: string } | null
      api_endpoint: string
    }
    supabase: {
      configured: boolean
      url?: string | null
      storage_mode: string
    }
    hindsight: {
      configured: boolean
      base_url?: string | null
      default_bank_id?: string | null
    }
    groq: {
      configured: boolean
      reasoning_model?: string | null
      lightweight_model?: string | null
    }
    tree_sitter: {
      active: boolean
      supported_languages: string[]
    }
  }
}

export interface UserSessionProfile {
  id: string
  github_user_id: number
  login: string
  name?: string | null
  avatar_url?: string | null
  email?: string | null
}

export interface AuthStatusResponse {
  authenticated: boolean
  user?: UserSessionProfile | null
}

export interface LoginUrlResponse {
  url?: string | null
  configured: boolean
  client_id?: string | null
  state?: string | null
}

export interface OAuthCallbackRequest {
  code: string
  state: string
}

export interface AuthSessionResponse {
  session_token: string
  user: UserSessionProfile
  redirect_to: string
}

export interface UserRepoItem {
  id: number
  name: string
  full_name: string
  owner: string
  owner_avatar_url?: string | null
  owner_type?: string | null
  private: boolean
  description?: string | null
  default_branch: string
  html_url?: string | null
  stargazers_count?: number | null
  updated_at?: string | null
  access_role?: string | null
  connected: boolean
  connected_repo_id?: string | null
}


export interface PublicRepoSearchResponse {
  total_count: number
  items: UserRepoItem[]
  page: number
  per_page: number
}

// ─── Phase 7: Semantic PR Walkthrough ────────────────────────────────────────

export type ChangeGroupKind =
  | 'schema'
  | 'api'
  | 'service'
  | 'test'
  | 'configuration'
  | 'documentation'
  | 'dependency'
  | 'ui'
  | 'types'
  | 'utility'
  | 'flow'
  | 'unknown'

export type ChangeGroupImpact = 'contained' | 'cross_cutting' | 'foundational'

export type SemanticRelationshipType =
  | 'CALLS'
  | 'IMPORTS'
  | 'REFERENCES'
  | 'DEFINES'
  | 'DEPENDS_ON'
  | 'TESTS'
  | 'RELATED_TO'

export interface SemanticRelationship {
  source: string
  target: string
  relationship: SemanticRelationshipType
  detail?: string | null
  evidence_source?: string
}

export interface GroupTestMapping {
  test_file_path: string
  target_symbol_or_file: string
  test_name?: string | null
  relationship: string
  is_in_pr_diff: boolean
}

export interface GroupFindingEntry {
  id?: string | null
  category: string
  severity: string
  title: string
  message: string
  file_path: string
  line_number?: number | null
  historical_pr_ref?: string | null
  rationale?: string | null
}

export interface HunkSymbolDetail {
  file_path: string
  start_line: number
  end_line: number
  enclosing_symbol?: string | null
  changed_symbols: string[]
  calls: string[]
  imports: string[]
}

export interface FileChangeEntry {
  path: string
  status: string
  additions: number
  deletions: number
  language: string
  affected_symbols: string[]
  hunks?: HunkSymbolDetail[]
  note?: string | null
}

export interface ChangeGroup {
  id: string
  kind: ChangeGroupKind
  label: string
  description: string
  purpose?: string
  observed_implementation?: string
  impact: ChangeGroupImpact
  impact_justification?: string
  files: FileChangeEntry[]
  symbols_changed: string[]
  relationships?: SemanticRelationship[]
  tests?: GroupTestMapping[]
  findings?: GroupFindingEntry[]
  evidence?: string[]
  related_findings_count: number
  has_test_coverage: boolean
  ordering: number
  total_files_count?: number
  total_symbols_count?: number
  total_relationships_count?: number
  truncated_files?: number
  truncated_symbols?: number
  truncated_relationships?: number
}

export interface WalkthroughStep {
  step_number: number
  group_id: string
  headline: string
  detail: string
  files_count: number
  impact: ChangeGroupImpact
  primary_symbols?: string[]
  key_relationships?: string[]
}

export interface PRWalkthrough {
  pr_number: number
  pr_title: string
  repository_id: string
  head_sha: string
  intent_summary: string
  declared_intent?: string
  observed_implementation?: string
  change_type: string
  groups: ChangeGroup[]
  steps: WalkthroughStep[]
  total_files_changed: number
  total_additions: number
  total_deletions: number
  groups_count: number
  has_schema_changes: boolean
  has_api_changes: boolean
  has_test_changes: boolean
  has_config_changes: boolean
  surfaced_finding_count: number
  is_truncated?: boolean
  truncation_notes?: string[]
  derived_from: string
}

// ---------------------------------------------------------------------------
// Phase 8: Context-Aware Fix Types
// ---------------------------------------------------------------------------

export type FixCandidateStatus =
  | 'DRAFT'
  | 'VALIDATED'
  | 'READY_FOR_APPROVAL'
  | 'APPROVED'
  | 'APPLIED'
  | 'REJECTED'
  | 'FAILED'

export type FixValidationStatus =
  | 'NOT_RUN'
  | 'PASSED'
  | 'FAILED'
  | 'BLOCKED'

export type FindingResolutionStatus =
  | 'RESOLVED'
  | 'STILL_PRESENT'
  | 'UNKNOWN'

export interface ValidationCheckResult {
  check_id: string
  name: string
  status: FixValidationStatus
  message: string
  blocking: boolean
}

export interface PostApplyValidation {
  original_finding_status: FindingResolutionStatus
  validation_status: FixValidationStatus
  new_findings_count: number
  details: string[]
  re_evaluated_at?: string | null
}

export interface FixCandidate {
  id: string
  finding_id: string
  repository_id: string
  pr_number: number
  base_sha: string
  title: string
  explanation: string
  affected_files: string[]
  affected_symbols: string[]
  proposed_change: string
  rationale: string
  applicable_constraint?: string | null
  precedent_source?: string | null
  confidence_status: string
  patch_diff: string
  status: FixCandidateStatus
  validation_status: FixValidationStatus
  validation_checks: ValidationCheckResult[]
  warnings: string[]
  relevant_tests: string[]
  is_approved: boolean
  approved_by?: string | null
  approved_at?: string | null
  applied_at?: string | null
  post_apply_validation?: PostApplyValidation | null
  created_at: string
  updated_at: string
}

export interface FixListResponse {
  repository_id: string
  pr_number: number
  head_sha: string
  fixes: FixCandidate[]
  total_count: number
  can_apply?: boolean
  unavailable_reasons?: Record<string, string>
}

// ==========================================
// Phase 10: Deep PR Evidence & History Models
// ==========================================

export interface CommitEvidence {
  sha: string
  short_sha: string
  message: string
  author?: string | null
  author_email?: string | null
  authored_at?: string | null
  committed_at?: string | null
  additions?: number | null
  deletions?: number | null
  changed_files_count?: number | null
  changed_files: string[]
  url?: string | null
}

export interface ConversationEvidence {
  id: string
  source_type: 'issue_comment' | 'review_comment' | 'review_summary' | string
  author: string
  body: string
  created_at: string
  updated_at?: string | null
  url?: string | null
  file_path?: string | null
  line?: number | null
  side?: string | null
  commit_sha?: string | null
  resolved?: boolean | null
  review_id?: string | null
  in_reply_to_id?: string | null
}

export interface CheckEvidence {
  id: string
  name: string
  status: 'completed' | 'in_progress' | 'queued' | 'pending' | string
  conclusion?: 'success' | 'failure' | 'neutral' | 'cancelled' | 'timed_out' | 'action_required' | 'skipped' | string | null
  workflow_name?: string | null
  started_at?: string | null
  completed_at?: string | null
  url?: string | null
  details_url?: string | null
}

export interface HistoricalPREvidence {
  repository: string
  pr_number: number
  title: string
  state: string
  merged: boolean
  merged_at?: string | null
  closed_at?: string | null
  relevant_files: string[]
  relevant_symbols: string[]
  relevance_reason: string
  relevance_score: number
  source_url?: string | null
}

export interface PREvidenceSummary {
  total_commits: number
  total_conversation_items: number
  total_checks: number
  checks_success_count: number
  checks_failed_count: number
  checks_pending_count: number
  total_historical_prs: number
  commits_truncated: boolean
  conversation_truncated: boolean
  checks_truncated: boolean
  historical_prs_truncated: boolean
}

export interface PREvidenceResponse {
  repository_id: string
  pr_number: number
  head_sha: string
  commits: CommitEvidence[]
  conversation: ConversationEvidence[]
  checks: CheckEvidence[]
  historical_prs: HistoricalPREvidence[]
  summary: PREvidenceSummary
}


