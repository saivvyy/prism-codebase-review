import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.exceptions import ConfigurationError, HindsightMemoryError
from app.schemas.bootstrap import (
    BootstrapCandidate,
    BootstrapSourceRequest,
    BootstrapSummaryResponse,
)
from app.schemas.canonical_memory import (
    CanonicalMemory,
    KnowledgeType,
    MemoryAuthority,
    MemoryStatus,
    compute_memory_identity,
    get_default_authority,
    normalize_claim_or_statement,
    normalize_knowledge_type,
    normalize_status,
    validate_provenance,
)
from app.services.github import GitHubService, get_github_service
from app.services.groq import GroqService, get_groq_service
from app.services.memory import HindsightMemoryService, get_hindsight_service
from app.services.supabase import SupabaseService, get_supabase_service

logger = logging.getLogger(__name__)


def get_repo_bank_id(repo_id: str, full_name: Optional[str] = None) -> str:
    """Returns deterministic bank_id scoped strictly to a repository."""
    if full_name and "/" in full_name:
        return full_name.replace("/", "_").lower()
    if repo_id and "/" in repo_id:
        return repo_id.replace("/", "_").lower()
    if full_name:
        return full_name.replace("/", "_").lower()
    return f"repo_{repo_id.replace('-', '').lower()}"


class HistoricalBootstrapService:
    """Discovers, parses, classifies, deduplicates, and retains repository architectural memory

    Evidence-first, bounded, repository-scoped, deduplicated, and provenance-backed.
    """

    KNOWN_ADR_DIRS = [
        "docs/adr",
        "doc/adr",
        "adr",
        "docs/decisions",
        "architecture/decisions",
    ]

    KNOWN_DOC_FILES = [
        "docs/architecture.md",
        "architecture.md",
        "docs/design.md",
        "design.md",
        "docs/ARCH.md",
        "docs/architecture/overview.md",
    ]

    KNOWN_GUIDELINE_FILES = [
        "CONTRIBUTING.md",
        ".github/CONTRIBUTING.md",
        "docs/CONTRIBUTING.md",
        "guidelines.md",
        "docs/guidelines.md",
        "DEVELOPMENT.md",
        "docs/engineering-guidelines.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
    ]

    def __init__(
        self,
        github: Optional[GitHubService] = None,
        supabase: Optional[SupabaseService] = None,
        memory: Optional[HindsightMemoryService] = None,
        groq: Optional[GroqService] = None,
    ):
        self.github = github or get_github_service()
        self.supabase = supabase or get_supabase_service()
        self.memory = memory or get_hindsight_service()
        self.groq = groq or get_groq_service()

    # =========================================================================
    # 1. Source Discovery
    # =========================================================================

    async def discover_sources(
        self,
        owner: str,
        repo_name: str,
        req: BootstrapSourceRequest,
    ) -> List[Dict[str, str]]:
        """Discovers relevant repository files for knowledge extraction without scanning entire repo."""
        discovered: List[Dict[str, str]] = []

        if req.document_paths:
            for p in req.document_paths:
                p_clean = p.strip().lstrip("/")
                src_type = "adr" if "adr" in p_clean.lower() else "architecture_doc"
                discovered.append({"type": src_type, "path": p_clean})
            return discovered

        # 1. ADR Directories
        if req.include_adrs:
            for adr_dir in self.KNOWN_ADR_DIRS:
                try:
                    entries = await self.github.list_directory_contents(owner, repo_name, adr_dir)
                    for item in entries:
                        if isinstance(item, dict):
                            fname = item.get("name", "")
                            fpath = item.get("path", "")
                            if fname.endswith(".md") and not fname.lower().startswith("template"):
                                discovered.append({"type": "adr", "path": fpath})
                except Exception as e:
                    logger.debug("ADR discovery check failed for %s: %s", adr_dir, e)

        # 2. Architecture / Design Docs
        if req.include_docs:
            for doc_file in self.KNOWN_DOC_FILES:
                try:
                    content = await self.github.get_file_contents(owner, repo_name, doc_file)
                    if content:
                        discovered.append({"type": "architecture_doc", "path": doc_file})
                except Exception as e:
                    logger.debug("Doc file check failed for %s: %s", doc_file, e)

        # 3. CONTRIBUTING and Guidelines
        if req.include_guidelines:
            for guide_file in self.KNOWN_GUIDELINE_FILES:
                try:
                    content = await self.github.get_file_contents(owner, repo_name, guide_file)
                    if content:
                        src_type = "contributing_guide" if "contributing" in guide_file.lower() else "repo_guidelines"
                        discovered.append({"type": src_type, "path": guide_file})
                except Exception as e:
                    logger.debug("Guidelines file check failed for %s: %s", guide_file, e)

        return discovered

    # =========================================================================
    # 2. Evidence Parsers
    # =========================================================================

    def parse_adr_content(
        self,
        content: str,
        file_path: str,
        repo_full_name: str,
    ) -> List[BootstrapCandidate]:
        """Parses Architectural Decision Records (MADR / Nygard format)."""
        candidates: List[BootstrapCandidate] = []
        filename = file_path.split("/")[-1]

        # Extract title or ADR reference (e.g. ADR-0001 or filename)
        source_ref = filename.removesuffix(".md").upper()
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        adr_title = title_match.group(1).strip() if title_match else filename

        ref_match = re.search(r"(ADR[-\s_]?\d+)", f"{adr_title} {filename}", re.IGNORECASE)
        if ref_match:
            source_ref = ref_match.group(1).replace("_", "-").replace(" ", "-").upper()

        # Parse Status
        status_val = MemoryStatus.ACTIVE
        superseded_target: Optional[str] = None
        supersedes_target: Optional[str] = None

        status_match = re.search(r"(?:Status|State):\s*([^\n\r]+)", content, re.IGNORECASE)
        if status_match:
            status_line = status_match.group(1).strip()
            status_lower = status_line.lower()

            if "superseded by" in status_lower:
                status_val = MemoryStatus.SUPERSEDED
                sup_match = re.search(r"superseded by\s+(?:ADR[-\s_]?)?(\d+|[\w\-]+)", status_line, re.IGNORECASE)
                if sup_match:
                    superseded_target = f"ADR-{sup_match.group(1)}" if sup_match.group(1).isdigit() else sup_match.group(1)
            elif "supersedes" in status_lower:
                status_val = MemoryStatus.ACTIVE
                sup_match = re.search(r"supersedes\s+(?:ADR[-\s_]?)?(\d+|[\w\-]+)", status_line, re.IGNORECASE)
                if sup_match:
                    supersedes_target = f"ADR-{sup_match.group(1)}" if sup_match.group(1).isdigit() else sup_match.group(1)
            elif any(w in status_lower for w in ("revoked", "deprecated", "rejected")):
                status_val = MemoryStatus.REVOKED
            elif any(w in status_lower for w in ("accepted", "approved", "active")):
                status_val = MemoryStatus.ACTIVE

        # Extract Context / Rationale
        rationale = "Documented in architectural decision record."
        context_match = re.search(
            r"##\s+(?:Context|Rationale|Background)\s*\n+(.*?)(?=\n##|\Z)",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if context_match:
            r_text = context_match.group(1).strip()
            # Clean markdown formatting
            r_clean = re.sub(r"\n+", " ", r_text)
            if len(r_clean) >= 10:
                rationale = r_clean[:500]

        # Extract Decision Statement
        decision_statement = ""
        decision_match = re.search(
            r"##\s+(?:Decision|Solution|Outcome)\s*\n+(.*?)(?=\n##|\Z)",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if decision_match:
            d_text = decision_match.group(1).strip()
            # Find the primary sentence or paragraph
            d_lines = [line.strip().lstrip("-* ").strip() for line in d_text.split("\n") if line.strip()]
            if d_lines:
                decision_statement = d_lines[0]
                if len(decision_statement) < 20 and len(d_lines) > 1:
                    decision_statement += " " + d_lines[1]

        if not decision_statement:
            # Fallback to title
            clean_title = re.sub(r"^(?:ADR[-\s_]?\d+[:.]?\s*|\d+[:.]\s*)", "", adr_title, flags=re.IGNORECASE).strip()
            if len(clean_title) >= 10:
                decision_statement = clean_title

        # Infer Scope
        scope = "Repository architecture"
        scope_match = re.search(r"(?:Scope|Component|Layer):\s*([^\n\r]+)", content, re.IGNORECASE)
        if scope_match:
            scope = scope_match.group(1).strip()
        elif "controller" in decision_statement.lower() or "controller" in content.lower():
            scope = "*Controller*"
        elif "service" in decision_statement.lower():
            scope = "*Service*"
        elif "database" in decision_statement.lower() or "sql" in decision_statement.lower():
            scope = "Database / Persistence"

        source_url = f"https://github.com/{repo_full_name}/blob/main/{file_path}"

        if decision_statement and len(decision_statement) >= 5:
            candidates.append(
                BootstrapCandidate(
                    statement=decision_statement,
                    rationale=rationale,
                    scope=scope,
                    type=KnowledgeType.DOCUMENTED_DECISION,
                    authority=MemoryAuthority.ENFORCEABLE,
                    status=status_val,
                    source_type="adr",
                    source_ref=source_ref,
                    source_url=source_url,
                    supersedes=supersedes_target,
                    superseded_by=superseded_target,
                )
            )

        # Check for Explicit Considered Alternatives / Rejected Patterns (Source Priority 6)
        alternatives_match = re.search(
            r"##\s+(?:Considered Alternatives|Rejected Patterns|Alternatives Considered)\s*\n+(.*?)(?=\n##|\Z)",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if alternatives_match:
            alt_text = alternatives_match.group(1).strip()
            for line in alt_text.split("\n"):
                l_clean = line.strip().lstrip("-* ").strip()
                if not l_clean or len(l_clean) < 10:
                    continue
                if any(w in l_clean.lower() for w in ("rejected", "disallowed", "not chosen", "prohibited", "avoid", "do not")):
                    candidates.append(
                        BootstrapCandidate(
                            statement=l_clean,
                            rationale=f"Rejected alternative documented in {source_ref}.",
                            scope=scope,
                            type=KnowledgeType.REJECTED_PATTERN,
                            authority=MemoryAuthority.REJECTED_PATTERN,
                            status=MemoryStatus.ACTIVE,
                            source_type="adr",
                            source_ref=f"{source_ref} (Rejected Pattern)",
                            source_url=source_url,
                        )
                    )

        # Check for Explicit Approved Exceptions (Source Priority 7)
        exceptions_match = re.search(
            r"##\s+(?:Exceptions|Approved Exceptions|Allowed Exceptions)\s*\n+(.*?)(?=\n##|\Z)",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if exceptions_match:
            exc_text = exceptions_match.group(1).strip()
            for line in exc_text.split("\n"):
                l_clean = line.strip().lstrip("-* ").strip()
                if not l_clean or len(l_clean) < 10:
                    continue
                candidates.append(
                    BootstrapCandidate(
                        statement=l_clean,
                        rationale=f"Approved exception documented in {source_ref}.",
                        scope=scope,
                        type=KnowledgeType.APPROVED_EXCEPTION,
                        authority=MemoryAuthority.EXCEPTION,
                        status=MemoryStatus.ACTIVE,
                        source_type="adr",
                        source_ref=f"{source_ref} (Approved Exception)",
                        source_url=source_url,
                    )
                )

        return candidates

    def parse_guidelines_content(
        self,
        content: str,
        file_path: str,
        source_type: str,
        repo_full_name: str,
    ) -> List[BootstrapCandidate]:
        """Parses CONTRIBUTING.md, design docs, or repository guidelines for explicit rules."""
        candidates: List[BootstrapCandidate] = []
        source_ref = file_path.split("/")[-1]
        source_url = f"https://github.com/{repo_full_name}/blob/main/{file_path}"

        # Split into sections
        sections = re.split(r"\n(?=#{1,3}\s+)", content)
        for sec in sections:
            header_match = re.match(r"^(#{1,3})\s+([^\n]+)", sec)
            sec_title = header_match.group(2).strip() if header_match else "General"
            sec_title_lower = sec_title.lower()

            # Target relevant architectural or coding sections
            is_relevant_section = any(
                term in sec_title_lower
                for term in (
                    "architect",
                    "design",
                    "rule",
                    "convention",
                    "guideline",
                    "standard",
                    "pattern",
                    "layer",
                    "controller",
                    "service",
                    "database",
                )
            )
            if not is_relevant_section and source_type != "architecture_doc":
                continue

            lines = sec.split("\n")
            for line in lines:
                l_clean = line.strip().lstrip("-* ").strip()
                if len(l_clean) < 15:
                    continue

                l_lower = l_clean.lower()
                # Check explicit rules
                has_enforceable_imperative = any(
                    w in l_lower for w in ("must ", "must not", "required", "prohibited", "forbidden", "always ", "never ")
                )
                has_rejected_imperative = any(
                    w in l_lower for w in ("must not", "prohibited", "forbidden", "never ", "rejected")
                )
                has_exception_indicator = any(
                    w in l_lower for w in ("except for", "permitted to", "allowed to", "exception:")
                )
                has_convention_indicator = any(
                    w in l_lower for w in ("prefer ", "should ", "recommended", "convention:", "style:")
                )

                scope = "Repository architecture"
                if "controller" in l_lower:
                    scope = "*Controller*"
                elif "service" in l_lower:
                    scope = "*Service*"
                elif "db" in l_lower or "database" in l_lower:
                    scope = "Database layer"

                if has_exception_indicator and (has_enforceable_imperative or "exception" in l_lower):
                    candidates.append(
                        BootstrapCandidate(
                            statement=l_clean,
                            rationale=f"Documented exception in {source_ref} ({sec_title}).",
                            scope=scope,
                            type=KnowledgeType.APPROVED_EXCEPTION,
                            authority=MemoryAuthority.EXCEPTION,
                            status=MemoryStatus.ACTIVE,
                            source_type=source_type,
                            source_ref=f"{source_ref} ({sec_title})",
                            source_url=source_url,
                        )
                    )
                elif has_rejected_imperative:
                    candidates.append(
                        BootstrapCandidate(
                            statement=l_clean,
                            rationale=f"Explicitly rejected pattern in {source_ref} ({sec_title}).",
                            scope=scope,
                            type=KnowledgeType.REJECTED_PATTERN,
                            authority=MemoryAuthority.REJECTED_PATTERN,
                            status=MemoryStatus.ACTIVE,
                            source_type=source_type,
                            source_ref=f"{source_ref} ({sec_title})",
                            source_url=source_url,
                        )
                    )
                elif has_enforceable_imperative:
                    candidates.append(
                        BootstrapCandidate(
                            statement=l_clean,
                            rationale=f"Explicit engineering rule documented in {source_ref} ({sec_title}).",
                            scope=scope,
                            type=KnowledgeType.DOCUMENTED_DECISION,
                            authority=MemoryAuthority.ENFORCEABLE,
                            status=MemoryStatus.ACTIVE,
                            source_type=source_type,
                            source_ref=f"{source_ref} ({sec_title})",
                            source_url=source_url,
                        )
                    )
                elif has_convention_indicator:
                    # Inferred/preferred conventions are OBSERVED_CONVENTION only
                    candidates.append(
                        BootstrapCandidate(
                            statement=l_clean,
                            rationale=f"Documented guideline convention in {source_ref}.",
                            scope=scope,
                            type=KnowledgeType.OBSERVED_CONVENTION,
                            authority=MemoryAuthority.CONTEXTUAL,
                            status=MemoryStatus.ACTIVE,
                            source_type=source_type,
                            source_ref=f"{source_ref} ({sec_title})",
                            source_url=source_url,
                        )
                    )

        return candidates

    def parse_pr_review_evidence(
        self,
        pr_number: int,
        pr_title: str,
        pr_body: Optional[str],
        pr_url: Optional[str],
        head_sha: Optional[str],
        comments: List[Any],
        reviews: List[Any],
    ) -> List[BootstrapCandidate]:
        """Extracts candidate knowledge from explicit historical PR review decisions."""
        candidates: List[BootstrapCandidate] = []
        source_ref = f"PR #{pr_number}"

        # Combine text items to scan
        text_blobs = []
        if pr_body:
            text_blobs.append(("PR description", pr_body))
        for c in comments:
            b = getattr(c, "body", None) if not isinstance(c, dict) else c.get("body")
            if b:
                text_blobs.append(("PR comment", b))
        for r in reviews:
            b = getattr(r, "body", None) if not isinstance(r, dict) else r.get("body")
            if b:
                text_blobs.append(("Review summary", b))

        for origin, text in text_blobs:
            # Look for explicit architectural decisions, exceptions, or rejected patterns
            lines = text.split("\n")
            for line in lines:
                l_clean = line.strip().lstrip("-* >").strip()
                if len(l_clean) < 15:
                    continue

                l_lower = l_clean.lower()
                scope = "*Controller*" if "controller" in l_lower else ("*Service*" if "service" in l_lower else "Repository architecture")

                if "approved exception" in l_lower:
                    statement = re.sub(r"^approved\s+exception(?:\s+in\s+pr\s*#\d+)?:?\s*", "", l_clean, flags=re.IGNORECASE).strip()
                    candidates.append(
                        BootstrapCandidate(
                            statement=statement or l_clean,
                            rationale=f"Explicit approved exception decided in {source_ref} ({origin}).",
                            scope=scope,
                            type=KnowledgeType.APPROVED_EXCEPTION,
                            authority=MemoryAuthority.EXCEPTION,
                            status=MemoryStatus.ACTIVE,
                            source_type="pr_review",
                            source_ref=source_ref,
                            source_url=pr_url,
                            source_pr_number=pr_number,
                            source_commit_sha=head_sha,
                        )
                    )
                elif "rejected pattern" in l_lower:
                    statement = re.sub(r"^rejected\s+pattern(?:\s+in\s+pr\s*#\d+)?:?\s*", "", l_clean, flags=re.IGNORECASE).strip()
                    candidates.append(
                        BootstrapCandidate(
                            statement=statement or l_clean,
                            rationale=f"Explicit rejected pattern decided in {source_ref} ({origin}).",
                            scope=scope,
                            type=KnowledgeType.REJECTED_PATTERN,
                            authority=MemoryAuthority.REJECTED_PATTERN,
                            status=MemoryStatus.ACTIVE,
                            source_type="pr_review",
                            source_ref=source_ref,
                            source_url=pr_url,
                            source_pr_number=pr_number,
                            source_commit_sha=head_sha,
                        )
                    )
                elif "architectural decision" in l_lower or "architecture decision" in l_lower:
                    statement = re.sub(r"^(?:architectural|architecture)\s+decision(?:\s+in\s+pr\s*#\d+)?:?\s*", "", l_clean, flags=re.IGNORECASE).strip()
                    candidates.append(
                        BootstrapCandidate(
                            statement=statement or l_clean,
                            rationale=f"Explicit architectural decision decided in {source_ref} ({origin}).",
                            scope=scope,
                            type=KnowledgeType.DOCUMENTED_DECISION,
                            authority=MemoryAuthority.ENFORCEABLE,
                            status=MemoryStatus.ACTIVE,
                            source_type="pr_review",
                            source_ref=source_ref,
                            source_url=pr_url,
                            source_pr_number=pr_number,
                            source_commit_sha=head_sha,
                        )
                    )

        return candidates

    # =========================================================================
    # 3. Main Bootstrap Pipeline
    # =========================================================================

    async def bootstrap_repository(
        self,
        repository_id: str,
        repository_full_name: str,
        req: Optional[BootstrapSourceRequest] = None,
    ) -> BootstrapSummaryResponse:
        """Executes full repository bootstrap: discovery -> extraction -> validation -> deduplication -> persistence."""
        options = req or BootstrapSourceRequest()
        parts = repository_full_name.split("/")
        if len(parts) != 2:
            raise ValueError(f"Invalid repository full name format: '{repository_full_name}'. Expected 'owner/repo'.")
        owner, repo_name = parts[0].strip(), parts[1].strip()

        summary = BootstrapSummaryResponse(
            repository_id=repository_id,
            repository_full_name=repository_full_name,
        )

        # 1. Discover Documents
        discovered_sources = await self.discover_sources(owner, repo_name, options)
        summary.sources_discovered = [s["path"] for s in discovered_sources]

        raw_candidates: List[BootstrapCandidate] = []

        # 2. Extract Candidates from Discovered Files
        for src in discovered_sources:
            path = src["path"]
            src_type = src["type"]
            try:
                content = await self.github.get_file_contents(owner, repo_name, path)
                if not content:
                    continue

                if src_type == "adr":
                    file_cands = self.parse_adr_content(content, path, repository_full_name)
                else:
                    file_cands = self.parse_guidelines_content(content, path, src_type, repository_full_name)

                raw_candidates.extend(file_cands)
            except Exception as e:
                logger.warning("Failed to extract candidates from '%s': %s", path, e)

        # 3. Extract Candidates from Bounded Historical PRs (Source Priority 5)
        if options.include_prs and options.max_prs > 0:
            try:
                prs_res = await self.github.list_pull_requests(
                    owner=owner,
                    repo=repo_name,
                    state="merged",
                    per_page=min(options.max_prs, 30),
                    fetch_limit=options.max_prs,
                )
                prs = prs_res.items if hasattr(prs_res, "items") else list(prs_res)
                for pr in prs[:options.max_prs]:
                    try:
                        comments = await self.github.get_pr_comments(owner, repo_name, pr.number)
                        reviews = await self.github.get_pr_reviews(owner, repo_name, pr.number)
                        pr_cands = self.parse_pr_review_evidence(
                            pr_number=pr.number,
                            pr_title=pr.title,
                            pr_body=pr.body,
                            pr_url=pr.html_url,
                            head_sha=pr.head_sha,
                            comments=comments,
                            reviews=reviews,
                        )
                        raw_candidates.extend(pr_cands)
                    except Exception as e:
                        logger.debug("PR #%s evidence extraction skipped: %s", pr.number, e)
            except Exception as e:
                logger.warning("Bounded PR history extraction failed: %s", e)

        # 4. Extract Inferred Implementation Conventions (Source Priority 8)
        # CRITICAL RULE: Inferred historical behavior ONLY as OBSERVED_CONVENTION, never authoritative!
        if options.inferred_conventions:
            for conv in options.inferred_conventions:
                stmt = str(conv.get("statement") or conv.get("claim") or "").strip()
                if not stmt or len(stmt) < 5:
                    continue
                raw_candidates.append(
                    BootstrapCandidate(
                        statement=stmt,
                        rationale=str(conv.get("rationale") or "Observed statistical convention across repository codebase."),
                        scope=str(conv.get("scope") or "Repository architecture"),
                        type=KnowledgeType.OBSERVED_CONVENTION,
                        authority=MemoryAuthority.CONTEXTUAL,
                        status=MemoryStatus.ACTIVE,
                        source_type="code_convention",
                        source_ref=str(conv.get("source_ref") or "Repository codebase analysis"),
                        source_url=conv.get("source_url"),
                    )
                )

        summary.candidates_extracted = len(raw_candidates)

        # 5. Provenance Validation & Filtering
        valid_candidates: List[BootstrapCandidate] = []
        for cand in raw_candidates:
            # Validate provenance strictly
            is_valid, reason = validate_provenance({
                "repository_id": repository_id,
                "source_ref": cand.source_ref,
                "source_url": cand.source_url,
                "source_pr_number": cand.source_pr_number,
                "source_commit_sha": cand.source_commit_sha,
            })
            if not is_valid:
                logger.warning("Rejecting bootstrap candidate with invalid provenance: %s", reason)
                summary.candidates_rejected += 1
                continue
            valid_candidates.append(cand)

        # 6. Fetch Existing Memories from Supabase for this repository (Isolation & Idempotency)
        existing_memories_raw = await self.supabase.list_engineering_memories(
            repository_id=repository_id,
            status="all",
        )
        existing_identities: Set[str] = set()
        existing_statements: Dict[str, Dict[str, Any]] = {}
        ref_lookup: Dict[str, Dict[str, Any]] = {}

        for em in existing_memories_raw:
            s_norm = normalize_claim_or_statement(em.get("statement") or em.get("claim") or "").lower()
            em_id_hash = em.get("identity_hash") or compute_memory_identity(
                repository_id=repository_id,
                statement=s_norm,
                scope=em.get("scope"),
            )
            existing_identities.add(em_id_hash)
            existing_statements[s_norm] = em
            source_ref_clean = (em.get("source_ref") or "").strip().upper()
            if source_ref_clean:
                ref_lookup[source_ref_clean] = em

        # 7. Deduplication, Supersession, and Conflict Processing
        batch_seen_identities: Set[str] = set()
        candidates_to_persist: List[BootstrapCandidate] = []

        for cand in valid_candidates:
            norm_s = normalize_claim_or_statement(cand.statement).lower()
            cand_id_hash = compute_memory_identity(
                repository_id=repository_id,
                statement=norm_s,
                scope=cand.scope,
            )
            cand.identity_hash = cand_id_hash

            # Deduplication check against existing persistent memories
            if cand_id_hash in existing_identities or norm_s in existing_statements:
                matched_existing = existing_statements.get(norm_s) or next(
                    (m for m in existing_memories_raw if m.get("identity_hash") == cand_id_hash), None
                )
                if matched_existing:
                    # Reinforce evidence if new source_ref
                    ev_items = list(matched_existing.get("evidence_items") or [])
                    if cand.source_ref and cand.source_ref not in ev_items:
                        ev_items.append(cand.source_ref)
                        await self.supabase.update_engineering_memory(
                            matched_existing["id"],
                            {"evidence_items": ev_items},
                        )
                summary.candidates_deduplicated += 1
                continue

            # Deduplication check against current batch
            if cand_id_hash in batch_seen_identities:
                summary.candidates_deduplicated += 1
                continue

            batch_seen_identities.add(cand_id_hash)
            candidates_to_persist.append(cand)

        # Handle Supersession (Source Priority & Rule 7)
        for cand in candidates_to_persist:
            if cand.supersedes:
                target_ref = cand.supersedes.strip().upper()
                old_mem = ref_lookup.get(target_ref)
                if old_mem:
                    await self.supabase.update_engineering_memory(
                        old_mem["id"],
                        {
                            "status": MemoryStatus.SUPERSEDED.value,
                            "superseded_by": cand.source_ref,
                        },
                    )
                    summary.candidates_superseded += 1

        # Check for Conflicts among active enforceable rules in batch
        enforceable_in_batch = [c for c in candidates_to_persist if c.authority in (MemoryAuthority.ENFORCEABLE, MemoryAuthority.REJECTED_PATTERN) and c.status == MemoryStatus.ACTIVE]
        for i in range(len(enforceable_in_batch)):
            for j in range(i + 1, len(enforceable_in_batch)):
                c1 = enforceable_in_batch[i]
                c2 = enforceable_in_batch[j]
                if c1.supersedes != c2.source_ref and c2.supersedes != c1.source_ref:
                    s1 = c1.statement.lower()
                    s2 = c2.statement.lower()
                    negation_words = {"never", "not", "must not", "prohibit", "disallow"}
                    has_neg_1 = any(nw in s1 for nw in negation_words)
                    has_neg_2 = any(nw in s2 for nw in negation_words)
                    words1 = set(re.findall(r"\w{4,}", s1))
                    words2 = set(re.findall(r"\w{4,}", s2))
                    common = words1.intersection(words2)
                    if len(common) >= 2 and (has_neg_1 != has_neg_2):
                        # Both are preserved to represent the conflict
                        summary.conflicts_detected += 1

        # 8. Persist in Supabase and Hindsight
        bank_id = get_repo_bank_id(repository_id, repository_full_name)
        retained_memories: List[CanonicalMemory] = []

        hindsight_configured = self.memory.is_configured
        if not hindsight_configured:
            summary.hindsight_status = "unavailable"
            summary.hindsight_error = "Hindsight memory service is not configured (API key or URL missing)."

        for cand in candidates_to_persist:
            # Create in Supabase
            created_row = await self.supabase.create_engineering_memory(
                repository_id=repository_id,
                statement=cand.statement,
                type=cand.type.value,
                authority=cand.authority.value,
                rationale=cand.rationale,
                scope=cand.scope,
                status=cand.status.value,
                source_type=cand.source_type,
                source_ref=cand.source_ref,
                source_url=cand.source_url,
                source_pr_number=cand.source_pr_number,
                source_commit_sha=cand.source_commit_sha,
                supersedes=cand.supersedes,
                superseded_by=cand.superseded_by,
                repository_full_name=repository_full_name,
                identity_hash=cand.identity_hash,
                evidence_items=[cand.source_ref],
            )
            canonical_mem = CanonicalMemory(**created_row)
            retained_memories.append(canonical_mem)
            summary.candidates_retained += 1

            # Retain in Hindsight if configured
            if hindsight_configured:
                doc_id = f"doc-bootstrap-{repository_id[:8]}-{cand.identity_hash[:12]}"
                content_text = (
                    f"Type: {cand.type.value}\n"
                    f"Authority: {cand.authority.value}\n"
                    f"Claim: {cand.statement}\n"
                    f"Rationale: {cand.rationale}\n"
                    f"Scope: {cand.scope}\n"
                    f"Source: {cand.source_ref}"
                )
                try:
                    await self.memory.aretain(
                        content=content_text,
                        bank_id=bank_id,
                        context=f"Repository: {repository_full_name}, Source: {cand.source_ref}",
                        document_id=doc_id,
                        metadata={
                            "repository_id": repository_id,
                            "repository_full_name": repository_full_name,
                            "type": cand.type.value,
                            "authority": cand.authority.value,
                            "scope": cand.scope,
                            "source_ref": cand.source_ref,
                            "source_pr_number": str(cand.source_pr_number or ""),
                            "status": cand.status.value,
                            "identity_hash": cand.identity_hash or "",
                        },
                        tags=[
                            cand.type.value.lower(),
                            cand.authority.value.lower(),
                            cand.source_type.lower(),
                        ],
                    )
                    summary.hindsight_retained += 1
                except ConfigurationError:
                    summary.hindsight_status = "unavailable"
                except Exception as e:
                    logger.error("Hindsight retention failed during bootstrap for '%s': %s", cand.statement, e)
                    summary.hindsight_status = "failed"
                    summary.hindsight_error = str(e)

        summary.memories = retained_memories
        return summary


def get_bootstrap_service() -> HistoricalBootstrapService:
    """Returns HistoricalBootstrapService configured with current service singletons."""
    return HistoricalBootstrapService(
        github=get_github_service(),
        supabase=get_supabase_service(),
        memory=get_hindsight_service(),
        groq=get_groq_service(),
    )


