import { useContext } from 'react'
import { AuthContext, type AuthContextType } from './auth-context-def'

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}

export type { AuthContextType }
