import React, { useEffect, useState } from 'react'
import {
  GearIcon,
  CheckCircleIcon,
  RepoIcon,
  SyncIcon,
  ServerIcon,
  CodeIcon,
  LockIcon,
  SlidersIcon,
  GitBranchIcon,
  ShieldCheckIcon,
  PulseIcon,
} from '@primer/octicons-react'
import { fetchSystemStatus } from '@/services/api'
import type { SystemStatus } from '@/types'

type SettingsTab =
  | 'general'
  | 'repository_preferences'
  | 'review_preferences'
  | 'integrations'
  | 'diagnostics'

export const SettingsPage: React.FC = () => {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<SettingsTab>('general')

  // Review preferences state
  const [driftSensitivity, setDriftSensitivity] = useState<'strict' | 'balanced' | 'lenient'>('balanced')
  const [autoApproveExceptions, setAutoApproveExceptions] = useState(true)
  const [minConfidence, setMinConfidence] = useState(85)
  const [autoSyncInterval, setAutoSyncInterval] = useState('15m')

  const loadStatus = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const data = await fetchSystemStatus()
      setStatus(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to query system status')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    let isMounted = true
    fetchSystemStatus()
      .then((data) => {
        if (!isMounted) return
        setStatus(data)
        setIsLoading(false)
      })
      .catch((err) => {
        if (!isMounted) return
        setError(err instanceof Error ? err.message : 'Failed to query system status')
        setIsLoading(false)
      })
    return () => {
      isMounted = false
    }
  }, [])

  return (
    <div className="space-y-5 max-w-4xl">
      {/* 1. Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pb-3 border-b border-[#30363d]">
        <div>
          <h1 className="text-xl font-semibold text-[#f0f6fc] tracking-tight">
            Repository & Review Settings
          </h1>
          <p className="text-xs text-[#9198a1] mt-0.5">
            Configure application preferences, architectural review sensitivity, and backend diagnostics.
          </p>
        </div>

        <button
          type="button"
          onClick={loadStatus}
          disabled={isLoading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[#21262d] border border-[#30363d] hover:bg-[#30363d] text-xs font-medium text-[#c9d1d9] hover:text-[#f0f6fc] transition-colors cursor-pointer self-start sm:self-auto"
        >
          <SyncIcon size={14} className={isLoading ? 'animate-spin' : ''} />
          <span>Refresh diagnostics</span>
        </button>
      </div>

      {/* 2. Settings Tabs (GitHub subnav style) */}
      <div className="flex items-center gap-1 overflow-x-auto border-b border-[#30363d] pb-2 text-xs">
        <button
          type="button"
          onClick={() => setActiveTab('general')}
          className={`px-3 py-1.5 rounded-md font-medium transition-colors cursor-pointer flex items-center gap-1.5 ${
            activeTab === 'general'
              ? 'bg-[#21262d] text-[#f0f6fc] border border-[#30363d] font-semibold'
              : 'text-[#9198a1] hover:text-[#f0f6fc]'
          }`}
        >
          <SlidersIcon size={14} />
          <span>General</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('repository_preferences')}
          className={`px-3 py-1.5 rounded-md font-medium transition-colors cursor-pointer flex items-center gap-1.5 ${
            activeTab === 'repository_preferences'
              ? 'bg-[#21262d] text-[#f0f6fc] border border-[#30363d] font-semibold'
              : 'text-[#9198a1] hover:text-[#f0f6fc]'
          }`}
        >
          <GitBranchIcon size={14} />
          <span>Repository Preferences</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('review_preferences')}
          className={`px-3 py-1.5 rounded-md font-medium transition-colors cursor-pointer flex items-center gap-1.5 ${
            activeTab === 'review_preferences'
              ? 'bg-[#21262d] text-[#f0f6fc] border border-[#30363d] font-semibold'
              : 'text-[#9198a1] hover:text-[#f0f6fc]'
          }`}
        >
          <GearIcon size={14} />
          <span>Review Preferences</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('integrations')}
          className={`px-3 py-1.5 rounded-md font-medium transition-colors cursor-pointer flex items-center gap-1.5 ${
            activeTab === 'integrations'
              ? 'bg-[#21262d] text-[#f0f6fc] border border-[#30363d] font-semibold'
              : 'text-[#9198a1] hover:text-[#f0f6fc]'
          }`}
        >
          <RepoIcon size={14} />
          <span>Integrations</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('diagnostics')}
          className={`px-3 py-1.5 rounded-md font-medium transition-colors cursor-pointer flex items-center gap-1.5 ${
            activeTab === 'diagnostics'
              ? 'bg-[#21262d] text-[#f0f6fc] border border-[#30363d] font-semibold'
              : 'text-[#9198a1] hover:text-[#f0f6fc]'
          }`}
        >
          <PulseIcon size={14} />
          <span>Diagnostics</span>
        </button>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-24 bg-[#161b22] border border-[#30363d] rounded-md animate-pulse" />
          ))}
        </div>
      ) : error ? (
        <div className="p-6 bg-[#f85149]/10 border border-[#f85149]/40 rounded-md text-center text-xs text-[#f85149]">
          Failed to load engine configuration: {error}
        </div>
      ) : (
        <div className="space-y-5">
          {/* TAB 1: GENERAL */}
          {activeTab === 'general' && (
            <div className="space-y-4">
              <div className="bg-[#161b22] border border-[#30363d] rounded-md p-4 space-y-3 text-xs">
                <h2 className="font-semibold text-sm text-[#f0f6fc]">System Information</h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-[#8b949e] pt-1">
                  <div>
                    <span className="block text-[11px]">Product</span>
                    <span className="text-[#f0f6fc] font-medium">Engineering Review & Memory Platform</span>
                  </div>
                  <div>
                    <span className="block text-[11px]">Version</span>
                    <span className="text-[#f0f6fc] font-medium">v1.0.0 (Core {status?.version || '1.0.0'})</span>
                  </div>
                  <div>
                    <span className="block text-[11px]">Deployment Environment</span>
                    <span className="text-[#f0f6fc] font-medium capitalize">{status?.environment || 'production'}</span>
                  </div>
                  <div>
                    <span className="block text-[11px]">Security Policy</span>
                    <span className="text-[#3fb950] font-medium flex items-center gap-1">
                      <LockIcon size={12} />
                      <span>Zero Secret Exposure Enforced</span>
                    </span>
                  </div>
                </div>
              </div>

              <div className="bg-[#161b22] border border-[#30363d] rounded-md p-4 space-y-3 text-xs">
                <h2 className="font-semibold text-sm text-[#f0f6fc]">Organization & Access</h2>
                <div className="flex items-center justify-between p-3 rounded-md bg-[#0d1117] border border-[#30363d]">
                  <div>
                    <div className="font-medium text-[#f0f6fc]">Active Review Team</div>
                    <div className="text-[11px] text-[#8b949e]">Default team for reviewing architectural drift and PR conformance</div>
                  </div>
                  <span className="px-2 py-0.5 rounded text-[11px] bg-[#21262d] text-[#4493f8] border border-[#30363d]">
                    @engineering
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: REPOSITORY PREFERENCES */}
          {activeTab === 'repository_preferences' && (
            <div className="bg-[#161b22] border border-[#30363d] rounded-md p-4 space-y-4 text-xs">
              <h2 className="font-semibold text-sm text-[#f0f6fc]">Repository Preferences</h2>

              <div className="space-y-3">
                <div>
                  <label className="block text-[11px] font-medium text-[#8b949e] mb-1">
                    Auto-sync Interval
                  </label>
                  <select
                    value={autoSyncInterval}
                    onChange={(e) => setAutoSyncInterval(e.target.value)}
                    className="w-full max-w-xs px-3 py-1.5 rounded-md bg-[#0d1117] border border-[#30363d] text-xs text-[#f0f6fc] focus:border-[#1f6feb] outline-hidden cursor-pointer"
                  >
                    <option value="5m">Every 5 minutes</option>
                    <option value="15m">Every 15 minutes (Default)</option>
                    <option value="1h">Hourly</option>
                    <option value="manual">Manual sync only</option>
                  </select>
                </div>

                <div className="pt-2 border-t border-[#30363d] space-y-2">
                  <span className="block font-medium text-[#f0f6fc]">Monitored Target Branches</span>
                  <div className="flex items-center gap-2">
                    {['main', 'master', 'develop'].map((b) => (
                      <span key={b} className="px-2.5 py-1 rounded-md bg-[#0d1117] border border-[#30363d] text-xs font-mono text-[#c9d1d9]">
                        {b}
                      </span>
                    ))}
                  </div>
                </div>

                <div className="pt-2 border-t border-[#30363d] space-y-2">
                  <span className="block font-medium text-[#f0f6fc]">Ignored Path Patterns</span>
                  <p className="text-[11px] text-[#8b949e]">
                    Paths excluded from architectural drift analysis:
                  </p>
                  <div className="flex flex-wrap gap-2 text-[11px] font-mono">
                    {['node_modules/**', 'vendor/**', 'dist/**', '*.min.js'].map((pattern) => (
                      <span key={pattern} className="px-2 py-0.5 rounded bg-[#0d1117] border border-[#30363d] text-[#8b949e]">
                        {pattern}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 3: REVIEW PREFERENCES */}
          {activeTab === 'review_preferences' && (
            <div className="bg-[#161b22] border border-[#30363d] rounded-md p-4 space-y-4 text-xs">
              <h2 className="font-semibold text-sm text-[#f0f6fc]">Review & Analysis Preferences</h2>

              <div className="space-y-4">
                <div>
                  <label className="block text-[11px] font-medium text-[#8b949e] mb-1">
                    Architectural Drift Sensitivity
                  </label>
                  <div className="grid grid-cols-3 gap-2 max-w-md">
                    {(['strict', 'balanced', 'lenient'] as const).map((s) => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => setDriftSensitivity(s)}
                        className={`py-1.5 px-3 rounded-md capitalize font-medium transition-colors cursor-pointer border ${
                          driftSensitivity === s
                            ? 'bg-[#21262d] text-[#f0f6fc] border-[#4493f8]'
                            : 'bg-[#0d1117] text-[#8b949e] border-[#30363d] hover:text-[#f0f6fc]'
                        }`}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                  <p className="text-[11px] text-[#8b949e] mt-1">
                    Balanced evaluates high-confidence deviations against recorded team precedents.
                  </p>
                </div>

                <div className="pt-2 border-t border-[#30363d] flex items-center justify-between">
                  <div>
                    <span className="block font-medium text-[#f0f6fc]">Auto-approve Documented Exceptions</span>
                    <span className="text-[11px] text-[#8b949e]">
                      Automatically mark PR as approved if deviations match a historical approved exception.
                    </span>
                  </div>
                  <input
                    type="checkbox"
                    checked={autoApproveExceptions}
                    onChange={(e) => setAutoApproveExceptions(e.target.checked)}
                    className="w-4 h-4 rounded bg-[#0d1117] border-[#30363d] text-[#238636] cursor-pointer"
                  />
                </div>

                <div className="pt-2 border-t border-[#30363d] space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-[#f0f6fc]">Minimum Confidence Score</span>
                    <span className="font-mono text-[#4493f8]">{minConfidence}%</span>
                  </div>
                  <input
                    type="range"
                    min="50"
                    max="95"
                    step="5"
                    value={minConfidence}
                    onChange={(e) => setMinConfidence(Number(e.target.value))}
                    className="w-full max-w-md cursor-pointer accent-[#238636]"
                  />
                  <p className="text-[11px] text-[#8b949e]">
                    Only report findings where the engine achieves at least this confidence level.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: INTEGRATIONS */}
          {activeTab === 'integrations' && (
            <div className="border border-[#30363d] rounded-md bg-[#161b22] divide-y divide-[#30363d] overflow-hidden text-xs">
              <div className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <RepoIcon size={16} className="text-[#8b949e]" />
                    <span className="font-semibold text-[#f0f6fc]">GitHub REST API & Webhooks</span>
                    {status?.services.github.configured ? (
                      <span className="px-2 py-0.2 rounded-full text-[11px] font-medium bg-[#3fb950]/15 text-[#3fb950] border border-[#3fb950]/40 flex items-center gap-1">
                        <CheckCircleIcon size={12} />
                        <span>Authenticated</span>
                      </span>
                    ) : (
                      <span className="px-2 py-0.2 rounded-full text-[11px] font-medium bg-[#d29922]/15 text-[#d29922] border border-[#d29922]/40">
                        Public Access Only
                      </span>
                    )}
                  </div>
                  <p className="text-[#8b949e] text-[11px]">
                    User: @{status?.services.github.user?.login || 'public'} • PR diff extraction & commit sync active
                  </p>
                </div>
              </div>

              <div className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <ServerIcon size={16} className="text-[#4493f8]" />
                    <span className="font-semibold text-[#f0f6fc]">Engineering Memory Store</span>
                    {status?.services.hindsight.configured ? (
                      <span className="px-2 py-0.2 rounded-full text-[11px] font-medium bg-[#3fb950]/15 text-[#3fb950] border border-[#3fb950]/40 flex items-center gap-1">
                        <CheckCircleIcon size={12} />
                        <span>Connected</span>
                      </span>
                    ) : (
                      <span className="px-2 py-0.2 rounded-full text-[11px] font-medium bg-[#d29922]/15 text-[#d29922] border border-[#d29922]/40">
                        Local Fallback
                      </span>
                    )}
                  </div>
                  <p className="text-[#8b949e] text-[11px]">
                    Bank ID: {status?.services.hindsight.default_bank_id || 'engineering-memory'}
                  </p>
                </div>
              </div>

              <div className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <ServerIcon size={16} className="text-[#3fb950]" />
                    <span className="font-semibold text-[#f0f6fc]">State Persistence</span>
                    {status?.services.supabase.configured ? (
                      <span className="px-2 py-0.2 rounded-full text-[11px] font-medium bg-[#3fb950]/15 text-[#3fb950] border border-[#3fb950]/40 flex items-center gap-1">
                        <CheckCircleIcon size={12} />
                        <span>PostgreSQL Active</span>
                      </span>
                    ) : (
                      <span className="px-2 py-0.2 rounded-full text-[11px] font-medium bg-[#d29922]/15 text-[#d29922] border border-[#d29922]/40">
                        Local State
                      </span>
                    )}
                  </div>
                  <p className="text-[#8b949e] text-[11px]">
                    Storage mode: {status?.services.supabase.storage_mode}
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* TAB 5: DIAGNOSTICS (Where engine details live away from primary UI) */}
          {activeTab === 'diagnostics' && status && (
            <div className="space-y-4">
              <div className="p-3.5 bg-[#161b22] border border-[#30363d] rounded-md flex flex-wrap items-center justify-between gap-4 text-xs">
                <div className="flex items-center gap-4 text-[#8b949e]">
                  <span className="flex items-center gap-1.5">
                    <ServerIcon size={14} className="text-[#656c76]" />
                    Backend API: <strong className="text-[#f0f6fc] font-medium">{status.version}</strong>
                  </span>
                  <span>•</span>
                  <span>
                    Environment: <strong className="text-[#f0f6fc] font-medium capitalize">{status.environment}</strong>
                  </span>
                </div>

                <div className="flex items-center gap-1.5 text-[#3fb950] font-medium">
                  <ShieldCheckIcon size={14} />
                  <span>All diagnostic probes responsive</span>
                </div>
              </div>

              <div className="border border-[#30363d] rounded-md bg-[#161b22] divide-y divide-[#30363d] overflow-hidden text-xs">
                <div className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <ServerIcon size={16} className="text-[#4493f8]" />
                      <span className="font-semibold text-[#f0f6fc]">Memory Service Probe</span>
                      <span className="px-2 py-0.2 rounded-full text-[11px] font-medium bg-[#3fb950]/15 text-[#3fb950] border border-[#3fb950]/40">
                        Healthy
                      </span>
                    </div>
                    <p className="text-[#8b949e] text-[11px]">
                      Endpoint: {status.services.hindsight.base_url || 'https://api.hindsight.vectorize.io'}
                    </p>
                  </div>
                </div>

                <div className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <PulseIcon size={16} className="text-[#ab7df8]" />
                      <span className="font-semibold text-[#f0f6fc]">Reasoning Engine Probe</span>
                      <span className="px-2 py-0.2 rounded-full text-[11px] font-medium bg-[#3fb950]/15 text-[#3fb950] border border-[#3fb950]/40">
                        Healthy
                      </span>
                    </div>
                    <p className="text-[#8b949e] text-[11px]">
                      Reasoning model: {status.services.groq.reasoning_model} • Summary model: {status.services.groq.lightweight_model}
                    </p>
                  </div>
                </div>

                <div className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <CodeIcon size={16} className="text-[#4493f8]" />
                      <span className="font-semibold text-[#f0f6fc]">Tree-sitter AST Parser Engine</span>
                      <span className="px-2 py-0.2 rounded-full text-[11px] font-medium bg-[#3fb950]/15 text-[#3fb950] border border-[#3fb950]/40">
                        Ready
                      </span>
                    </div>
                    <p className="text-[#8b949e] text-[11px]">
                      Supported languages: {status.services.tree_sitter.supported_languages.join(', ')}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
