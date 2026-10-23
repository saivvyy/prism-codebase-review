import { createContext } from 'react'
import type { UserSessionProfile } from '@/types'

export interface AuthContextType {
  user: UserSessionProfile | null
  isAuthenticated: boolean
  isLoading: boolean
  isAuthenticating: boolean
  authError: string | null
  login: () => Promise<{ configured: boolean; error?: string }>
  logout: () => Promise<void>
  handleCallback: (code: string, state: string) => Promise<string>
  sessionToken: string | null
  clearAuthError: () => void
}

export const AuthContext = createContext<AuthContextType | undefined>(undefined)
