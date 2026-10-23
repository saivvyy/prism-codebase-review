import { createContext } from 'react'
import type { ConnectedRepo, GitHubPR, GitHubPRListResponse, RecentReview, EngineeringMemory } from '@/types'

export interface RepoContextType {
  repositories: ConnectedRepo[]
  activeRepo: ConnectedRepo | null
  setActiveRepo: (repo: ConnectedRepo | null) => void
  isLoading: boolean
  isLoadingRepositories: boolean
  isSyncing: boolean
  error: string | null
  activePR: GitHubPR | null
  setActivePR: (pr: GitHubPR | null) => void
  pullRequestsByRepo: Record<string, GitHubPR[]>
  pullRequestsByRepoState: Record<string, GitHubPR[]>
  prMetaByRepo: Record<string, GitHubPRListResponse>
  fetchPullRequests: (repoId: string, stateOrForce?: string | boolean, force?: boolean) => Promise<GitHubPR[]>
  reviewsByRepo: Record<string, RecentReview[]>
  fetchReviews: (repoId: string, force?: boolean) => Promise<RecentReview[]>
  syncRepository: (repoId: string) => Promise<void>
  syncingRepos: Record<string, boolean>
  refreshRepos: (force?: boolean, state?: string) => Promise<void>
  addRepository: (repo: ConnectedRepo) => void
  removeRepository: (repoId: string) => void
  repoPRCounts: Record<string, number>
  fetchRepoPRCount: (repoId: string) => Promise<number>
  // Engineering Memory Caching and Invalidation
  memoryCache: Record<string, EngineeringMemory[]>
  memoryRequestState: Record<string, 'idle' | 'loading' | 'success' | 'error'>
  memoryError: Record<string, string | null>
  memoryLastFetched: Record<string, number>
  fetchRepositoryMemories: (repoId: string, category?: string, query?: string, force?: boolean) => Promise<EngineeringMemory[]>
  invalidateRepoReviewState: (repoId: string) => Promise<void>
  invalidateRepoMemory: (repoId: string) => void
  isSidebarOpen: boolean
  toggleSidebar: () => void
  setSidebarOpen: (open: boolean) => void
}

export const RepoContext = createContext<RepoContextType | undefined>(undefined)
