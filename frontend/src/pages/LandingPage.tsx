import React, { useState, useEffect, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  MarkGithubIcon,
  GitPullRequestIcon,
  CheckCircleIcon,
  XCircleIcon,
  ShieldCheckIcon,
  ClockIcon,
  ArrowRightIcon,
  SyncIcon,
  CommentDiscussionIcon,
  ChevronRightIcon,
  CheckIcon,
  HistoryIcon,
  AlertIcon,
  XIcon,
} from '@primer/octicons-react'
import { useAuth } from '@/context/useAuth'
import { PrismLogo } from '@/components/PrismLogo'

// Memory category item type for interactive explorer
interface MemoryCategoryItem {
  id: string
  title: string
  subtitle: string
  precedentPr: string
  claim: string
  rationale: string
  impact: string
}

const MEMORY_CATEGORIES: Record<string, { label: string; description: string; items: MemoryCategoryItem[] }> = {
  architecture: {
    label: 'Architecture decisions',
    description: 'Structural boundaries, service contracts, and layer separation rules established across past reviews.',
    items: [
      {
        id: 'arch-1',
        title: 'Domain Repository Isolation',
        subtitle: 'Presentation controllers must not query persistence directly',
        precedentPr: 'Precedent: Architecture Decision',
        claim: 'Controllers must route all read and write queries through Domain Repositories rather than raw ORM client instances.',
        rationale: 'Direct persistence calls in web controllers bypass tenancy filters, read-replica routing, and audit logs.',
        impact: 'Prevents database coupling and ensures transaction boundaries remain consistent.',
      },
      {
        id: 'arch-2',
        title: 'Asynchronous Event Ingestion',
        subtitle: 'Third-party webhook handlers must acknowledge before processing',
        precedentPr: 'Precedent: Ingress Architecture',
        claim: 'Inbound webhooks must push payloads to the durable stream and return 202 Accepted within 150ms.',
        rationale: 'Synchronous downstream processing caused webhook provider timeouts during burst traffic.',
        impact: 'Maintains 99.99% ingress availability regardless of downstream system load.',
      },
    ],
  },
  rejected: {
    label: 'Rejected patterns',
    description: 'Anti-patterns and flawed approaches previously discussed, flagged, and deliberately dismissed by the team.',
    items: [
      {
        id: 'rej-1',
        title: 'Untyped JSON Mutation Bags',
        subtitle: 'Reject untyped Record<string, any> in domain command handlers',
        precedentPr: 'Precedent: Rejected Anti-pattern',
        claim: 'Domain command objects must be explicitly typed and schema-validated before dispatch.',
        rationale: 'Permissive bag passing masked breaking payload changes across microservice boundaries.',
        impact: 'Eliminates silent data corruption at runtime across service updates.',
      },
      {
        id: 'rej-2',
        title: 'Client-Side Token Decryption',
        subtitle: 'Session credentials must remain server-side httpOnly',
        precedentPr: 'Precedent: Security Decision',
        claim: 'Authentication claims must not be unpacked or decoded in client-accessible memory.',
        rationale: 'Exposing decrypted session tokens violated the repository threat model established in security audit.',
        impact: 'Hardens workspace sessions against cross-site script exfiltration.',
      },
    ],
  },
  exceptions: {
    label: 'Approved exceptions',
    description: 'Deliberate, sanctioned exceptions to standard rules with documented scope and reasoning.',
    items: [
      {
        id: 'exc-1',
        title: 'Diagnostics Direct Ping Exception',
        subtitle: 'Health and liveness probes permitted to bypass repository abstraction',
        precedentPr: 'Precedent: Approved Exception',
        claim: 'HealthCheckController and diagnostics routes may execute lightweight raw database ping queries directly.',
        rationale: 'Liveness probes must assess physical database socket health independently of domain caching layers.',
        impact: 'Ensures orchestrator health checks accurately signal container health without false positives.',
      },
    ],
  },
  decisions: {
    label: 'Review decisions',
    description: 'Consensus reached during thorough code reviews that establish repository precedent.',
    items: [
      {
        id: 'dec-1',
        title: 'Mandatory Idempotency Keys on Payment Mutations',
        subtitle: 'All billing mutation endpoints require unique client request tokens',
        precedentPr: 'Precedent: Consensus Decision',
        claim: 'Payment gateway calls and balance adjustments must enforce idempotency keys with 24-hour cache deduplication.',
        rationale: 'Network drop retries previously caused duplicate charges during checkout flow degradations.',
        impact: 'Protects customers against double billing during connectivity flickers.',
      },
    ],
  },
  lessons: {
    label: 'Lessons learned',
    description: 'Post-incident takeaways and engineering learnings codified into review guardrails.',
    items: [
      {
        id: 'les-1',
        title: 'Explicit Connection Pool Leases',
        subtitle: 'Always wrap multi-step transactions in scoped try/finally release blocks',
        precedentPr: 'Precedent: Post-incident Takeaway',
        claim: 'Raw connection pool checkouts must be explicitly released in finally blocks with 5-second lease timeouts.',
        rationale: 'Unhandled promise rejections caused connection starvation and cascaded pod restarts in production.',
        impact: 'Prevents silent socket exhaustion under high concurrency.',
      },
    ],
  },
}

export const LandingPage: React.FC = () => {
  const { isAuthenticated, isAuthenticating, login, authError, clearAuthError } = useAuth()
  const navigate = useNavigate()
  const [selectedCategory, setSelectedCategory] = useState<string>('architecture')
  const [selectedScenario, setSelectedScenario] = useState<'drift' | 'exception'>('drift')
  const [showAuthConfigNotice, setShowAuthConfigNotice] = useState<boolean>(false)
  const [isConnecting, setIsConnecting] = useState<boolean>(false)
  const previewRef = useRef<HTMLDivElement>(null)
  const [previewTransform, setPreviewTransform] = useState<string>('')

  const handlePreviewMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (typeof window === 'undefined') return
    if (!window.matchMedia('(pointer: fine)').matches) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const el = previewRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top
    const centerX = rect.width / 2
    const centerY = rect.height / 2
    // Very subtle tilt: maximum 1.5 to 2 degrees
    const rotateX = ((y - centerY) / centerY) * -1.8
    const rotateY = ((x - centerX) / centerX) * 1.8
    setPreviewTransform(`perspective(1000px) rotateX(${rotateX.toFixed(2)}deg) rotateY(${rotateY.toFixed(2)}deg) translate3d(0, -2px, 0)`)
  }

  const handlePreviewMouseLeave = () => {
    setPreviewTransform('')
  }

  // Prevent authenticated users from unnecessarily seeing landing again
  useEffect(() => {
    if (isAuthenticated) {
      navigate('/workspace', { replace: true })
    }
  }, [isAuthenticated, navigate])

  // Subtle IntersectionObserver for gentle section scroll reveals
  useEffect(() => {
    if (typeof window === 'undefined' || typeof IntersectionObserver === 'undefined') return
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (prefersReducedMotion) return

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-revealed')
            observer.unobserve(entry.target)
          }
        })
      },
      { threshold: 0.12, rootMargin: '0px 0px -40px 0px' }
    )

    const sections = document.querySelectorAll('.prism-reveal-section')
    sections.forEach((sec) => observer.observe(sec))

    return () => observer.disconnect()
  }, [])


  // Real GitHub OAuth entry: checks environment configuration without faking auth
  const handleContinueWithGitHub = async () => {
    if (isAuthenticated) {
      navigate('/workspace')
      return
    }

    setIsConnecting(true)
    clearAuthError()

    try {
      const res = await login()
      if (!res.configured) {
        // Do NOT navigate to /workspace or pretend the user is authenticated.
        // Instead, show a clear, development-safe state explaining OAuth setup.
        setShowAuthConfigNotice(true)
      }
    } catch {
      // Error handled by AuthContext
    } finally {
      setIsConnecting(false)
    }
  }

  const isAuthPending = isConnecting || isAuthenticating
  const activeCategoryData = MEMORY_CATEGORIES[selectedCategory] || MEMORY_CATEGORIES.architecture

  return (
    <div className="landing-page min-h-screen bg-[#010409] text-[#f0f6fc] font-sans selection:bg-[#1f6feb]/30 selection:text-[#58a6ff]">
      {/* =====================================================================
          1. MINIMAL TOP NAVIGATION
          ===================================================================== */}
      <header className="sticky top-0 z-50 backdrop-blur-md bg-[#010409]/85 border-b border-[#21262d] transition-colors">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
          {/* Brand Identity */}
          <div className="flex items-center gap-3">
            <Link to="/" className="flex items-center gap-2.5 group cursor-pointer focus:outline-none">
              <div className="prism-logo-glow-fx transition-transform duration-300 group-hover:scale-105">
                <PrismLogo size="lg" />
              </div>
              <span className="font-semibold text-base tracking-tight text-[#f0f6fc] group-hover:text-white transition-colors">
                PRism
              </span>
            </Link>
            <span className="hidden sm:inline-block px-2 py-0.5 rounded-full text-[11px] font-mono text-[#8b949e] border border-[#30363d] bg-[#0d1117]">
              Code review that remembers
            </span>
          </div>

          {/* Minimal Navigation Links */}
          <nav className="flex items-center gap-6 text-xs font-medium text-[#8b949e]">
            <a
              href="#product"
              className="prism-nav-link hidden md:inline-block cursor-pointer"
            >
              Product
            </a>
            <a
              href="#problem"
              className="prism-nav-link hidden md:inline-block cursor-pointer"
            >
              The Problem
            </a>
            <a
              href="#memory"
              className="prism-nav-link hidden md:inline-block cursor-pointer"
            >
              Engineering Memory
            </a>
            <a
              href="#how-it-works"
              className="prism-nav-link hidden sm:inline-block cursor-pointer"
            >
              How it works
            </a>
          </nav>

          {/* Auth Action */}
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={handleContinueWithGitHub}
              disabled={isAuthPending}
              className="prism-primary-cta inline-flex items-center gap-2 px-3.5 py-1.5 rounded-md bg-[#238636] hover:bg-[#2ea043] border border-[rgba(240,246,252,0.15)] text-white text-xs font-semibold shadow-xs transition-all cursor-pointer focus:outline-none focus:ring-2 focus:ring-[#3fb950] disabled:opacity-75 disabled:cursor-not-allowed"
            >
              {isAuthPending ? (
                <>
                  <SyncIcon size={14} className="animate-spin" />
                  <span>Connecting to GitHub…</span>
                </>
              ) : (
                <>
                  <MarkGithubIcon size={14} />
                  <span>Continue with GitHub</span>
                </>
              )}
            </button>
          </div>
        </div>
      </header>

      <main>
        {/* =====================================================================
            2. HERO SECTION
            ===================================================================== */}
        <section className="relative pt-16 sm:pt-24 pb-16 sm:pb-24 overflow-hidden border-b border-[#21262d]">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <div className="max-w-3xl mx-auto text-center space-y-6">
              {/* Product Badge */}
              <div className="prism-badge prism-entrance-1 inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-medium bg-[#161b22] border border-[#30363d] text-[#c9d1d9] shadow-xs cursor-default">
                <span className="prism-badge-dot w-2 h-2 rounded-full bg-[#3fb950] animate-pulse" />
                <span>Preserve Engineering Reasoning Across Pull Requests</span>
              </div>

              {/* Primary Value Proposition Headline - Solid White, No Gradient */}
              <h1 className="prism-hero-headline prism-headline-entrance text-4xl sm:text-6xl font-extrabold tracking-tight leading-[1.1] sm:leading-[1.1]">
                <span className="block text-[#f0f6fc]">
                  CODE REVIEW
                </span>
                <span className="prism-headline-glow block text-[#f0f6fc]">
                  THAT REMEMBERS.
                </span>
              </h1>

              {/* Supporting Copy */}
              <p className="prism-entrance-3 text-base sm:text-lg text-[#9198a1] max-w-2xl mx-auto leading-relaxed">
                PRism remembers the engineering decisions behind your codebase and brings that context directly into future pull requests.
              </p>

              {/* Error notice if authentication failed */}
              {authError && (
                <div className="max-w-md mx-auto p-3 rounded-md bg-[#f85149]/10 border border-[#f85149]/30 text-xs text-[#f85149] flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <AlertIcon size={14} className="shrink-0" />
                    <span>{authError}</span>
                  </div>
                  <button
                    type="button"
                    onClick={clearAuthError}
                    className="p-1 hover:bg-[#f85149]/20 rounded text-[#f85149] cursor-pointer"
                  >
                    <XIcon size={12} />
                  </button>
                </div>
              )}

              {/* Dual CTAs */}
              <div className="prism-entrance-4 flex flex-col sm:flex-row items-center justify-center gap-3 pt-2">
                <button
                  type="button"
                  onClick={handleContinueWithGitHub}
                  disabled={isAuthPending}
                  className="prism-primary-cta group w-full sm:w-auto inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-md bg-[#238636] hover:bg-[#2ea043] border border-[rgba(240,246,252,0.15)] text-white text-sm font-semibold shadow-md transition-all cursor-pointer focus:outline-none focus:ring-2 focus:ring-[#3fb950] disabled:opacity-75 disabled:cursor-not-allowed"
                >
                  {isAuthPending ? (
                    <>
                      <SyncIcon size={16} className="animate-spin" />
                      <span>Connecting to GitHub…</span>
                    </>
                  ) : (
                    <>
                      <MarkGithubIcon size={16} />
                      <span>Continue with GitHub</span>
                      <ArrowRightIcon size={14} className="ml-0.5 prism-cta-arrow" />
                    </>
                  )}
                </button>

                <a
                  href="#how-it-works"
                  className="prism-secondary-cta w-full sm:w-auto inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-md bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] text-sm font-medium text-[#c9d1d9] hover:text-[#f0f6fc] transition-all cursor-pointer"
                >
                  <span>See how it works</span>
                </a>
              </div>

              {/* Subtle visual bridge: Past Decisions -> Future Code Review */}
              <div className="prism-entrance-5 prism-bridge-row pt-6 flex items-center justify-center gap-3 text-xs text-[#8b949e] group cursor-default">
                <span className="flex items-center gap-1.5 font-medium text-[#c9d1d9] group-hover:text-[#f0f6fc] transition-colors duration-200">
                  <HistoryIcon size={14} className="text-[#58a6ff] group-hover:brightness-125 transition-all duration-200" />
                  <span>Past engineering decisions</span>
                </span>
                <span className="text-[#30363d] group-hover:text-[#8b949e] group-hover:translate-x-0.5 transition-all duration-200">→</span>
                <span className="flex items-center gap-1.5 font-medium text-[#c9d1d9] group-hover:text-[#f0f6fc] transition-colors duration-200">
                  <GitPullRequestIcon size={14} className="text-[#3fb950] group-hover:brightness-125 transition-all duration-200" />
                  <span>Future code review</span>
                </span>
              </div>
            </div>

            {/* =====================================================================
                HERO PRODUCT VISUALIZATION: Authentic PRism Review UI
                ===================================================================== */}
            <div id="product" className="mt-12 sm:mt-16 max-w-4xl mx-auto relative">
              {/* Subtle ambient backlight behind preview */}
              <div
                className="absolute inset-0 -m-4 sm:-m-6 rounded-2xl pointer-events-none blur-2xl opacity-40 -z-10"
                style={{
                  background: 'radial-gradient(ellipse at center, rgba(68,147,248,0.18) 0%, rgba(171,125,248,0.10) 45%, rgba(1,4,9,0) 75%)',
                }}
              />
              <div
                ref={previewRef}
                onMouseMove={handlePreviewMouseMove}
                onMouseLeave={handlePreviewMouseLeave}
                style={previewTransform ? { transform: previewTransform } : undefined}
                className="prism-preview-frame rounded-lg border border-[#30363d] bg-[#0d1117] shadow-2xl overflow-hidden"
              >
                {/* Visual Header / Tab Switcher */}
                <div className="bg-[#161b22] px-4 py-3 border-b border-[#30363d] flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-full bg-[#f85149]/40 border border-[#f85149]" />
                    <span className="w-3 h-3 rounded-full bg-[#d29922]/40 border border-[#d29922]" />
                    <span className="w-3 h-3 rounded-full bg-[#3fb950]/40 border border-[#3fb950]" />
                    <span className="ml-2 font-mono text-xs text-[#8b949e]">
                      Illustrative Preview • PRism Review Interface Demonstration
                    </span>
                  </div>

                  {/* Interactive Scenario Toggle */}
                  <div className="flex items-center rounded-md bg-[#0d1117] p-0.5 border border-[#30363d] text-[11px]">
                    <button
                      type="button"
                      onClick={() => setSelectedScenario('drift')}
                      className={`px-2.5 py-1 rounded font-medium transition-colors cursor-pointer ${selectedScenario === 'drift'
                        ? 'bg-[#21262d] text-[#f85149] font-semibold shadow-xs'
                        : 'text-[#8b949e] hover:text-[#f0f6fc]'
                        }`}
                    >
                      Scenario 1: Drift Detected
                    </button>
                    <button
                      type="button"
                      onClick={() => setSelectedScenario('exception')}
                      className={`px-2.5 py-1 rounded font-medium transition-colors cursor-pointer ${selectedScenario === 'exception'
                        ? 'bg-[#21262d] text-[#3fb950] font-semibold shadow-xs'
                        : 'text-[#8b949e] hover:text-[#f0f6fc]'
                        }`}
                    >
                      Scenario 2: Approved Exception
                    </button>
                  </div>
                </div>

                {/* Simulated PR Context Banner */}
                <div className="px-5 py-4 border-b border-[#21262d] bg-[#0d1117] flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2">
                      <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-[#238636] text-white">
                        Open
                      </span>
                      <h2 className="font-semibold text-sm text-[#f0f6fc]">
                        {selectedScenario === 'drift'
                          ? 'refactor(gateway): direct persistence query in controller (Example PR)'
                          : 'feat(diagnostics): deep database liveness probe (Example PR)'}
                      </h2>
                    </div>
                    <div className="text-xs text-[#8b949e]">
                      <strong>@alex-chen</strong> wants to merge into <code className="text-[#58a6ff]">main</code> from <code className="text-[#58a6ff]">gateway-optimization</code>
                    </div>
                  </div>

                  <div className="shrink-0 flex items-center gap-2">
                    <span className="px-2 py-1 rounded bg-[#161b22] border border-[#30363d] text-[11px] font-mono text-[#8b949e]">
                      src/controllers/{selectedScenario === 'drift' ? 'OrderGatewayController.ts' : 'HealthCheckController.ts'}
                    </span>
                  </div>
                </div>

                {/* Diff Viewer Representation */}
                <div className="bg-[#010409] px-4 py-3 border-b border-[#21262d] text-[11px] font-mono overflow-x-auto">
                  <div className="text-[#8b949e] pb-1 select-none">@@ -18,6 +18,10 @@</div>
                  {selectedScenario === 'drift' ? (
                    <div className="space-y-0.5">
                      <div className="text-[#8b949e]">&nbsp;&nbsp;export class OrderGatewayController &#123;</div>
                      <div className="bg-[#238636]/15 text-[#7ee787] px-2 py-0.5 rounded">
                        +&nbsp;&nbsp;&nbsp;async getOrders(userId: string) &#123;
                      </div>
                      <div className="bg-[#238636]/15 text-[#7ee787] px-2 py-0.5 rounded">
                        +&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return await this.db.orders.findMany(&#123; where: &#123; userId &#125; &#125;);
                      </div>
                      <div className="bg-[#238636]/15 text-[#7ee787] px-2 py-0.5 rounded">
                        +&nbsp;&nbsp;&nbsp;&#125;
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-0.5">
                      <div className="text-[#8b949e]">&nbsp;&nbsp;export class HealthCheckController &#123;</div>
                      <div className="bg-[#238636]/15 text-[#7ee787] px-2 py-0.5 rounded">
                        +&nbsp;&nbsp;&nbsp;async checkReadiness() &#123;
                      </div>
                      <div className="bg-[#238636]/15 text-[#7ee787] px-2 py-0.5 rounded">
                        +&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return await this.db.$queryRaw`SELECT 1`; // Direct socket ping
                      </div>
                      <div className="bg-[#238636]/15 text-[#7ee787] px-2 py-0.5 rounded">
                        +&nbsp;&nbsp;&nbsp;&#125;
                      </div>
                    </div>
                  )}
                </div>

                {/* PRism Review Evaluation Box (Primer Review Card) */}
                <div className="p-5 space-y-4 bg-[#0d1117]">
                  {/* Status Banner */}
                  <div
                    className={`p-3.5 rounded-md border flex items-start justify-between gap-3 ${selectedScenario === 'drift'
                      ? 'bg-[#f85149]/10 border-[#f85149]/30 text-[#f85149]'
                      : 'bg-[#3fb950]/10 border-[#3fb950]/30 text-[#3fb950]'
                      }`}
                  >
                    <div className="flex items-start gap-2.5">
                      {selectedScenario === 'drift' ? (
                        <XCircleIcon size={18} className="mt-0.5 shrink-0" />
                      ) : (
                        <CheckCircleIcon size={18} className="mt-0.5 shrink-0" />
                      )}
                      <div>
                        <div className="font-semibold text-xs text-[#f0f6fc]">
                          {selectedScenario === 'drift'
                            ? 'Architecture Drift Detected — Review Required'
                            : 'Architectural Conformance Verified — Approved Exception'}
                        </div>
                        <p className="text-xs text-[#c9d1d9] mt-0.5">
                          {selectedScenario === 'drift'
                            ? 'Incoming change violates presentation-layer isolation established in prior repository decisions.'
                            : 'This change adheres to documented repository boundaries with a verified exception.'}
                        </p>
                      </div>
                    </div>

                    <span className="text-[10px] px-2 py-0.5 rounded-full font-mono bg-[#161b22] border border-[#30363d] text-[#8b949e] shrink-0">
                      1 finding evaluated
                    </span>
                  </div>

                  {/* Historical Context Card (The Core Differentiator) */}
                  <div className="rounded-md border border-[#1f6feb]/40 bg-[#010409] overflow-hidden text-xs">
                    <div className="px-3.5 py-2.5 bg-[#161b22] border-b border-[#30363d] flex items-center justify-between">
                      <div className="flex items-center gap-2 text-[#58a6ff] font-semibold">
                        <ShieldCheckIcon size={15} />
                        <span>Historical Context</span>
                        <span className="px-2 py-0.2 rounded-full text-[10px] bg-[#238636]/20 border border-[#238636]/40 text-[#3fb950]">
                          Illustrative precedent: presentation-layer boundary
                        </span>
                      </div>

                      <span className="text-[11px] text-[#8b949e]">
                        Documented precedent
                      </span>
                    </div>

                    <div className="p-4 space-y-2.5 text-xs">
                      <div>
                        <span className="font-semibold text-[#c9d1d9]">Decision: </span>
                        <span className="text-[#f0f6fc]">
                          {selectedScenario === 'drift'
                            ? 'Decouple presentation controllers from data persistence via Domain Repositories'
                            : 'Permit direct socket query pinging for HealthCheckController and diagnostics'}
                        </span>
                      </div>

                      <div>
                        <span className="font-semibold text-[#8b949e]">Rationale: </span>
                        <span className="text-[#c9d1d9]">
                          {selectedScenario === 'drift'
                            ? 'Direct ORM calls in controllers bypass multi-tenant scoping and cache invalidation protocols.'
                            : 'Container orchestrator liveness checks must verify raw database socket responsiveness independently of caching middleware.'}
                        </span>
                      </div>

                      <div className="flex items-center gap-2 pt-1 border-t border-[#21262d] text-[11px]">
                        <span className="font-semibold text-[#8b949e]">Precedent source:</span>
                        <span className="px-2 py-0.5 rounded bg-[#21262d] border border-[#30363d] text-[#58a6ff] font-mono">
                          Documented engineering decision (Illustrative)
                        </span>
                        <span className="text-[#656c76]">•</span>
                        <span className="text-[#8b949e]">
                          {selectedScenario === 'drift'
                            ? 'Action: Route call through OrderRepository'
                            : 'Status: Exception active and verified'}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Illustrative Demonstration Disclaimer */}
                  <div className="px-3 py-2 rounded bg-[#161b22]/60 border border-[#21262d] text-[10px] text-[#8b949e] text-center">
                    * Illustrative demonstration only. PRism never fabricates PR data; in live operation, reviews reference your repository's verifiable pull requests.
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* =====================================================================
            3. SECTION: THE PROBLEM
            ===================================================================== */}
        <section id="problem" className="prism-reveal-section py-20 lg:py-24 border-b border-[#21262d] bg-[#0d1117]">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <div className="max-w-2xl mx-auto text-center space-y-3 mb-16">
              <span className="text-xs font-semibold text-[#f78166] uppercase tracking-wider">
                The Continuity Breakdown
              </span>
              <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-[#f0f6fc]">
                Your codebase remembers.
                <br />
                Your team doesn't always.
              </h2>
              <p className="text-sm sm:text-base text-[#8b949e] leading-relaxed">
                Every team makes critical architectural choices in pull request reviews. But as time passes, the reasons behind those choices evaporate.
              </p>
            </div>

            {/* Problem Pillars (3 Columns) */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {/* Problem 1 */}
              <div className="prism-card-interactive p-6 rounded-lg border border-[#30363d] bg-[#010409] space-y-3 flex flex-col justify-between">
                <div className="space-y-3">
                  <div className="w-9 h-9 rounded-md bg-[#21262d] border border-[#30363d] flex items-center justify-center text-[#f85149]">
                    <ClockIcon size={18} />
                  </div>
                  <h3 className="text-base font-semibold text-[#f0f6fc]">
                    Decisions disappear
                  </h3>
                  <p className="text-xs text-[#8b949e] leading-relaxed">
                    Engineering decisions vanish into closed PRs, merged diff comments, and undocumented review threads. Months later, nobody remembers why a boundary was created.
                  </p>
                </div>
                <div className="pt-4 border-t border-[#21262d] text-[11px] text-[#f85149] font-medium">
                  Context lost to git history
                </div>
              </div>

              {/* Problem 2 */}
              <div className="prism-card-interactive p-6 rounded-lg border border-[#30363d] bg-[#010409] space-y-3 flex flex-col justify-between">
                <div className="space-y-3">
                  <div className="w-9 h-9 rounded-md bg-[#21262d] border border-[#30363d] flex items-center justify-center text-[#d29922]">
                    <CommentDiscussionIcon size={18} />
                  </div>
                  <h3 className="text-base font-semibold text-[#f0f6fc]">
                    Debates repeat
                  </h3>
                  <p className="text-xs text-[#8b949e] leading-relaxed">
                    New engineers join and re-propose previously debated patterns. Teams burn hours rehashing arguments that were already resolved in past pull requests.
                  </p>
                </div>
                <div className="pt-4 border-t border-[#21262d] text-[11px] text-[#d29922] font-medium">
                  Engineering hours wasted
                </div>
              </div>

              {/* Problem 3 */}
              <div className="prism-card-interactive p-6 rounded-lg border border-[#30363d] bg-[#010409] space-y-3 flex flex-col justify-between">
                <div className="space-y-3">
                  <div className="w-9 h-9 rounded-md bg-[#21262d] border border-[#30363d] flex items-center justify-center text-[#58a6ff]">
                    <SyncIcon size={18} />
                  </div>
                  <h3 className="text-base font-semibold text-[#f0f6fc]">
                    Patterns regress
                  </h3>
                  <p className="text-xs text-[#8b949e] leading-relaxed">
                    Rejected anti-patterns quietly slip back during fast-paced feature launches when the original reviewer isn't on the pull request.
                  </p>
                </div>
                <div className="pt-4 border-t border-[#21262d] text-[11px] text-[#58a6ff] font-medium">
                  Architecture silently drifts
                </div>
              </div>
            </div>

            {/* Bottom Insight Quote */}
            <div className="mt-12 p-4 rounded-md border border-[#30363d] bg-[#161b22] text-center text-xs text-[#c9d1d9] max-w-xl mx-auto">
              <strong className="text-[#f0f6fc]">The fundamental reality: </strong>
              Code survives in the repository. The reasoning behind it gets lost in time.
            </div>
          </div>
        </section>

        {/* =====================================================================
            4. SECTION: MEMORY GETS USEFUL (Visual Storytelling)
            ===================================================================== */}
        <section className="prism-reveal-section py-20 lg:py-24 border-b border-[#21262d] bg-[#010409]">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <div className="max-w-2xl mx-auto text-center space-y-3 mb-16">
              <span className="text-xs font-semibold text-[#58a6ff] uppercase tracking-wider">
                Visual Lifecycle
              </span>
              <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-[#f0f6fc]">
                How memory gets useful.
              </h2>
              <p className="text-sm sm:text-base text-[#8b949e] leading-relaxed">
                PRism turns transient review conversations into durable organizational knowledge that protects future changes.
              </p>
            </div>

            {/* 4-Step Narrative Grid */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 relative">
              {/* Step 1 */}
              <div className="prism-card-interactive p-5 rounded-lg border border-[#30363d] bg-[#0d1117] space-y-3 relative flex flex-col justify-between">
                <div>
                  <div className="text-[11px] font-mono text-[#58a6ff] font-semibold mb-1">
                    PHASE 01
                  </div>
                  <h3 className="text-sm font-semibold text-[#f0f6fc]">
                    A decision is made.
                  </h3>
                  <p className="text-xs text-[#8b949e] mt-2 leading-relaxed">
                    Your team deliberates on a pull request. You agree on a boundary, reject an anti-pattern, or approve a scoped exception.
                  </p>
                </div>
                <div className="p-2.5 rounded bg-[#161b22] border border-[#21262d] text-[11px] text-[#c9d1d9] font-mono">
                  &ldquo;Let's keep ORM queries out of HTTP controllers.&rdquo;
                </div>
              </div>

              {/* Step 2 */}
              <div className="prism-card-interactive p-5 rounded-lg border border-[#30363d] bg-[#0d1117] space-y-3 relative flex flex-col justify-between">
                <div>
                  <div className="text-[11px] font-mono text-[#3fb950] font-semibold mb-1">
                    PHASE 02
                  </div>
                  <h3 className="text-sm font-semibold text-[#f0f6fc]">
                    It becomes history.
                  </h3>
                  <p className="text-xs text-[#8b949e] mt-2 leading-relaxed">
                    PRism preserves the architectural reasoning, source PR, and exact scope into your repository's permanent memory.
                  </p>
                </div>
                <div className="p-2.5 rounded bg-[#161b22] border border-[#21262d] text-[11px] text-[#3fb950] flex items-center gap-1.5 font-medium">
                  <CheckIcon size={14} />
                  <span>Preserved as repository precedent</span>
                </div>
              </div>

              {/* Step 3 */}
              <div className="prism-card-interactive p-5 rounded-lg border border-[#30363d] bg-[#0d1117] space-y-3 relative flex flex-col justify-between">
                <div>
                  <div className="text-[11px] font-mono text-[#d29922] font-semibold mb-1">
                    PHASE 03
                  </div>
                  <h3 className="text-sm font-semibold text-[#f0f6fc]">
                    Months pass by.
                  </h3>
                  <p className="text-xs text-[#8b949e] mt-2 leading-relaxed">
                    A different engineer opens a new pull request touching the same architectural surface. The original reviewer is on vacation.
                  </p>
                </div>
                <div className="p-2.5 rounded bg-[#161b22] border border-[#21262d] text-[11px] text-[#c9d1d9] font-mono">
                  Incoming: new query added to OrdersController.ts
                </div>
              </div>

              {/* Step 4 */}
              <div className="prism-card-interactive p-5 rounded-lg border border-[#30363d] bg-[#0d1117] space-y-3 relative flex flex-col justify-between">
                <div>
                  <div className="text-[11px] font-mono text-[#58a6ff] font-semibold mb-1">
                    PHASE 04
                  </div>
                  <h3 className="text-sm font-semibold text-[#58a6ff]">
                    Context brought back.
                  </h3>
                  <p className="text-xs text-[#c9d1d9] mt-2 leading-relaxed">
                    PRism flags the precedent immediately on the PR, referencing the original discussion, PR number, and rationale.
                  </p>
                </div>
                <div className="p-2.5 rounded bg-[#1f6feb]/15 border border-[#1f6feb]/30 text-[11px] text-[#58a6ff] font-semibold">
                  Reviewer sees verified precedent instantly
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* =====================================================================
            5. SECTION: ENGINEERING MEMORY (Knowledge That Compounds)
            ===================================================================== */}
        <section id="memory" className="prism-reveal-section py-20 lg:py-24 border-b border-[#21262d] bg-[#0d1117]">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <div className="max-w-2xl mx-auto text-center space-y-3 mb-16">
              <span className="text-xs font-semibold text-[#3fb950] uppercase tracking-wider">
                Organizational Intelligence
              </span>
              <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-[#f0f6fc]">
                Knowledge that compounds.
              </h2>
              <p className="text-sm sm:text-base text-[#8b949e] leading-relaxed">
                PRism turns past engineering decisions into structured, reusable context for future reviews.
              </p>
            </div>

            {/* Interactive Category Selector & Card View */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
              {/* Category Pills (Left Side) */}
              <div className="lg:col-span-4 space-y-1.5">
                {Object.entries(MEMORY_CATEGORIES).map(([key, cat]) => {
                  const isActive = selectedCategory === key
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setSelectedCategory(key)}
                      className={`w-full text-left p-3.5 rounded-md border transition-all cursor-pointer flex items-center justify-between ${isActive
                        ? 'bg-[#161b22] border-[#58a6ff] text-[#f0f6fc] shadow-sm'
                        : 'bg-[#010409] border-[#30363d] text-[#8b949e] hover:text-[#f0f6fc] hover:border-[#8b949e]'
                        }`}
                    >
                      <div className="space-y-0.5 min-w-0">
                        <div className="font-semibold text-xs text-[#f0f6fc]">
                          {cat.label}
                        </div>
                        <div className="text-[11px] text-[#8b949e] line-clamp-1">
                          {cat.description}
                        </div>
                      </div>
                      <ChevronRightIcon
                        size={14}
                        className={isActive ? 'text-[#58a6ff] shrink-0' : 'text-[#30363d] shrink-0'}
                      />
                    </button>
                  )
                })}
              </div>

              {/* Memory Showcase Preview (Right Side) */}
              <div className="lg:col-span-8 rounded-lg border border-[#30363d] bg-[#010409] p-6 space-y-5">
                <div>
                  <div className="flex items-center gap-2">
                    <ShieldCheckIcon size={16} className="text-[#3fb950]" />
                    <h3 className="font-semibold text-sm text-[#f0f6fc]">
                      {activeCategoryData.label}
                    </h3>
                  </div>
                  <p className="text-xs text-[#8b949e] mt-1">
                    {activeCategoryData.description}
                  </p>
                </div>

                <div className="space-y-4">
                  {activeCategoryData.items.map((item) => (
                    <div
                      key={item.id}
                      className="prism-card-interactive p-4 rounded-md border border-[#30363d] bg-[#0d1117] space-y-3"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="space-y-0.5">
                          <h4 className="text-xs font-semibold text-[#f0f6fc]">
                            {item.title}
                          </h4>
                          <div className="text-[11px] text-[#8b949e]">
                            {item.subtitle}
                          </div>
                        </div>
                        <span className="px-2 py-0.5 rounded bg-[#21262d] border border-[#30363d] text-[#58a6ff] font-mono text-[10px] shrink-0">
                          {item.precedentPr}
                        </span>
                      </div>

                      <div className="p-3 rounded bg-[#010409] border border-[#21262d] space-y-2 text-xs">
                        <div>
                          <strong className="text-[#c9d1d9]">Rule / Claim: </strong>
                          <span className="text-[#f0f6fc]">{item.claim}</span>
                        </div>
                        <div>
                          <strong className="text-[#8b949e]">Why recorded: </strong>
                          <span className="text-[#c9d1d9]">{item.rationale}</span>
                        </div>
                      </div>

                      <div className="text-[11px] text-[#3fb950] font-medium flex items-center gap-1.5">
                        <CheckIcon size={12} />
                        <span>Impact: {item.impact}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* =====================================================================
            6. SECTION: HOW IT WORKS
            ===================================================================== */}
        <section id="how-it-works" className="prism-reveal-section py-20 lg:py-24 border-b border-[#21262d] bg-[#010409]">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <div className="max-w-2xl mx-auto text-center space-y-3 mb-16">
              <span className="text-xs font-semibold text-[#58a6ff] uppercase tracking-wider">
                Workflow
              </span>
              <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-[#f0f6fc]">
                How it works.
              </h2>
              <p className="text-sm sm:text-base text-[#8b949e] leading-relaxed">
                Connect your repository in seconds. No complex config files, no workflow changes.
              </p>
            </div>

            {/* 4 Clean Steps */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
              {/* Step 1 */}
              <div className="prism-card-interactive p-6 rounded-lg border border-[#30363d] bg-[#0d1117] space-y-3">
                <div className="font-mono text-xl font-bold text-[#8b949e]">01</div>
                <h3 className="text-base font-semibold text-[#f0f6fc]">
                  Connect repository
                </h3>
                <p className="text-xs text-[#8b949e] leading-relaxed">
                  Link your GitHub repository with one click. PRism indexes repository metadata and previous pull requests seamlessly.
                </p>
              </div>

              {/* Step 2 */}
              <div className="prism-card-interactive p-6 rounded-lg border border-[#30363d] bg-[#0d1117] space-y-3">
                <div className="font-mono text-xl font-bold text-[#8b949e]">02</div>
                <h3 className="text-base font-semibold text-[#f0f6fc]">
                  Review pull request
                </h3>
                <p className="text-xs text-[#8b949e] leading-relaxed">
                  When a new pull request is opened, PRism evaluates code diffs against structural AST changes and architectural boundaries.
                </p>
              </div>

              {/* Step 3 */}
              <div className="prism-card-interactive p-6 rounded-lg border border-[#30363d] bg-[#0d1117] space-y-3">
                <div className="font-mono text-xl font-bold text-[#58a6ff]">03</div>
                <h3 className="text-base font-semibold text-[#f0f6fc]">
                  Check team learnings
                </h3>
                <p className="text-xs text-[#8b949e] leading-relaxed">
                  Incoming changes are validated against the engineering decisions, rejected patterns, and approved exceptions your team already agreed on.
                </p>
              </div>

              {/* Step 4 */}
              <div className="prism-card-interactive p-6 rounded-lg border border-[#30363d] bg-[#0d1117] space-y-3">
                <div className="font-mono text-xl font-bold text-[#3fb950]">04</div>
                <h3 className="text-base font-semibold text-[#f0f6fc]">
                  Accumulate context
                </h3>
                <p className="text-xs text-[#8b949e] leading-relaxed">
                  Every reviewed decision compounds your team's collective memory. Future reviews on similar code become faster and more consistent.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* =====================================================================
            7. FINAL CTA SECTION
            ===================================================================== */}
        <section className="prism-reveal-section py-20 lg:py-28 bg-[#0d1117] border-b border-[#21262d] relative overflow-hidden">
          {/* Subtle ambient spectral background glow for final CTA */}
          <div
            className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[300px] rounded-full blur-[100px] pointer-events-none -z-10"
            style={{
              background: 'radial-gradient(ellipse at center, rgba(68,147,248,0.08) 0%, rgba(171,125,248,0.05) 40%, rgba(1,4,9,0) 70%)',
            }}
          />

          <div className="max-w-4xl mx-auto px-4 sm:px-6 text-center space-y-6">
            <h2 className="text-3xl sm:text-5xl font-extrabold tracking-tight text-[#f0f6fc] leading-tight">
              Your codebase has a history.
              <br />
              <span className="prism-headline-glow text-[#f0f6fc]">
                Make your reviews remember it.
              </span>
            </h2>

            <p className="text-base text-[#9198a1] max-w-xl mx-auto">
              Start preserving architectural reasoning across your repositories today.
            </p>

            <div className="pt-2">
              <button
                type="button"
                onClick={handleContinueWithGitHub}
                disabled={isAuthPending}
                className="prism-primary-cta group inline-flex items-center gap-2.5 px-6 py-3 rounded-md bg-[#238636] hover:bg-[#2ea043] border border-[rgba(240,246,252,0.15)] text-white text-base font-semibold shadow-lg transition-all cursor-pointer focus:outline-none focus:ring-2 focus:ring-[#3fb950] disabled:opacity-75 disabled:cursor-not-allowed"
              >
                {isAuthPending ? (
                  <>
                    <SyncIcon size={18} className="animate-spin" />
                    <span>Connecting to GitHub…</span>
                  </>
                ) : (
                  <>
                    <MarkGithubIcon size={18} />
                    <span>Continue with GitHub</span>
                    <ArrowRightIcon size={16} className="prism-cta-arrow" />
                  </>
                )}
              </button>
            </div>

            <div className="text-xs text-[#656c76]">
              Designed for high-velocity software engineering teams.
            </div>
          </div>
        </section>
      </main>

      {/* =====================================================================
          8. MINIMAL FOOTER
          ===================================================================== */}
      <footer className="py-12 bg-[#010409] text-xs text-[#8b949e]">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 flex flex-col sm:flex-row items-center justify-between gap-6">
          <div className="flex items-center gap-3">
            <PrismLogo size="sm" />
            <span className="font-semibold text-sm text-[#f0f6fc]">PRism</span>
            <span className="text-[#30363d]">•</span>
            <span>Code review that remembers.</span>
          </div>

          <div className="flex items-center gap-6">
            <a
              href="https://github.com"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-[#f0f6fc] transition-colors flex items-center gap-1.5 cursor-pointer"
            >
              <MarkGithubIcon size={14} />
              <span>GitHub</span>
            </a>
          </div>
        </div>
      </footer>

      {/* =====================================================================
          9. DEVELOPMENT-SAFE OAUTH UNCONFIGURED MODAL
          ===================================================================== */}
      {showAuthConfigNotice && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-xs">
          <div
            className="w-full max-w-md rounded-lg border border-[#30363d] bg-[#161b22] shadow-2xl p-5 space-y-4 text-xs"
            role="dialog"
            aria-modal="true"
            aria-labelledby="oauth-config-title"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-2.5 text-[#f0f6fc] font-semibold text-sm">
                <AlertIcon size={18} className="text-[#d29922] shrink-0" />
                <span id="oauth-config-title">GitHub sign-in isn't configured yet</span>
              </div>
              <button
                type="button"
                onClick={() => setShowAuthConfigNotice(false)}
                className="text-[#8b949e] hover:text-[#f0f6fc] p-1 rounded hover:bg-[#21262d] transition-colors cursor-pointer"
                aria-label="Close notification"
              >
                <XIcon size={14} />
              </button>
            </div>

            <div className="space-y-2 text-[#c9d1d9] leading-relaxed">
              <p>
                Real GitHub OAuth authentication requires configuring the{' '}
                <code className="px-1.5 py-0.5 rounded bg-[#0d1117] border border-[#30363d] text-[#58a6ff] font-mono text-[11px]">
                  VITE_GITHUB_CLIENT_ID
                </code>{' '}
                environment variable.
              </p>
              <p className="text-[#8b949e]">
                PRism does not simulate or fake authentication. Once real OAuth credentials are supplied, this button will start the GitHub authorization handshake.
              </p>
            </div>

            <div className="pt-2 border-t border-[#30363d] flex items-center justify-end">
              <button
                type="button"
                onClick={() => setShowAuthConfigNotice(false)}
                className="px-3.5 py-1.5 rounded-md bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] text-xs font-semibold text-[#f0f6fc] transition-colors cursor-pointer"
              >
                Dismiss
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default LandingPage
