import { useContext } from 'react'
import { RepoContext, type RepoContextType } from './repo-context-def'

export function useRepo(): RepoContextType {
  const context = useContext(RepoContext)
  if (!context) {
    throw new Error('useRepo must be used within a RepoProvider')
  }
  return context
}
