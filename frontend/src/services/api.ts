import type {
  AuthSessionResponse,
  AuthStatusResponse,
  ConnectedRepo,
  EngineeringMemory,
  GitHubPR,
  GitHubPRListResponse,
  HealthStatus,
  ListEngineeringMemoriesResponse,
  LoginUrlResponse,
  PRDetailResponse,
  PRWalkthrough,
  PublicRepoSearchResponse,
  RecallMemoryResponse,
  RecentReview,
  RecordMemoryRequest,
  ReviewAnalysisOutput,
  SystemStatus,
  UserRepoItem,
  FixCandidate,
  FixListResponse,
  LearningCandidate,
  DeveloperFeedbackRequest,
  ConfirmLearningCandidateRequest,
  RejectLearningCandidateRequest,
  ConfirmCandidateResponse,
  PREvidenceResponse,
} from '@/types'

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const SESSION_TOKEN_KEY = 'prism_session_token'

export function getSessionToken(): string | null {
  try {
    return localStorage.getItem(SESSION_TOKEN_KEY)
  } catch {
    return null
  }
}

export function setSessionToken(token: string | null): void {
  try {
    if (token) {
      localStorage.setItem(SESSION_TOKEN_KEY, token)
    } else {
      localStorage.removeItem(SESSION_TOKEN_KEY)
    }
  } catch {
    // localStorage might be unavailable or restricted
  }
}

export class ApiError extends Error {
  status: number
  statusText: string
  detail?: string
  isNetworkError: boolean

  constructor(status: number, statusText: string, message: string, detail?: string, isNetworkError: boolean = false) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.statusText = statusText
    this.detail = detail
    this.isNetworkError = isNetworkError
  }
}

async function request<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`
  const token = getSessionToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...((options?.headers as Record<string, string>) || {}),
  }

  let response: Response
  try {
    response = await fetch(url, { ...options, headers })
  } catch (err) {
    if (err instanceof TypeError && (err.message.toLowerCase().includes('fetch') || err.message.toLowerCase().includes('network') || err.message.toLowerCase().includes('failed'))) {
      throw new ApiError(
        0,
        'Network Error',
        `Unable to reach backend server at ${API_BASE_URL}. Please verify your connection and that the backend service is running.`,
        undefined,
        true
      )
    }
    if (err instanceof ApiError) {
      throw err
    }
    throw new ApiError(0, 'Network Error', err instanceof Error ? err.message : String(err), undefined, true)
  }

  if (!response.ok) {
    let serverDetail: string | undefined
    try {
      const text = await response.text()
      if (text) {
        try {
          const errJson = JSON.parse(text)
          if (errJson && errJson.detail) {
            serverDetail = typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail)
          } else if (typeof errJson === 'string') {
            serverDetail = errJson
          }
        } catch {
          serverDetail = text.length < 300 ? text : undefined
        }
      }
    } catch {
      // ignore body read error
    }

    let cleanDetail = (serverDetail || '').trim()
    while (cleanDetail.endsWith(':')) {
      cleanDetail = cleanDetail.slice(0, -1).trim()
    }

    let userFriendlyMessage: string
    switch (response.status) {
      case 401:
        userFriendlyMessage = cleanDetail || 'GitHub authentication required. Your session may have expired.'
        break
      case 403:
        userFriendlyMessage = cleanDetail || 'GitHub denied access or repository is not available to the current user.'
        break
      case 404:
        userFriendlyMessage = cleanDetail || 'The requested resource or pull request was not found.'
        break
      case 409:
        userFriendlyMessage = cleanDetail || 'A state conflict occurred. Please refresh and try again.'
        break
      case 422:
        userFriendlyMessage = cleanDetail || 'The request could not be validated. Please check the provided inputs.'
        break
      case 429:
        userFriendlyMessage = cleanDetail || 'GitHub or PRism rate limit exceeded. Please wait a moment before retrying.'
        break
      case 500:
        userFriendlyMessage = cleanDetail && !cleanDetail.toLowerCase().includes('traceback')
          ? cleanDetail
          : 'PRism failed while processing this request.'
        break
      case 502:
      case 503:
      case 504:
        userFriendlyMessage = cleanDetail || 'GitHub API request failed or timed out. Please try again.'
        break
      default:
        userFriendlyMessage = cleanDetail || `Request failed with status ${response.status}.`
        break
    }

    throw new ApiError(
      response.status,
      response.statusText,
      userFriendlyMessage,
      cleanDetail || undefined,
      false
    )
  }

  // 1. Status 204 (No Content) or 205 (Reset Content): no body by HTTP specification
  if (response.status === 204 || response.status === 205) {
    return undefined as unknown as T
  }

  // 2. Explicit Content-Length: 0 header check
  const contentLength = response.headers.get('content-length')
  if (contentLength === '0') {
    return undefined as unknown as T
  }

  // 3. Read body safely as text first to verify non-empty before JSON parsing
  const text = await response.text()
  if (!text || !text.trim()) {
    return undefined as unknown as T
  }

  try {
    return JSON.parse(text) as T
  } catch (err) {
    console.error(`Failed to parse JSON response from ${endpoint}:`, err, text)
    throw new Error(`Invalid JSON response received from ${endpoint}`)
  }
}

export async function fetchHealth(): Promise<HealthStatus> {
  return request<HealthStatus>('/health')
}

export async function listRepositories(): Promise<ConnectedRepo[]> {
  return request<ConnectedRepo[]>('/api/v1/repositories')
}

export async function connectRepository(fullName: string): Promise<ConnectedRepo> {
  return request<ConnectedRepo>('/api/v1/repositories/connect', {
    method: 'POST',
    body: JSON.stringify({ full_name: fullName.trim() }),
  })
}

export async function disconnectRepository(repoId: string): Promise<void> {
  await request<void>(`/api/v1/repositories/${encodeURIComponent(repoId)}`, {
    method: 'DELETE',
  })
}

export async function getRepositoryPRCount(repoId: string): Promise<number> {
  const data = await request<{ repo_id: string; pr_count: number; open_pr_count?: number }>(
    `/api/v1/repositories/${encodeURIComponent(repoId)}/pr-count`
  )
  return data.open_pr_count ?? data.pr_count ?? 0
}

export async function getRepositoryOpenPRCount(repoId: string): Promise<number> {
  const data = await request<{ repo_id: string; open_pr_count: number; pr_count?: number }>(
    `/api/v1/repositories/${encodeURIComponent(repoId)}/open-pr-count`
  )
  return data.open_pr_count ?? data.pr_count ?? 0
}

export async function listPullRequests(repoId: string, state: string = 'all'): Promise<GitHubPRListResponse> {
  const data = await request<GitHubPRListResponse | GitHubPR[]>(
    `/api/v1/repositories/${encodeURIComponent(repoId)}/pulls?state=${encodeURIComponent(state)}`
  )
  if (Array.isArray(data)) {
    return {
      items: data,
      total_fetched: data.length,
      pages_fetched: 1,
      per_page: 100,
      fetch_limit: 500,
      complete: true,
      truncated: false,
      fetch_duration_ms: 0,
      last_synced_at: new Date().toISOString(),
    }
  }
  return data
}

export async function getPullRequestDetail(repoId: string, prNumber: number): Promise<PRDetailResponse> {
  return request<PRDetailResponse>(`/api/v1/repositories/${encodeURIComponent(repoId)}/pulls/${prNumber}`)
}

export async function triggerReview(repoId: string, prNumber: number): Promise<ReviewAnalysisOutput> {
  return request<ReviewAnalysisOutput>(`/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}`, {
    method: 'POST',
  })
}

export async function getPRReview(repoId: string, prNumber: number): Promise<RecentReview | null> {
  try {
    return await request<RecentReview | null>(`/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}`)
  } catch {
    return null
  }
}

export async function getPRReviewHistory(repoId: string, prNumber: number): Promise<RecentReview[]> {
  try {
    return await request<RecentReview[]>(`/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}/history`)
  } catch {
    return []
  }
}

export async function listRecentReviews(limit: number = 15, repoId?: string): Promise<RecentReview[]> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (repoId) params.set('repo_id', repoId)
  return request<RecentReview[]>(`/api/v1/reviews/recent?${params.toString()}`)
}

export async function recallMemories(params?: {
  query?: string
  bank_id?: string
  category?: string
  repo_id?: string
}): Promise<RecallMemoryResponse> {
  const searchParams = new URLSearchParams()
  if (params?.query) searchParams.set('query', params.query)
  if (params?.bank_id) searchParams.set('bank_id', params.bank_id)
  if (params?.category) searchParams.set('category', params.category)
  if (params?.repo_id) searchParams.set('repo_id', params.repo_id)

  const queryStr = searchParams.toString()
  return request<RecallMemoryResponse>(`/api/v1/memory/recall${queryStr ? `?${queryStr}` : ''}`)
}

export async function retainMemory(
  content: string,
  category: string = 'architectural_decision',
  bankId?: string
): Promise<{ success: boolean; bank_id: string }> {
  return request<{ success: boolean; bank_id: string }>('/api/v1/memory/retain', {
    method: 'POST',
    body: JSON.stringify({
      content,
      bank_id: bankId,
      tags: [category, 'manual_entry'],
      metadata: { category, source: 'frontend_entry' },
    }),
  })
}

export async function listRepositoryMemories(
  repoId: string,
  category?: string,
  limit: number = 50
): Promise<ListEngineeringMemoriesResponse> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (category && category !== 'all') params.set('category', category)
  return request<ListEngineeringMemoriesResponse>(
    `/api/v1/repositories/${encodeURIComponent(repoId)}/memory?${params.toString()}`
  )
}

export async function recordRepositoryMemory(
  repoId: string,
  data: RecordMemoryRequest
): Promise<EngineeringMemory> {
  return request<EngineeringMemory>(
    `/api/v1/repositories/${encodeURIComponent(repoId)}/memory`,
    {
      method: 'POST',
      body: JSON.stringify(data),
    }
  )
}

export async function searchRepositoryMemories(
  repoId: string,
  query: string
): Promise<ListEngineeringMemoriesResponse> {
  const params = new URLSearchParams({ q: query })
  return request<ListEngineeringMemoriesResponse>(
    `/api/v1/repositories/${encodeURIComponent(repoId)}/memory/search?${params.toString()}`
  )
}

export async function deleteRepositoryMemory(
  repoId: string,
  memoryId: string
): Promise<void> {
  return request<void>(
    `/api/v1/repositories/${encodeURIComponent(repoId)}/memory/${encodeURIComponent(memoryId)}`,
    { method: 'DELETE' }
  )
}

export async function fetchSystemStatus(): Promise<SystemStatus> {
  return request<SystemStatus>('/api/v1/settings/status')
}

// ============================================================================
// AUTHENTICATION & GITHUB IDENTITY API
// ============================================================================

export async function getGitHubLoginUrl(redirectUri?: string): Promise<LoginUrlResponse> {
  const query = redirectUri ? `?redirect_uri=${encodeURIComponent(redirectUri)}` : ''
  return request<LoginUrlResponse>(`/api/v1/auth/github/login${query}`)
}

export async function handleOAuthCallback(code: string, state: string): Promise<AuthSessionResponse> {
  return request<AuthSessionResponse>('/api/v1/auth/github/callback', {
    method: 'POST',
    body: JSON.stringify({ code, state }),
  })
}

export async function fetchCurrentUser(): Promise<AuthStatusResponse> {
  return request<AuthStatusResponse>('/api/v1/auth/me')
}

export async function logoutUser(): Promise<{ success: boolean }> {
  return request<{ success: boolean }>('/api/v1/auth/logout', {
    method: 'POST',
  })
}

export async function listUserGitHubRepositories(page: number = 1, per_page: number = 30): Promise<UserRepoItem[]> {
  const params = new URLSearchParams({
    page: String(page),
    per_page: String(per_page),
  })
  return request<UserRepoItem[]>(`/api/v1/auth/github/repositories?${params.toString()}`)
}

export async function searchPublicRepositories(
  query: string,
  page: number = 1,
  per_page: number = 30
): Promise<PublicRepoSearchResponse> {
  const params = new URLSearchParams({
    q: query,
    page: String(page),
    per_page: String(per_page),
  })
  return request<PublicRepoSearchResponse>(`/api/v1/auth/github/search?${params.toString()}`)
}

export async function getPRWalkthrough(repoId: string, prNumber: number): Promise<PRWalkthrough> {
  return request<PRWalkthrough>(`/api/v1/reviews/${repoId}/${prNumber}/walkthrough`)
}

// ============================================================================
// PHASE 8: CONTEXT-AWARE FIXES API
// ============================================================================

export async function getPRFixes(
  repoId: string,
  prNumber: number
): Promise<FixListResponse> {
  return request<FixListResponse>(`/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}/fixes`)
}

export async function generatePRFixes(
  repoId: string,
  prNumber: number,
  findingId?: string
): Promise<FixListResponse> {
  return request<FixListResponse>(
    `/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}/fixes/generate`,
    {
      method: 'POST',
      body: JSON.stringify({ finding_id: findingId }),
    }
  )
}

export async function validatePRFix(
  repoId: string,
  prNumber: number,
  fixId: string
): Promise<FixCandidate> {
  return request<FixCandidate>(
    `/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}/fixes/${encodeURIComponent(fixId)}/validate`,
    { method: 'POST' }
  )
}

export async function approvePRFix(
  repoId: string,
  prNumber: number,
  fixId: string
): Promise<FixCandidate> {
  return request<FixCandidate>(
    `/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}/fixes/${encodeURIComponent(fixId)}/approve`,
    { method: 'POST' }
  )
}

export async function applyPRFix(
  repoId: string,
  prNumber: number,
  fixId: string
): Promise<FixCandidate> {
  return request<FixCandidate>(
    `/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}/fixes/${encodeURIComponent(fixId)}/apply`,
    { method: 'POST' }
  )
}

export async function rejectPRFix(
  repoId: string,
  prNumber: number,
  fixId: string
): Promise<FixCandidate> {
  return request<FixCandidate>(
    `/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}/fixes/${encodeURIComponent(fixId)}/reject`,
    { method: 'POST' }
  )
}

// =========================================================================
// PHASE 9: DEVELOPER FEEDBACK & CONTROLLED LEARNING
// =========================================================================

export async function submitDeveloperFeedback(
  repoId: string,
  prNumber: number,
  feedback: DeveloperFeedbackRequest
): Promise<LearningCandidate> {
  return request<LearningCandidate>(
    `/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}/feedback`,
    {
      method: 'POST',
      body: JSON.stringify(feedback),
    }
  )
}

export async function listPRLearningCandidates(
  repoId: string,
  prNumber: number
): Promise<LearningCandidate[]> {
  return request<LearningCandidate[]>(
    `/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}/learning-candidates`
  )
}

export async function validatePRLearningCandidate(
  repoId: string,
  prNumber: number,
  candidateId: string
): Promise<LearningCandidate> {
  return request<LearningCandidate>(
    `/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}/learning-candidates/${encodeURIComponent(candidateId)}/validate`,
    { method: 'POST' }
  )
}

export async function confirmPRLearningCandidate(
  repoId: string,
  prNumber: number,
  candidateId: string,
  confirmation: ConfirmLearningCandidateRequest
): Promise<ConfirmCandidateResponse> {
  return request<ConfirmCandidateResponse>(
    `/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}/learning-candidates/${encodeURIComponent(candidateId)}/confirm`,
    {
      method: 'POST',
      body: JSON.stringify(confirmation),
    }
  )
}

export async function rejectPRLearningCandidate(
  repoId: string,
  prNumber: number,
  candidateId: string,
  rejection: RejectLearningCandidateRequest = {}
): Promise<LearningCandidate> {
  return request<LearningCandidate>(
    `/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}/learning-candidates/${encodeURIComponent(candidateId)}/reject`,
    {
      method: 'POST',
      body: JSON.stringify(rejection),
    }
  )
}

export async function getPREvidence(
  repoId: string,
  prNumber: number
): Promise<PREvidenceResponse> {
  return request<PREvidenceResponse>(
    `/api/v1/reviews/${encodeURIComponent(repoId)}/${prNumber}/evidence`
  )
}



