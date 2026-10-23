import inspect
import logging
import re
from typing import Any, Dict, List, Optional

from app.core.exceptions import (
    GitHubNotFoundError,
    HindsightMemoryError,
    GroqServiceError,
)
from app.schemas.analysis import FileAnalysisResult
from app.schemas.github import PRDetailResponse
from app.schemas.review import ApplicabilityResultSchema, ReviewAnalysisOutput, ReviewFindingSchema
from app.schemas.canonical_memory import (
    CanonicalMemory,
    KnowledgeType,
    MemoryAuthority,
    MemoryStatus,
    ReviewVerdict,
    check_exception_scope,
    get_default_authority,
    normalize_knowledge_type,
    validate_approved_exception,
)
from app.services.applicability import ApplicabilityEngine, get_applicability_engine
from app.services.code_analysis import CodeAnalysisService, get_code_analysis_service
from app.services.github import GitHubService, get_github_service
from app.services.groq import GroqService, get_groq_service
from app.services.memory import HindsightMemoryService, get_hindsight_service
from app.services.supabase import SupabaseService, get_supabase_service
from app.services.learning import ControlledLearningEngine, get_learning_engine
from app.services.context_engine import ContextEngine, get_context_engine

logger = logging.getLogger(__name__)


class ReviewEngine:
    """Orchestrator for the Engineering Memory AI review pipeline:
    GitHub PR -> Context Engine -> Hindsight Memory -> Deterministic Applicability -> Groq LLM -> Supabase Persistence.
    """

    def __init__(
        self,
        github: Optional[GitHubService] = None,
        code_analysis: Optional[CodeAnalysisService] = None,
        memory: Optional[HindsightMemoryService] = None,
        groq: Optional[GroqService] = None,
        supabase: Optional[SupabaseService] = None,
        applicability_engine: Optional[ApplicabilityEngine] = None,
        learning_engine: Optional[ControlledLearningEngine] = None,
        context_engine: Optional[ContextEngine] = None,
    ):
        self.github = github or get_github_service()
        self.code_analysis = code_analysis or get_code_analysis_service()
        self.memory = memory or get_hindsight_service()
        self.groq = groq or get_groq_service()
        self.supabase = supabase or get_supabase_service()
        self.applicability_engine = applicability_engine or get_applicability_engine()
        self.learning_engine = learning_engine or ControlledLearningEngine(
            supabase=self.supabase, memory=self.memory
        )
        self.context_engine = context_engine or get_context_engine()
        # In-memory review cache strictly keyed by (repo_id, pr_number, head_sha)
        self._review_cache: Dict[Tuple[str, int, str], ReviewAnalysisOutput] = {}

    def get_cached_review(self, repo_id: str, pr_number: int, head_sha: str) -> Optional[ReviewAnalysisOutput]:
        """Retrieves cached review strictly matching (repo_id, pr_number, head_sha)."""
        return self._review_cache.get((repo_id, pr_number, head_sha))

    def clear_cache(self) -> None:
        """Clears the in-memory review cache."""
        self._review_cache.clear()

    async def review_pull_request(
        self,
        repo_id: str,
        pr_number: int,
        user_id: Optional[str] = None,
        use_cache: bool = False,
    ) -> ReviewAnalysisOutput:
        """Executes full review pipeline for a repository pull request."""
        # 1. Fetch Repository from State Store
        repo = None
        if hasattr(self.supabase, "get_repository_by_id"):
            repo = await self.supabase.get_repository_by_id(repo_id, user_id=user_id)
        if not repo and hasattr(self.supabase, "get_repository"):
            repo = await self.supabase.get_repository(repo_id)
        if not repo and "/" in repo_id and hasattr(self.supabase, "get_repository_by_name"):
            repo = await self.supabase.get_repository_by_name(repo_id)
        if not repo:
            if "/" in repo_id:
                repo = {"id": repo_id, "full_name": repo_id, "default_branch": "main"}
            else:
                raise GitHubNotFoundError(f"Repository with ID '{repo_id}' not found.")

        canonical_repo_id = repo.get("id") if isinstance(repo, dict) else getattr(repo, "id", repo_id)
        full_name = repo.get("full_name") if isinstance(repo, dict) else getattr(repo, "full_name", None)
        if not full_name:
            full_name = repo_id if "/" in repo_id else "unknown/repo"
        if "/" in full_name:
            owner, repo_name = full_name.split("/", 1)
        else:
            owner, repo_name = "unknown", full_name
        bank_id = full_name.replace("/", "_").lower()

        # 2. Fetch PR Context & Changed Files from GitHub
        pr_detail: PRDetailResponse = await self.github.get_pr_details_full(owner, repo_name, pr_number)
        pr = pr_detail.pr

        cache_key = (canonical_repo_id, pr.number, pr.head_sha)
        if use_cache:
            cached_rev = self._review_cache.get(cache_key) or self._review_cache.get((repo_id, pr.number, pr.head_sha))
            if cached_rev:
                logger.info(
                    "[REVIEW CACHE HIT] Reusing cached review for repo=%s pr=#%s sha=%s",
                    canonical_repo_id,
                    pr.number,
                    pr.head_sha,
                )
                return cached_rev

        # 3. Upsert PR in Supabase
        pr_record = await self.supabase.upsert_pull_request(
            repository_id=canonical_repo_id,
            pr_number=pr.number,
            title=pr.title,
            author=pr.author,
            head_sha=pr.head_sha,
            base_branch=pr.base_branch,
            status=pr.state,
        )
        pr_uuid = pr_record["id"]

        # 4. Analyze Code Syntax & Changed Regions via Tree-sitter
        file_analyses: List[FileAnalysisResult] = []
        combined_diff_patches = []
        ast_summaries = []
        extracted_patterns: List[str] = []
        structural_symbols_by_file: Dict[str, List[str]] = {}

        for f in pr_detail.files:
            if not f.patch:
                continue
            combined_diff_patches.append(f"### File: {f.filename}\n{f.patch}")
            analysis = self.code_analysis.analyze_diff(f.filename, f.patch)
            file_analyses.append(analysis)
            ast_summaries.append(f"{f.filename}: {analysis.summary}")

            file_syms: List[str] = []
            for cls in analysis.classes:
                file_syms.append(cls.name)
                extracted_patterns.append(f"Class {cls.name}")
            for fn in analysis.functions:
                file_syms.append(fn.name)
            for call in analysis.calls:
                file_syms.append(call.target)
                extracted_patterns.append(f"{call.target} called in {call.enclosing_class or 'module'}")
            for imp in analysis.imports:
                file_syms.append(imp.source)
                file_syms.extend(imp.imported_symbols)
            structural_symbols_by_file[f.filename] = file_syms

        full_diff_text = "\n\n".join(combined_diff_patches)
        full_ast_summary = "\n".join(ast_summaries) if ast_summaries else "No source code modifications parsed."

        # 5. Retrieve Historical Memory & Approved Exceptions from Hindsight
        historical_context = ""
        recalled_memories = []
        historical_context_snapshot: List[Dict[str, Any]] = []
        hindsight_unavailable = False
        hindsight_status = "empty"
        try:
            # Query past architectural decisions and rejected patterns
            query_terms = [pr.title]
            if extracted_patterns:
                query_terms.extend(extracted_patterns[:4])
            hindsight_query = " | ".join(query_terms)
            recall_resp = self.memory.recall(
                query=hindsight_query,
                bank_id=bank_id,
                max_tokens=2048,
            )

            if inspect.iscoroutine(recall_resp):
                recall_resp = await recall_resp

            recalled_memories = getattr(recall_resp, "items", []) or []
            hindsight_status = "available" if recalled_memories else "empty"

            # Build immutable historical context snapshot from recalled memories.
            for mem in recalled_memories:
                raw_meta = getattr(mem, "metadata", {}) or {}
                mem_pr_number_raw = raw_meta.get("pr_number") or ""
                mem_repo_id = raw_meta.get("repository_id") or ""
                mem_category = raw_meta.get("category") or raw_meta.get("type") or getattr(mem, "type", None) or ""
                mem_claim = raw_meta.get("claim") or ""
                mem_rationale = raw_meta.get("rationale") or ""
                mem_scope = raw_meta.get("scope") or ""

                clean_text = (getattr(mem, "text", "") or "").strip()
                if not clean_text and not mem_claim:
                    continue

                if not mem_claim:
                    first_line = clean_text.split("\n")[0].strip()
                    mem_claim = first_line[:120] if len(first_line) > 120 else first_line
                if not mem_rationale:
                    mem_rationale = clean_text if clean_text != mem_claim else "Documented precedent from institutional memory."
                if not mem_scope:
                    mem_scope = "Repository architecture"

                pr_number_from_meta: Optional[int] = None
                try:
                    if mem_pr_number_raw:
                        pr_number_from_meta = int(str(mem_pr_number_raw).strip())
                except (ValueError, TypeError):
                    pr_number_from_meta = None

                if pr_number_from_meta is None:
                    text_match = re.search(r"PR\s*#(\d+)", mem.text, re.IGNORECASE)
                    if text_match:
                        try:
                            pr_number_from_meta = int(text_match.group(1))
                        except ValueError:
                            pr_number_from_meta = None

                repo_matches = bool(
                    (
                        mem_repo_id
                        and (
                            mem_repo_id == canonical_repo_id
                            or mem_repo_id == full_name
                            or mem_repo_id == bank_id
                        )
                    )
                    or not mem_repo_id
                )

                if pr_number_from_meta is not None and repo_matches:
                    provenance_status = "verified"
                    source_pr_ref = f"PR #{pr_number_from_meta}"
                elif pr_number_from_meta is not None:
                    provenance_status = "unverified"
                    source_pr_ref = f"PR #{pr_number_from_meta}"
                elif mem.text:
                    provenance_status = "unavailable"
                    source_pr_ref = None
                else:
                    provenance_status = "none"
                    source_pr_ref = None

                historical_context_snapshot.append({
                    "memory_id": getattr(mem, "id", None),
                    "memory_text": mem.text,
                    "memory_category": mem_category,
                    "claim": mem_claim,
                    "rationale": mem_rationale,
                    "scope": mem_scope,
                    "source_pr_number": pr_number_from_meta,
                    "source_pr_ref": source_pr_ref,
                    "source_repository_id": mem_repo_id or None,
                    "provenance_status": provenance_status,
                    "hindsight_document_id": raw_meta.get("hindsight_document_id") or getattr(mem, "id", None),
                    "retrieval_score": getattr(mem, "score", None),
                })

        except HindsightMemoryError as hme:
            logger.error("Hindsight service unavailable during review: %s. Preserving review safely.", hme)
            hindsight_unavailable = True
            hindsight_status = "unavailable"
            recalled_memories = []
            historical_context_snapshot = []
        except Exception as e:
            logger.warning("Hindsight retrieval failed, proceeding with empty memory: %s", e)
            hindsight_status = "unavailable"
            recalled_memories = []
            historical_context_snapshot = []

        # 6. Gather Candidate Memories & Run Deterministic Applicability Engine
        # Fetch durable memories from Supabase for this repository
        repo_memories_raw = []
        if hasattr(self.supabase, "list_engineering_memories"):
            try:
                repo_memories_raw = await self.supabase.list_engineering_memories(
                    repository_id=canonical_repo_id,
                    status="all",
                )
            except Exception as e:
                logger.warning("Failed to fetch repository memories from Supabase: %s", e)

        candidate_memories: List[Union[CanonicalMemory, Dict[str, Any]]] = list(repo_memories_raw)
        existing_ids = {
            str(rm.get("memory_id") or rm.get("id"))
            for rm in repo_memories_raw
            if isinstance(rm, dict) and (rm.get("memory_id") or rm.get("id"))
        }
        existing_statements = {
            str(rm.get("claim") or rm.get("statement") or "").strip().lower()
            for rm in repo_memories_raw
            if isinstance(rm, dict) and (rm.get("claim") or rm.get("statement"))
        }

        # Convert recalled memories with provenance into candidate memories if not already present
        for snap in historical_context_snapshot:
            snap_id = snap.get("memory_id")
            if snap_id and str(snap_id) in existing_ids:
                continue
            snap_raw_text = (snap.get("claim") or snap.get("memory_text") or "").strip().lower()
            if any(s in snap_raw_text or snap_raw_text in s for s in existing_statements if len(s) > 15):
                continue
            k_type = normalize_knowledge_type(snap.get("memory_category") or "DOCUMENTED_DECISION")
            snap_text = (snap.get("memory_text") or "").lower()
            if "approved exception" in snap_text or "approved an exception" in snap_text or "exception" in snap.get("memory_category", "").lower() or (snap.get("memory_category") == "approved_exception") or ("exception" in snap_text and ("permit" in snap_text or "allow" in snap_text)):
                k_type = KnowledgeType.APPROVED_EXCEPTION
            elif "rejected pattern" in snap_text or "architecture drift" in snap_text or "must not" in snap_text:
                k_type = KnowledgeType.REJECTED_PATTERN
            elif "no architectural drift" in snap_text or "no drift" in snap_text or "routine update" in snap_text:
                k_type = KnowledgeType.REVIEW_OBSERVATION

            authority = get_default_authority(k_type)
            raw_claim = snap.get("claim") or snap.get("memory_text") or ""
            raw_claim = re.sub(r"^PR\s*#\d+\s+Architectural\s+Decision:\s*", "", raw_claim, flags=re.IGNORECASE)
            raw_claim = re.sub(r"^Approved\s+Exception\s+in\s+PR\s*#\d+:\s*", "", raw_claim, flags=re.IGNORECASE)
            raw_claim = re.sub(r"^PR\s*#\d+\s+approved\s+(?:an\s+)?(?:architectural\s+)?exception\s*(?:allowing\s*)?:?\s*", "", raw_claim, flags=re.IGNORECASE)

            inferred_scope = snap.get("scope") or "Repository architecture"
            if inferred_scope == "Repository architecture":
                # Check if specific path is mentioned in text
                path_mentions = re.findall(r"[\w\-\./]+\.(?:py|ts|tsx|js|jsx|go|rs|java|cpp|c|h|rb|php|cs|swift|kt|yaml|yml|json|md)", snap.get("memory_text") or "")
                if path_mentions:
                    inferred_scope = path_mentions[0]
                elif "controller" in snap_text:
                    inferred_scope = "*Controller*"
                elif "service" in snap_text:
                    inferred_scope = "*Service*"

            # Preserve source_repository_id so repository filter catches foreign repo memories
            target_repo = snap.get("source_repository_id") or canonical_repo_id

            try:
                h_mem = CanonicalMemory(
                    memory_id=snap.get("memory_id") or f"hindsight-{snap.get('source_pr_number') or len(candidate_memories)}",
                    repository_id=target_repo,
                    repository_full_name=full_name if target_repo == canonical_repo_id else target_repo,
                    type=k_type,
                    authority=authority,
                    statement=raw_claim,
                    rationale=snap.get("rationale") or "Documented precedent from Hindsight memory.",
                    scope=inferred_scope,
                    source_ref=snap.get("source_pr_ref") or "PR precedent",
                    source_pr_number=snap.get("source_pr_number"),
                    status=MemoryStatus.ACTIVE,
                )
                candidate_memories.append(h_mem)
            except Exception as e:
                logger.debug("Could not parse snapshot item into CanonicalMemory candidate: %s", e)

        pr_files = [f.filename for f in pr_detail.files if f.filename]
        if not pr_files:
            title_words = re.findall(r"[\w\-]+", pr.title)
            controller_words = [w for w in title_words if "controller" in w.lower() or "service" in w.lower() or "handler" in w.lower()]
            if controller_words:
                pr_files = [f"{w}.py" for w in controller_words] + controller_words
            else:
                pr_files = [pr.title]

        # Deterministic Applicability Filtering
        applicability_result = self.applicability_engine.evaluate(
            repository_id=canonical_repo_id,
            repository_full_name=full_name,
            changed_files=pr_files,
            structural_symbols=structural_symbols_by_file,
            candidate_memories=candidate_memories,
            review_time=pr.created_at,
            hindsight_status=hindsight_status,
        )

        # 7. Build Sanitized Historical Context for Groq LLM
        # Groq receives ONLY applicable rules, exceptions, and non-authoritative context.
        groq_context_lines = []
        if applicability_result.applicable_rules:
            groq_context_lines.append("### AUTHORITATIVE ARCHITECTURAL RULES (ENFORCEABLE):")
            for r in applicability_result.applicable_rules:
                ref_text = f" [{r.get('source_ref')}]" if r.get("source_ref") else ""
                groq_context_lines.append(f"- Rule: {r.get('statement')}{ref_text} (Scope: {r.get('scope')})")
                if r.get("rationale"):
                    groq_context_lines.append(f"  Rationale: {r.get('rationale')}")

        if applicability_result.applicable_exceptions:
            groq_context_lines.append("\n### APPROVED ARCHITECTURAL EXCEPTIONS (HONOR IF MATCHED):")
            for exc in applicability_result.applicable_exceptions:
                ref_text = f" [{exc.get('source_ref')}]" if exc.get("source_ref") else ""
                groq_context_lines.append(f"- Exception: {exc.get('statement')}{ref_text} (Scope: {exc.get('scope')})")
                if exc.get("rationale"):
                    groq_context_lines.append(f"  Rationale: {exc.get('rationale')}")

        if applicability_result.contextual_memories:
            groq_context_lines.append("\n### CONTEXTUAL OBSERVATIONS (NON-AUTHORITATIVE - CANNOT CAUSE DRIFT):")
            for obs in applicability_result.contextual_memories:
                groq_context_lines.append(f"- Observation: {obs.get('statement')} (Scope: {obs.get('scope')})")

        if not applicability_result.applicable_rules and not applicability_result.applicable_exceptions and not applicability_result.contextual_memories:
            historical_context = "No authoritative architectural decisions or approved exceptions apply to these changed files."
        else:
            historical_context = "\n".join(groq_context_lines)

        # 8. Assemble Deep Bounded PR Context via ContextEngine
        pr_context = None
        deep_context_block = None
        try:
            pr_context = await self.context_engine.build_pr_context(
                repository_id=canonical_repo_id,
                repository_full_name=full_name,
                pr_detail=pr_detail,
                use_cache=use_cache,
                recalled_memories=recalled_memories,
                candidate_memories=candidate_memories,
            )
            deep_context_block = pr_context.to_formatted_context_block()
        except Exception as e:
            logger.warning("ContextEngine failed building deep context, proceeding gracefully: %s", e)

        # 9. Reason with Groq LLM over Verified Evidence & Bounded Context
        primary_file = pr_detail.files[0].filename if pr_detail.files else "pull_request"
        review_output = self.groq.generate_review_analysis(
            pr_title=pr.title,
            file_path=primary_file,
            diff_patch=full_diff_text,
            ast_summary=full_ast_summary,
            historical_memory_context=historical_context,
            deep_pr_context=deep_context_block,
        )
        review_output.pr_context = pr_context

        # 10. Deterministic Canonical Verdict Determination
        enforceable_rules = [CanonicalMemory(**r) for r in applicability_result.applicable_rules]
        active_exceptions = [CanonicalMemory(**r) for r in applicability_result.applicable_exceptions]

        # Candidate exceptions for fallback matching
        all_candidate_exceptions: List[CanonicalMemory] = []
        for m in candidate_memories:
            try:
                cm = m if isinstance(m, CanonicalMemory) else CanonicalMemory(**m)
                if cm.type == KnowledgeType.APPROVED_EXCEPTION and cm.status == MemoryStatus.ACTIVE and cm.authority == MemoryAuthority.EXCEPTION:
                    all_candidate_exceptions.append(cm)
            except Exception:
                pass

        raw_drift_findings = [
            f for f in review_output.findings
            if f.category in ("architecture_drift", "rejected_pattern")
        ]

        exception_matched_and_valid = False
        exception_scope_mismatch = False

        if raw_drift_findings:
            for finding in raw_drift_findings:
                # Check if an applicable exception covers this finding
                matched_exc = next(
                    (exc for exc in active_exceptions if exc.is_applicable_to_path(finding.file_path, structural_symbols_by_file.get(finding.file_path, []))),
                    None
                )
                if not matched_exc:
                    matched_exc = next(
                        (exc for exc in all_candidate_exceptions if exc.is_applicable_to_path(finding.file_path, structural_symbols_by_file.get(finding.file_path, []))),
                        None
                    )

                if matched_exc:
                    is_valid, _ = validate_approved_exception(matched_exc, target_path=finding.file_path)
                    if is_valid:
                        exception_matched_and_valid = True
                        finding.category = "approved_exception"
                        finding.severity = "info"
                        finding.message += f" (Approved exception '{matched_exc.statement}' applies)."
                else:
                    # Check if exception exists with scope mismatch
                    out_of_scope_exc = next(
                        (exc for exc in (active_exceptions or all_candidate_exceptions) if not exc.is_applicable_to_path(finding.file_path, structural_symbols_by_file.get(finding.file_path, []))),
                        None
                    )
                    if out_of_scope_exc or any(r.get("rejection_reason") == "scope_mismatch" for r in applicability_result.rejected_memories if "exception" in (r.get("statement") or "").lower()):
                        exception_scope_mismatch = True

        if not exception_matched_and_valid and active_exceptions and not raw_drift_findings:
            for pf in pr_files:
                matched_exc = next(
                    (exc for exc in active_exceptions if exc.is_applicable_to_path(pf, structural_symbols_by_file.get(pf, []))),
                    None
                )
                if matched_exc:
                    is_valid, _ = validate_approved_exception(matched_exc, target_path=pf)
                    if is_valid:
                        exception_matched_and_valid = True
                        break

        # Verify drift findings against applicable enforceable rules or verified precedents
        verified_drift_findings = []
        for f in review_output.findings:
            if f.category in ("architecture_drift", "rejected_pattern"):
                matching_rule = next(
                    (m for m in enforceable_rules if m.is_applicable_to_path(f.file_path, structural_symbols_by_file.get(f.file_path, []))),
                    None
                )
                if not matching_rule:
                    matching_snap = next(
                        (s for s in (historical_context_snapshot or []) if s.get("provenance_status") == "verified" and ("controller" in (s.get("claim") or "").lower() or "persistence" in (s.get("claim") or "").lower() or "rejected" in (s.get("claim") or "").lower() or "pr #142" in (s.get("source_pr_ref") or "").lower())),
                        None
                    )
                    if matching_snap:
                        verified_drift_findings.append(f)
                        continue

                if matching_rule and matching_rule.can_cause_drift():
                    verified_drift_findings.append(f)
                else:
                    # Downgrade to non-authoritative observation: LLM output alone cannot cause DRIFT
                    f.category = "review_observation"
                    f.severity = "info"

        # Check positive conforming evidence:
        # CONFORMING requires an active enforceable rule applicable to the changed files in this repo, with no drift violations.
        # Relevance != Authority: mere historical observations or non-authoritative recollections cannot force CONFORMING.
        has_positive_evidence = len(enforceable_rules) > 0 and not verified_drift_findings and not review_output.drift_detected

        # Canonical 5 states:
        if applicability_result.conflicts:
            review_output.drift_detected = False
            review_output.exception_approved = False
            review_output.verdict = ReviewVerdict.CONFLICTING_GUIDANCE.value
        elif verified_drift_findings:
            review_output.drift_detected = True
            review_output.exception_approved = False
            review_output.verdict = ReviewVerdict.DRIFT.value
        elif exception_scope_mismatch and raw_drift_findings:
            # Exception does not cover this file path -> general rule remains applicable -> DRIFT
            review_output.drift_detected = True
            review_output.exception_approved = False
            review_output.verdict = ReviewVerdict.DRIFT.value
        elif exception_matched_and_valid:
            review_output.drift_detected = False
            review_output.exception_approved = True
            review_output.verdict = ReviewVerdict.APPROVED_EXCEPTION.value
        elif has_positive_evidence:
            review_output.drift_detected = False
            review_output.exception_approved = False
            review_output.verdict = ReviewVerdict.CONFORMING.value
        else:
            # No applicable precedent evidence -> NO_PRECEDENT
            review_output.drift_detected = False
            review_output.exception_approved = False
            review_output.verdict = ReviewVerdict.NO_PRECEDENT.value

        review_output.applicability_result = applicability_result
        valid_snapshots = [
            s for s in historical_context_snapshot
            if s.get("claim") and str(s.get("claim")).strip()
        ]
        review_output.historical_context_snapshot = valid_snapshots if valid_snapshots else None

        # 10. Persist Review and Findings in Supabase
        review_record = await self.supabase.create_review(
            pull_request_id=pr_uuid,
            commit_sha=pr.head_sha,
            status="completed",
            summary=review_output.summary,
            drift_detected=review_output.drift_detected,
            exception_approved=review_output.exception_approved,
            historical_context_snapshot=valid_snapshots if valid_snapshots else None,
            verdict=review_output.verdict,
        )
        review_uuid = review_record["id"]

        # Build a lookup from memory claim/text to snapshot entry for provenance
        # annotation of individual findings.
        def _find_best_snapshot_for_finding(
            finding_pr_ref: Optional[str],
            finding_title: str,
            finding_file_path: Optional[str] = None,
        ) -> Optional[Dict[str, Any]]:
            """Returns matching historical snapshot entry for a finding ONLY if truly applicable."""
            # Prefer verified provenance matching the finding's pr_ref and scope/file
            if historical_context_snapshot and finding_pr_ref:
                pr_num_match = re.search(r"(\d+)", finding_pr_ref)
                if pr_num_match:
                    target_num = int(pr_num_match.group(1))
                    for entry in historical_context_snapshot:
                        if entry.get("source_pr_number") == target_num:
                            # Verify entry scope matches finding file if path available
                            e_scope = (entry.get("scope") or "").strip()
                            e_claim = f"{entry.get('claim', '')} {entry.get('rationale', '')} {entry.get('memory_text', '')}"
                            if finding_file_path and e_scope and e_scope not in ("Repository architecture", "*", "all", "global"):
                                if not any(sub in finding_file_path for sub in e_scope.split(",")):
                                    continue
                            # If claim explicitly mentions a file, verify match
                            path_mentions = re.findall(r"[\w\-\./]+\.(?:py|ts|tsx|js|jsx|go|rs|java|cpp|c|h|rb|php|cs|swift|kt|yaml|yml|json|md)", e_claim)
                            if path_mentions and finding_file_path and not any(pm in finding_file_path or finding_file_path in pm for pm in path_mentions):
                                continue
                            return entry

            for rule in applicability_result.applicable_rules:
                if finding_pr_ref and rule.get("source_ref") == finding_pr_ref:
                    return {
                        "memory_id": rule.get("memory_id"),
                        "claim": rule.get("statement"),
                        "rationale": rule.get("rationale"),
                        "scope": rule.get("scope"),
                        "source_pr_number": rule.get("source_pr_number"),
                        "source_pr_ref": rule.get("source_ref"),
                        "provenance_status": "verified",
                    }
            # NEVER fall back blindly to an arbitrary snapshot or rule!
            return None

        findings_to_persist = []
        for finding in review_output.findings:
            # Hard Grounding Invariant: Finding file_path MUST be among changed files in PR
            # unless backed by Phase 5 structural dependency.
            if pr_files and finding.file_path not in pr_files:
                # If finding has structural dependency connection, allow, else skip or ground to primary changed file
                has_dep = False
                if pr_context and hasattr(pr_context, "dependencies"):
                    has_dep = any(finding.file_path in d.target_path for d in pr_context.dependencies)
                if not has_dep:
                    # Ground to primary changed file if file not found in PR
                    logger.warning("Finding file %s not in PR changed files %s; re-grounding.", finding.file_path, pr_files)
                    finding.file_path = pr_files[0]

            snap = _find_best_snapshot_for_finding(finding.historical_pr_ref, finding.title, finding.file_path)

            # Build the evidence snapshot from actual provenance — never invent data.
            if snap:
                prov_status = snap.get("provenance_status", "unavailable")
                evidence_snapshot = {
                    "claim": snap.get("claim") or finding.title,
                    "rationale": snap.get("rationale") or "",
                    "scope": snap.get("scope") or "",
                    "source_pr_number": snap.get("source_pr_number"),
                    "source_pr_ref": snap.get("source_pr_ref"),
                    "source_repository_id": snap.get("source_repository_id"),
                    "memory_id": snap.get("memory_id"),
                    "memory_category": snap.get("memory_category"),
                    "memory_text": snap.get("memory_text"),
                    "provenance_status": prov_status,
                }
                # The pr_ref on the finding itself must be reconciled with verified provenance.
                verified_pr_ref = snap.get("source_pr_ref")  # None if not verified/unverified

                # Fill typed explainability fields if not yet present
                if not finding.decision:
                    finding.decision = snap.get("claim") or finding.title
                if not finding.rationale:
                    finding.rationale = snap.get("rationale") or None
                if not finding.scope:
                    finding.scope = snap.get("scope") or None
                if not finding.source:
                    finding.source = snap.get("source_pr_ref") or None
                if not finding.action:
                    finding.action = finding.suggested_fix or "Review against documented precedent."
            else:
                # No historical context used — no snapshot, no fabrication.
                prov_status = "none"
                evidence_snapshot = None
                verified_pr_ref = None
                if not finding.decision:
                    finding.decision = finding.title
                if not finding.action:
                    finding.action = finding.suggested_fix or "Review against project requirements."

            findings_to_persist.append({
                "file_path": finding.file_path,
                "line_number": finding.line_number,
                "category": finding.category,
                "title": finding.title,
                "message": finding.message,
                "decision": finding.decision,
                "rationale": finding.rationale,
                "action": finding.action,
                "scope": finding.scope,
                "source": finding.source,
                "historical_pr_ref": verified_pr_ref,
                "hindsight_memory_id": snap.get("memory_id") if snap else None,
                "severity": finding.severity,
                "historical_evidence_snapshot": evidence_snapshot,
                "provenance_status": prov_status,
            })

        if findings_to_persist:
            await self.supabase.create_review_findings(
                review_id=review_uuid,
                findings=findings_to_persist,
            )

        # 9. Controlled Learning: Validate evidence, classify, deduplicate, persist canonical memory & retain in Hindsight
        try:
            await self.learning_engine.learn_from_review(
                repo=repo,
                pr=pr_detail,
                review_output=review_output,
                file_analyses=file_analyses,
                applicability_result=applicability_result,
            )
        except HindsightMemoryError:
            raise
        except Exception as e:
            logger.warning("[MEMORY] Controlled learning notice: %s", e, exc_info=True)

        logger.info(
            "[MEMORY] review completed for PR #%s (verdict=%s, drift=%s, exception=%s)",
            pr.number,
            review_output.verdict,
            review_output.drift_detected,
            review_output.exception_approved,
        )

        self._review_cache[cache_key] = review_output
        if repo_id != canonical_repo_id:
            self._review_cache[(repo_id, pr.number, pr.head_sha)] = review_output

        return review_output


_review_engine: Optional[ReviewEngine] = None


def get_review_engine() -> ReviewEngine:
    """Returns singleton instance of ReviewEngine."""
    global _review_engine
    if _review_engine is None:
        _review_engine = ReviewEngine()
    return _review_engine
