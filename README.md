# PRism — Institutional Engineering Memory & Architectural Code Review

PRism is an engineering intelligence and code review workspace that bridges the gap between organizational decisions and daily pull request reviews. By pairing semantic institutional memory with deterministic AST code analysis, PRism detects architectural drift, surfaces relevant historical precedents, and produces explainable, evidence-grounded review recommendations.

---

## 1. Project Overview

### The Engineering Problem
Modern software teams make critical architectural decisions during code reviews, design docs, and incident postmortems. Over time, these decisions are forgotten as teams scale, PR context is lost, and codebases suffer from **architectural drift**—the gradual divergence from established design principles. Traditional static analysis tools miss organizational intent, while generic AI reviewers hallucinate non-existent guidelines or flag cosmetic stylistic issues without understanding team history.

### How PRism Solves It
PRism maintains an **institutional engineering memory** scoped per repository:
1. **Decision Tracking & Precedents**: Records approved architectural decisions, rejected patterns, and approved exceptions tied directly to source PRs and commits.
2. **Deterministic Pre-Filtering**: Analyzes pull request diffs using tree-sitter AST parsing and evaluates memory applicability against changed file paths and symbols.
3. **Evidence-Grounded Reviews**: Compares code changes against active, enforceable rules. Verdicts cite specific historical PRs and rationale—never fabricated rules.
4. **Controlled Learning**: When new architectural patterns or intentional exceptions emerge, PRism proposes them for explicit developer review and confirmation before adding them to canonical memory.

---

## 2. Current Architecture

PRism is built with a modular, service-oriented architecture:

```
┌────────────────────────────────────────────────────────┐
│             PRism Frontend (React + Vite)              │
│       GitHub Primer Design System / Tailwind CSS       │
└───────────────────────────┬────────────────────────────┘
                            │ REST / JSON (FastAPI)
┌───────────────────────────▼────────────────────────────┐
│                    PRism Backend                       │
│                                                        │
│  ┌───────────────────┐        ┌─────────────────────┐  │
│  │   ReviewEngine    │◄───────┤ ApplicabilityEngine │  │
│  └─────────┬─────────┘        └──────────▲──────────┘  │
│            │                             │             │
│  ┌─────────▼─────────┐        ┌──────────┴──────────┐  │
│  │   ContextEngine   │        │ CodeAnalysisService │  │
│  │ (Deep PR Context) │        │ (Tree-sitter AST)   │  │
│  └─────────┬─────────┘        └─────────────────────┘  │
│            │                                           │
│  ┌─────────▼─────────┐        ┌─────────────────────┐  │
│  │ WalkthroughEngine │        │   FixEngine         │  │
│  └───────────────────┘        └─────────────────────┘  │
└─────────────┬────────────────────────────┬─────────────┘
              │                            │
┌─────────────▼──────────┐      ┌──────────▼─────────────┐
│  Hindsight Memory Svc  │      │  Supabase (PostgreSQL) │
│ (Semantic Vector Bank) │      │ (Canonical State & RLS)│
└────────────────────────┘      └────────────────────────┘
```

- **Frontend**: Single-page application built with React 19, TypeScript, Vite, Tailwind CSS, and GitHub Primer design components (`@primer/react`, `@primer/octicons-react`).
- **Backend**: Python 3.11+ asynchronous service built with FastAPI and Pydantic v2.
- **Tree-sitter Code Analysis**: Multi-language AST parsing (Python, JavaScript, TypeScript, Java, C++) extracting structural symbols, class hierarchies, call graphs, and imported modules.
- **Semantic Memory (Hindsight)**: Vectorized memory bank storing architectural decisions, rationale, and precedent recollections.
- **Canonical Storage (Supabase PostgreSQL)**: Authoritative relational storage for repositories, pull requests, reviews, findings, and verified memories with Row-Level Security (RLS).
- **LLM Reasoning (Groq)**: Low-latency inference for architectural synthesis, walkthrough explanations, and context-aware patch generation.
- **Engines & Core Services**:
  - `ReviewEngine`: Orchestrates deterministic verdict evaluation and evidence synthesis.
  - `ApplicabilityEngine`: Evaluates memory applicability, scope match, and status validity.
  - `ContextEngine`: Bounds contextual file tokens and imports surrounding diffs.
  - `WalkthroughEngine`: Generates step-by-step architectural walkthroughs for complex changes.
  - `FixEngine`: Proposes candidate diff patches strictly adhering to repository precedent.
  - `ControlledLearningEngine`: Proposes new candidate memories from PR review discussions for human approval.

---

## 3. Prerequisites

Before installing PRism locally, ensure the following runtimes and tools are installed:

- **Python**: Version `3.11` or higher (verified with Python 3.11.9)
- **Node.js**: Version `20.x` or higher (LTS recommended)
- **npm**: Version `10.x` or higher
- **Git**: For version control operations
- **Supabase CLI** (optional for local PostgreSQL) or an active Supabase project

---

## 4. Local Setup (Windows PowerShell)

Follow these steps to set up and run PRism locally:

### 1. Clone the repository
```powershell
git clone <repository-url>
cd engineering-review
```

### 2. Set up Backend Virtual Environment
```powershell
# Navigate to the backend directory
cd backend

# Create virtual environment
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Upgrade pip and install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure Backend Environment
```powershell
# Copy template to .env
Copy-Item .env.example .env

# Open .env and fill in required local credentials
notepad .env
```

### 4. Database Setup & Migrations
PRism uses Supabase PostgreSQL for canonical application state. Migrations are located under `supabase/migrations/`:
- `20260928000000_initial_schema.sql`: Core tables (`repositories`, `pull_requests`, `reviews`, `review_findings`) and RLS policies.
- `20260929000000_engineering_memory.sql`: Engineering memory schema and scoping.
- `20260930000000_persist_historical_context.sql`: Snapshot preservation for reviews.
- `20260930010000_fix_supabase_auth_rls.sql`: Authenticated user RLS policy fixes.
- `20260930020000_canonical_memory_model.sql`: Canonical memory classification, authorities, and conflict detection.
- `20260930030000_controlled_learning.sql`: Controlled learning suggestions and audit trail.

If using Supabase CLI locally:
```powershell
supabase db reset
# or apply migrations against a remote project:
# supabase db push
```
*Note: In local development without Supabase credentials, the backend automatically operates with an in-memory fallback storage mode.*

### 5. Set up Frontend
Open a second PowerShell terminal:
```powershell
cd frontend

# Install dependencies
npm install

# Copy frontend environment template
Copy-Item .env.example .env

# Verify VITE_API_URL points to the backend (default: http://localhost:8000)
```

### 6. Start the Services
**Terminal 1 (Backend):**
```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```
The backend API will be available at `http://localhost:8000` (OpenAPI documentation at `http://localhost:8000/docs`).

**Terminal 2 (Frontend):**
```powershell
cd frontend
npm run dev
```
The frontend UI will be available at `http://localhost:5173`.

---

## 5. Environment Configuration

| Variable | Scope | Required | Description |
|---|---|---|---|
| `ENVIRONMENT` | Backend | Optional | `development` or `production` |
| `API_V1_STR` | Backend | Optional | API URL prefix (default: `/api/v1`) |
| `CORS_ORIGINS` | Backend | Optional | Allowed origin array for browser requests |
| `SUPABASE_URL` | Backend | Required for DB | Supabase project REST URL |
| `SUPABASE_KEY` | Backend | Required for DB | Supabase public anonymous API key |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend | Backend Only | Supabase administrative service role key (Never expose to frontend!) |
| `HINDSIGHT_API_KEY` | Backend | Optional | Vector memory API key (falls back to local memory if unset) |
| `HINDSIGHT_BASE_URL` | Backend | Optional | Hindsight API base URL |
| `HINDSIGHT_DEFAULT_BANK_ID` | Backend | Optional | Default bank identifier for memory storage |
| `GROQ_API_KEY` | Backend | Required for AI | Groq API key for LLM inference |
| `GROQ_REASONING_MODEL` | Backend | Optional | Primary reasoning model (`openai/gpt-oss-120b`) |
| `GROQ_LIGHTWEIGHT_MODEL` | Backend | Optional | Fast model (`openai/gpt-oss-20b`) |
| `GITHUB_CLIENT_ID` | Backend | Required for OAuth | GitHub OAuth Application Client ID |
| `GITHUB_CLIENT_SECRET` | Backend | Backend Only | GitHub OAuth Application Client Secret |
| `GITHUB_REDIRECT_URI` | Backend | Required for OAuth | OAuth callback URL (`http://localhost:8000/api/v1/auth/callback`) |
| `SESSION_SECRET_KEY` | Backend | Required | Cryptographic secret for signing session tokens |
| `GITHUB_TOKEN` | Backend | Optional | Personal access token fallback for local manual inspection |
| `GITHUB_WEBHOOK_SECRET` | Backend | Optional | Secret for validating incoming GitHub webhook payloads |
| `VITE_API_URL` | Frontend | Required | Base URL of the backend API (`http://localhost:8000`) |

---

## 6. Testing & Quality Checks

### Backend Test Suite (Offline Unit & Integration Tests)
Run the full backend test suite using the isolated local mock fixtures:
```powershell
cd backend
.\.venv\Scripts\pytest -q
```
All automated test runs operate with deterministic, isolated in-memory fixtures without requiring active third-party subscriptions.

### Live External Integration Tests (Opt-In Only)
Live external integration testing against real third-party endpoints (e.g., active Hindsight cloud banks or live GitHub repositories) requires explicit local credentials and must be run separately with opt-in flags.

### Frontend Quality Checks
```powershell
cd frontend

# Code linting via Oxlint
npm run lint

# TypeScript compilation check & production bundle build
npm run build
```

---

## 7. Architecture & Memory Safety Invariants

PRism enforces strict architectural invariants to prevent hallucinated compliance and unauthorized state changes:

1. **`NO_PRECEDENT` is NOT Approval**: The absence of an existing architectural rule does not mean code conforms to standards. It simply means no precedent exists.
2. **Relevance is NOT Authority**: Historical discussions or recollections cannot trigger a `CONFORMING` or `DRIFT` verdict unless they are formally confirmed as active, enforceable rules applicable to the changed files.
3. **Absence of Memory Does Not Fabricate Compliance**: A clean pull request with zero applicable rules produces `NO_PRECEDENT`, never `CONFORMING`.
4. **Authoritative Evidence Required**: An architectural drift finding is only emitted when an active, enforceable rule with validated provenance matches the file path and structural symbols.
5. **Two-Tier Memory Separation**:
   - **Hindsight** serves as the semantic retrieval and recollection layer.
   - **Supabase** serves as the canonical, authoritative application state and audit log.
6. **Strict Repository Scoping**: Memories and reviews are partitioned strictly by repository ID. Cross-repository leakage is prevented by database RLS and application-layer assertion guards.
7. **Human-in-the-Loop Controlled Learning**: New architectural guidelines proposed from review interactions require explicit developer confirmation before graduating to enforceable status.
8. **No Silent Remote Mutation**: PRism operates in a read-only review posture; it never mutates pull request branches or merges code on GitHub without explicit developer action.

---

## 8. Project Status

PRism has completed implementation of Phases 1 through 10:
- Phase 1: Core Domain Models & Schemas
- Phase 2: Tree-sitter Code Analysis Pipeline
- Phase 3: Semantic Memory Engine (Hindsight Integration)
- Phase 4: Canonical Memory State & Scoping (Supabase)
- Phase 5: GitHub Integration & OAuth Authentication
- Phase 6: Explainable Review Engine & Verdict Determination
- Phase 7: Semantic Walkthrough Engine
- Phase 8: Context-Aware Fix Engine
- Phase 9: Controlled Learning Engine & Human-in-the-Loop Approval
- Phase 10: Security Hardening, Repository Isolation & Audit Remediation

*Note: PRism is an actively developed engineering intelligence workspace. It is currently in a pre-release development stage and has not yet undergone formal third-party security certification or production deployment.*

---

## 9. Security & Privacy

- **Environment Files**: `.env` and `*.local` files contain sensitive credentials and must **never** be committed to version control.
- **Service Role Credentials**: `SUPABASE_SERVICE_ROLE_KEY` grants full database access and must remain strictly confidential in backend environments. Never expose it in client code or frontend bundles.
- **Local Application Data**: Session cache files and local databases created during local development under `backend/data/` are strictly ignored by `.gitignore`.

---

## 10. License

The repository license has not yet been selected. All rights reserved until an explicit open-source or proprietary license is chosen and added by the repository owner prior to initial publication.
