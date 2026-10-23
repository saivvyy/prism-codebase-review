import React, { useState, useEffect, useCallback } from 'react'
import type { UserSessionProfile } from '@/types'
import {
  fetchCurrentUser,
  getGitHubLoginUrl,
  getSessionToken,
  handleOAuthCallback,
  logoutUser,
  setSessionToken,
} from '@/services/api'
import { AuthContext } from './auth-context-def'

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<UserSessionProfile | null>(null)
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(false)
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [isAuthenticating, setIsAuthenticating] = useState<boolean>(false)
  const [authError, setAuthError] = useState<string | null>(null)
  const [sessionToken, setLocalSessionToken] = useState<string | null>(getSessionToken())

  // Restore authenticated session on mount or refresh
  useEffect(() => {
    let isMounted = true
    const initSession = async () => {
      const existingToken = getSessionToken()
      if (!existingToken) {
        if (isMounted) {
          setIsLoading(false)
          setIsAuthenticated(false)
          setUser(null)
        }
        return
      }

      try {
        const res = await fetchCurrentUser()
        if (isMounted) {
          if (res.authenticated && res.user) {
            setUser(res.user)
            setIsAuthenticated(true)
            setLocalSessionToken(existingToken)
          } else {
            // Token expired or invalid on server
            setSessionToken(null)
            setLocalSessionToken(null)
            setUser(null)
            setIsAuthenticated(false)
          }
        }
      } catch (err) {
        if (isMounted) {
          console.warn('Could not verify existing session:', err)
          setSessionToken(null)
          setLocalSessionToken(null)
          setUser(null)
          setIsAuthenticated(false)
        }
      } finally {
        if (isMounted) {
          setIsLoading(false)
        }
      }
    }

    initSession()
    return () => {
      isMounted = false
    }
  }, [])

  const login = useCallback(async (): Promise<{ configured: boolean; error?: string }> => {
    setIsAuthenticating(true)
    setAuthError(null)

    try {
      const redirectUri = `${window.location.origin}/auth/callback`
      const res = await getGitHubLoginUrl(redirectUri)

      if (!res.configured || !res.url) {
        setIsAuthenticating(false)
        return { configured: false, error: "GitHub sign-in isn't configured yet." }
      }

      // Real OAuth flow redirect
      window.location.href = res.url
      return { configured: true }
    } catch (err) {
      setIsAuthenticating(false)
      const msg = err instanceof Error ? err.message : 'Failed to initiate GitHub authorization.'
      setAuthError(msg)
      return { configured: false, error: msg }
    }
  }, [])

  const handleCallback = useCallback(async (code: string, state: string): Promise<string> => {
    setIsAuthenticating(true)
    setAuthError(null)

    try {
      const res = await handleOAuthCallback(code, state)
      // Securely save internal application session token (never GitHub access token)
      setSessionToken(res.session_token)
      setLocalSessionToken(res.session_token)
      setUser(res.user)
      setIsAuthenticated(true)
      setIsAuthenticating(false)
      return res.redirect_to || '/repositories/select'
    } catch (err) {
      setIsAuthenticating(false)
      const msg = err instanceof Error ? err.message : 'Authentication failed during callback exchange.'
      setAuthError(msg)
      throw err
    }
  }, [])

  const logout = useCallback(async (): Promise<void> => {
    try {
      await logoutUser()
    } catch {
      // Best-effort logout
    } finally {
      setSessionToken(null)
      setLocalSessionToken(null)
      setUser(null)
      setIsAuthenticated(false)
      setAuthError(null)
    }
  }, [])

  const clearAuthError = useCallback(() => {
    setAuthError(null)
  }, [])

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated,
        isLoading,
        isAuthenticating,
        authError,
        login,
        logout,
        handleCallback,
        sessionToken,
        clearAuthError,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}
