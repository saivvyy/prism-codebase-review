import React, { useEffect, useState, useRef } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import {
  MarkGithubIcon,
  SyncIcon,
  XCircleIcon,
  AlertIcon,
  ArrowLeftIcon,
  CheckCircleIcon,
} from '@primer/octicons-react'
import { useAuth } from '@/context/useAuth'
import { PrismLogo } from '@/components/PrismLogo'

export const OAuthCallbackPage: React.FC = () => {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { handleCallback } = useAuth()

  const code = searchParams.get('code')
  const state = searchParams.get('state')
  const oauthError = searchParams.get('error')
  const oauthErrorDesc = searchParams.get('error_description')

  // Derive initial status and error directly from query params
  const initialStatus: 'exchanging' | 'success' | 'cancelled' | 'error' = oauthError
    ? oauthError === 'access_denied'
      ? 'cancelled'
      : 'error'
    : !code || !state
      ? 'error'
      : 'exchanging'

  const initialErrorMessage: string = oauthError
    ? oauthError === 'access_denied'
      ? (oauthErrorDesc || 'GitHub sign-in was cancelled. You chose not to grant PRism access to your GitHub account.')
      : (oauthErrorDesc ? `${oauthError}: ${oauthErrorDesc}` : `GitHub authorization error: ${oauthError}`)
    : !code || !state
      ? 'Missing required OAuth parameters (code or state). Please try signing in again.'
      : ''

  const [statusState, setStatusState] = useState<'exchanging' | 'success' | 'cancelled' | 'error'>(initialStatus)
  const [errorMessage, setErrorMessage] = useState<string>(initialErrorMessage)
  const exchangeStarted = useRef<boolean>(false)

  useEffect(() => {
    // If there is already an error or user cancelled, do not attempt token exchange
    if (oauthError || !code || !state) {
      return
    }

    // Prevent duplicate execution in React StrictMode
    if (exchangeStarted.current) return
    exchangeStarted.current = true

    // Exchange code for session
    const executeCallback = async () => {
      try {
        const destination = await handleCallback(code, state)
        setStatusState('success')
        // Mark explicitly as an active post-OAuth onboarding session and clear any prior workspace location
        try {
          sessionStorage.removeItem('prism_last_workspace_location')
          sessionStorage.setItem('prism_oauth_onboarding_active', 'true')
        } catch {
          // Ignore storage restrictions if in private browsing
        }
        const separator = destination.includes('?') ? '&' : '?'
        // Brief smooth transition before redirecting to repository selection
        setTimeout(() => {
          navigate(`${destination}${separator}onboarding=true`, {
            replace: true,
            state: { fromOAuthOnboarding: true },
          })
        }, 600)
      } catch (err) {
        setStatusState('error')
        const msg = err instanceof Error ? err.message : 'Failed to establish authenticated session.'
        setErrorMessage(msg)
      }
    }

    executeCallback()
  }, [code, state, oauthError, handleCallback, navigate])

  return (
    <div className="min-h-screen bg-[#010409] text-[#f0f6fc] flex flex-col items-center justify-center p-4 selection:bg-[#1f6feb]/30 selection:text-[#58a6ff]">
      {/* Background ambient gradient */}
      <div className="absolute inset-0 pointer-events-none -z-10 flex items-center justify-center">
        <div className="w-[600px] h-[350px] bg-[radial-gradient(ellipse_at_center,rgba(31,111,235,0.08)_0%,rgba(1,4,9,0)_70%)]" />
      </div>

      <div className="w-full max-w-md bg-[#161b22] border border-[#30363d] rounded-xl shadow-2xl p-6 sm:p-8 text-center space-y-6">
        {/* Brand header */}
        <div className="flex items-center justify-center gap-3">
          <PrismLogo size="xl" />
          <div className="text-left">
            <div className="font-semibold text-sm text-[#f0f6fc] tracking-tight">PRism</div>
            <div className="text-[11px] text-[#8b949e]">GitHub Authentication</div>
          </div>
        </div>

        {/* State 1: Exchanging */}
        {statusState === 'exchanging' && (
          <div className="space-y-4 py-4">
            <div className="w-12 h-12 rounded-full bg-[#21262d] border border-[#30363d] flex items-center justify-center mx-auto text-[#58a6ff]">
              <SyncIcon size={24} className="animate-spin" />
            </div>
            <div className="space-y-1">
              <h2 className="text-base font-semibold text-[#f0f6fc]">Connecting to GitHub…</h2>
              <p className="text-xs text-[#8b949e] max-w-xs mx-auto">
                Validating authorization code and establishing a secure PRism session.
              </p>
            </div>
          </div>
        )}

        {/* State 2: Success */}
        {statusState === 'success' && (
          <div className="space-y-4 py-4">
            <div className="w-12 h-12 rounded-full bg-[#238636]/10 border border-[#238636]/30 flex items-center justify-center mx-auto text-[#3fb950]">
              <CheckCircleIcon size={24} />
            </div>
            <div className="space-y-1">
              <h2 className="text-base font-semibold text-[#f0f6fc]">Authenticated</h2>
              <p className="text-xs text-[#8b949e]">
                Redirecting to repository selection…
              </p>
            </div>
          </div>
        )}

        {/* State 3: User Cancelled */}
        {statusState === 'cancelled' && (
          <div className="space-y-4 py-2">
            <div className="w-12 h-12 rounded-full bg-[#f85149]/10 border border-[#f85149]/30 flex items-center justify-center mx-auto text-[#d29922]">
              <AlertIcon size={24} />
            </div>
            <div className="space-y-1.5">
              <h2 className="text-base font-semibold text-[#f0f6fc]">Sign-in Cancelled</h2>
              <p className="text-xs text-[#8b949e] leading-relaxed">
                {errorMessage}
              </p>
            </div>
            <div className="pt-2">
              <Link
                to="/"
                className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-md bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] text-xs font-semibold text-[#c9d1d9] hover:text-[#f0f6fc] transition-colors"
              >
                <ArrowLeftIcon size={14} />
                <span>Return to Landing Page</span>
              </Link>
            </div>
          </div>
        )}

        {/* State 4: Error */}
        {statusState === 'error' && (
          <div className="space-y-4 py-2">
            <div className="w-12 h-12 rounded-full bg-[#f85149]/10 border border-[#f85149]/30 flex items-center justify-center mx-auto text-[#f85149]">
              <XCircleIcon size={24} />
            </div>
            <div className="space-y-1.5">
              <h2 className="text-base font-semibold text-[#f0f6fc]">Authentication Failed</h2>
              <p className="text-xs text-[#f85149] bg-[#f85149]/10 border border-[#f85149]/20 rounded-md p-2.5 leading-relaxed break-words font-mono text-[11px]">
                {errorMessage}
              </p>
            </div>
            <div className="pt-2 flex items-center justify-center gap-3">
              <Link
                to="/"
                className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-md bg-[#238636] hover:bg-[#2ea043] border border-[rgba(240,246,252,0.15)] text-xs font-semibold text-white transition-all shadow-xs"
              >
                <MarkGithubIcon size={14} />
                <span>Try Again</span>
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
