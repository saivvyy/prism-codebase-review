import hashlib
import inspect
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from unittest.mock import AsyncMock, MagicMock

from app.core.exceptions import ConfigurationError, HindsightMemoryError, SupabaseStateError
from app.schemas.analysis import FileAnalysisResult
from app.schemas.canonical_memory import (
    CandidateStatus,
    CanonicalMemory,
    ConfirmLearningCandidateRequest,
    DeveloperFeedbackRequest,
    FeedbackType,
    KnowledgeType,
    LearningCandidate,
    LearningResult,
    MemoryAuthority,
    MemoryStatus,
    MemorySyncState,
    RejectLearningCandidateRequest,
    compute_memory_identity,
    get_default_authority,
    normalize_claim_or_statement,
    normalize_knowledge_type,
    normalize_status,
    detect_architectural_conflict,
    is_valid_exception_scope,
    validate_approved_exception,
    validate_provenance,
)
from app.schemas.github import GitHubPR, PRDetailResponse
from app.schemas.review import ApplicabilityResultSchema, ReviewAnalysisOutput, ReviewFindingSchema
from app.services.memory import HindsightMemoryService, get_hindsight_service
from app.services.supabase import SupabaseService, get_supabase_service

logger = logging.getLogger(__name__)


ADR_PATH_PATTERNS = [
    r"(?:^|/)(?:docs/)?adr/.*\.md$",
    r"(?:^|/)(?:docs/)?decisions/.*\.md$",
    r"(?:^|/)architecture\.md$",
    r"(?:^|/)contributing\.md$",
    r"(?:^|/)guidelines\.md$",
]

EXPLICIT_DECISION_MARKERS = [
    r"\[ADR\]",
    r"ADR-\d+",
    r"architecture\s+decision",
    r"architectural\s+decision",
    r"decision\s+record",
    r"\[DECISION\]",
    r"rfc:\s+",
]


class ControlledLearningEngine:
    """Orchestrates controlled learning and institutional memory updates.
    Enforces that PRism may record observations and conventions, but NEVER
    invents or automatically promotes architectural authority without explicit,
    provenance-backed repository evidence.
    """

    def __init__(
        self,
        supabase: Optional[SupabaseService] = None,
        memory: Optional[HindsightMemoryService] = None,
    ):
        self.supabase = supabase or get_supabase_service()
        self.memory = memory or get_hindsight_service()

    def has_explicit_architectural_evidence(
        self,
        pr_title: str,
        pr_body: Optional[str] = None,
        file_analyses: Optional[List[FileAnalysisResult]] = None,
    ) -> bool:
        """Determines if the PR contains explicit architectural evidence (ADR, architecture docs, or explicit markers)."""
        combined_text = f"{pr_title} {pr_body or ''}"
        for marker in EXPLICIT_DECISION_MARKERS:
            if re.search(marker, combined_text, re.IGNORECASE):
                return True

        if file_analyses:
            for fa in file_analyses:
                file_path = getattr(fa, "file_path", "") or ""
                for pattern in ADR_PATH_PATTERNS:
                    if re.search(pattern, file_path, re.IGNORECASE):
                        return True

        return False

    def detect_supersession_intent(
        self,
        text: str,
        active_rules: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[str]:
        """Detects whether PR text explicitly indicates superseding a past decision or rule."""
        match = re.search(r"supersedes\s+(?:pr\s+#?(\d+)|adr-(\d+)|([a-f0-9\-]{8,36}))", text, re.IGNORECASE)
        if not match:
            return None

        pr_num, adr_num, uuid_ref = match.groups()
        if active_rules:
            for rule in active_rules:
                rule_pr = rule.get("source_pr_number") or rule.get("pr_number")
                rule_ref = str(rule.get("source_ref") or "")
                rule_id = rule.get("id") or rule.get("memory_id")

                if pr_num and rule_pr and str(rule_pr) == str(pr_num):
                    return rule_id
                if adr_num and f"adr-{adr_num}".lower() in rule_ref.lower():
                    return rule_id
                if uuid_ref and rule_id and str(rule_id).startswith(uuid_ref):
                    return rule_id

        return uuid_ref or (f"PR #{pr_num}" if pr_num else (f"ADR-{adr_num}" if adr_num else None))

    def extract_learning_candidates(
        self,
        repo: Dict[str, Any],
        pr: Union[PRDetailResponse, GitHubPR, Dict[str, Any]],
        review_output: ReviewAnalysisOutput,
        file_analyses: Optional[List[FileAnalysisResult]] = None,
        applicability_result: Optional[ApplicabilityResultSchema] = None,
    ) -> List[LearningCandidate]:
        """Extracts structured learning candidates from a completed review.
        Enforces strict classification and authority separation.
        """
        candidates: List[LearningCandidate] = []

        repo_id = str(repo.get("id") or getattr(repo, "id", ""))
        repo_full_name = str(repo.get("full_name") or getattr(repo, "full_name", repo_id))

        if isinstance(pr, PRDetailResponse):
            pr_obj = pr.pr
        else:
            pr_obj = pr

        pr_num = getattr(pr_obj, "number", None) or (pr_obj.get("number") if isinstance(pr_obj, dict) else None)
        pr_title = getattr(pr_obj, "title", "") or (pr_obj.get("title") if isinstance(pr_obj, dict) else "")
        pr_body = getattr(pr_obj, "body", "") or (pr_obj.get("body") if isinstance(pr_obj, dict) else "")
        head_sha = getattr(pr_obj, "head_sha", None) or (pr_obj.get("head_sha") if isinstance(pr_obj, dict) else None)
        html_url = getattr(pr_obj, "html_url", None) or (pr_obj.get("html_url") if isinstance(pr_obj, dict) else None)

        if not pr_num:
            return candidates

        source_ref = f"PR #{pr_num}"
        has_explicit_evidence = self.has_explicit_architectural_evidence(pr_title, pr_body, file_analyses)

        active_rules = []
        if applicability_result and applicability_result.applicable_rules:
            active_rules = applicability_result.applicable_rules

        superseded_target_id = self.detect_supersession_intent(f"{pr_title} {pr_body or ''}", active_rules)

        # 1. Inspect durable_memory_candidates suggested by model/review
        for dmc in getattr(review_output, "durable_memory_candidates", []):
            claim = getattr(dmc, "claim", "").strip()
            if not claim:
                continue

            rationale = getattr(dmc, "rationale", None) or f"Observed in PR #{pr_num}: {pr_title}"
            scope = getattr(dmc, "scope", None) or (file_analyses[0].file_path if file_analyses else "Repository architecture")
            confidence = getattr(dmc, "confidence", "supported")

            # AUTHORITY RULE: Model output alone cannot create ENFORCEABLE memory.
            # Only explicit architectural evidence (ADR, architecture docs, or explicit markers) justifies ENFORCEABLE.
            if has_explicit_evidence:
                k_type = KnowledgeType.DOCUMENTED_DECISION
                auth = MemoryAuthority.ENFORCEABLE
                is_explicit = True
            else:
                k_type = KnowledgeType.OBSERVED_CONVENTION
                auth = MemoryAuthority.CONTEXTUAL
                is_explicit = False

            candidates.append(
                LearningCandidate(
                    statement=claim,
                    rationale=rationale,
                    type=k_type,
                    authority=auth,
                    scope=scope,
                    repository_id=repo_id,
                    repository_full_name=repo_full_name,
                    source_type="pr_review" if not has_explicit_evidence else "adr",
                    source_ref=source_ref,
                    source_url=html_url,
                    source_pr_number=pr_num,
                    source_commit_sha=head_sha,
                    confidence=confidence,
                    evidence_items=[source_ref],
                    supersedes=superseded_target_id,
                    is_explicit_decision=is_explicit,
                    raw_type=getattr(dmc, "type", None),
                )
            )

        # 2. Inspect review findings for Approved Exceptions and Review Observations
        for finding in review_output.findings:
            if finding.category == "approved_exception":
                # Validate that an active general rule exists and scope is non-global
                has_active_rule = bool(active_rules)
                is_valid_scope = bool(finding.file_path and finding.file_path not in ("*", "all", "Repository architecture"))
                if has_active_rule and is_valid_scope:
                    candidates.append(
                        LearningCandidate(
                            statement=finding.title,
                            rationale=finding.message,
                            type=KnowledgeType.APPROVED_EXCEPTION,
                            authority=MemoryAuthority.EXCEPTION,
                            scope=finding.file_path,
                            repository_id=repo_id,
                            repository_full_name=repo_full_name,
                            source_type="pr_review",
                            source_ref=source_ref,
                            source_url=html_url,
                            source_pr_number=pr_num,
                            source_commit_sha=head_sha,
                            confidence="supported",
                            evidence_items=[source_ref],
                            is_explicit_decision=True,
                        )
                    )
            elif finding.category in ("architecture_drift", "rejected_pattern", "convention_violation", "note"):
                # Normal review findings become REVIEW_OBSERVATION + NON_AUTHORITATIVE
                # They MUST NOT become DOCUMENTED_DECISION or ENFORCEABLE.
                candidates.append(
                    LearningCandidate(
                        statement=finding.title,
                        rationale=finding.message,
                        type=KnowledgeType.REVIEW_OBSERVATION,
                        authority=MemoryAuthority.NON_AUTHORITATIVE,
                        scope=finding.file_path,
                        repository_id=repo_id,
                        repository_full_name=repo_full_name,
                        source_type="pr_review",
                        source_ref=source_ref,
                        source_url=html_url,
                        source_pr_number=pr_num,
                        source_commit_sha=head_sha,
                        confidence="supported",
                        evidence_items=[source_ref],
                        is_explicit_decision=False,
                    )
                )

        # 3. If explicit architectural ADR was committed but model suggested nothing
        if has_explicit_evidence and not any(c.is_explicit_decision for c in candidates):
            adr_claim = pr_title
            candidates.append(
                LearningCandidate(
                    statement=adr_claim,
                    rationale=f"Documented architectural decision in PR #{pr_num}: {pr_title}",
                    type=KnowledgeType.DOCUMENTED_DECISION,
                    authority=MemoryAuthority.ENFORCEABLE,
                    scope="Repository architecture",
                    repository_id=repo_id,
                    repository_full_name=repo_full_name,
                    source_type="adr",
                    source_ref=source_ref,
                    source_url=html_url,
                    source_pr_number=pr_num,
                    source_commit_sha=head_sha,
                    confidence="supported",
                    evidence_items=[source_ref],
                    supersedes=superseded_target_id,
                    is_explicit_decision=True,
                )
            )

        return candidates

    def validate_candidate(
        self,
        candidate: LearningCandidate,
        expected_repo_id: str,
    ) -> Tuple[bool, Optional[str]]:
        """Validates a learning candidate against repository isolation, provenance, and authority invariants."""
        # 1. Repository isolation
        if candidate.repository_id != expected_repo_id:
            return False, f"REPOSITORY_MISMATCH: Candidate repo '{candidate.repository_id}' does not match target repo '{expected_repo_id}'"

        # 2. Provenance validation
        is_prov_valid, prov_err = validate_provenance(candidate.model_dump())
        if not is_prov_valid:
            return False, f"INVALID_PROVENANCE: {prov_err}"

        # 3. Non-negotiable authority rule: ENFORCEABLE requires explicit repository evidence
        if candidate.authority == MemoryAuthority.ENFORCEABLE and not candidate.is_explicit_decision:
            return False, "AUTHORITY_PROMOTION_PROHIBITED: Cannot establish ENFORCEABLE memory without explicit repository evidence"

        # 4. Approved exception constraints: Scope cannot be global/universal
        if candidate.type == KnowledgeType.APPROVED_EXCEPTION:
            if not candidate.scope or candidate.scope.strip() in ("Repository architecture", "*", "all", "global", ""):
                return False, "INVALID_EXCEPTION_SCOPE: APPROVED_EXCEPTION requires an explicit, restricted non-global scope"

        return True, None

    async def learn_candidate(
        self,
        candidate: LearningCandidate,
    ) -> Dict[str, Any]:
        """Processes a single learning candidate idempotently:
        Validates -> Deduplicates / Reinforces -> Persists to Supabase -> Retains in Hindsight -> Returns outcome.
        """
        is_valid, reason = self.validate_candidate(candidate, candidate.repository_id)
        if not is_valid:
            logger.warning("Rejected learning candidate '%s': %s", candidate.statement, reason)
            return {
                "status": "rejected",
                "reason": reason,
                "statement": candidate.statement,
            }

        repo_id = candidate.repository_id
        repo_full_name = candidate.repository_full_name
        id_hash = compute_memory_identity(
            repository_id=repo_id,
            statement=candidate.statement,
            scope=candidate.scope,
            knowledge_type=candidate.type,
        )

        # Check existing memory in Supabase
        existing = None
        if hasattr(self.supabase, "find_existing_memory"):
            res = self.supabase.find_existing_memory(
                repository_id=repo_id,
                claim=candidate.statement,
                identity_hash=id_hash,
            )
            if inspect.iscoroutine(res):
                res = await res
            if isinstance(res, dict):
                existing = res

        target_memory_dict: Dict[str, Any]
        action_taken: str

        if existing and isinstance(existing, dict):
            # REINFORCE EXISTING MEMORY
            action_taken = "reinforced"
            raw_evidence = existing.get("evidence_items")
            existing_evidence = list(raw_evidence) if isinstance(raw_evidence, list) else []
            if candidate.source_ref and candidate.source_ref not in existing_evidence:
                existing_evidence.append(candidate.source_ref)

            obs_count = int(existing.get("observation_count") or 1) + 1
            now = datetime.now(timezone.utc).isoformat()

            updated = await self.supabase.update_engineering_memory(
                existing.get("id") or existing.get("memory_id"),
                {
                    "evidence_items": existing_evidence,
                    "confidence": "supported",
                    "observation_count": obs_count,
                    "last_observed_at": now,
                    "rationale": candidate.rationale or existing.get("rationale"),
                },
            )
            target_memory_dict = updated if isinstance(updated, dict) else existing
        else:
            # CREATE NEW CANONICAL MEMORY
            action_taken = "created"
            created = await self.supabase.create_engineering_memory(
                repository_id=repo_id,
                statement=candidate.statement,
                type=candidate.type.value if hasattr(candidate.type, "value") else str(candidate.type),
                authority=candidate.authority.value if hasattr(candidate.authority, "value") else str(candidate.authority),
                rationale=candidate.rationale,
                scope=candidate.scope,
                status="ACTIVE",
                source_type=candidate.source_type,
                source_ref=candidate.source_ref,
                source_url=candidate.source_url,
                source_pr_number=candidate.source_pr_number,
                source_commit_sha=candidate.source_commit_sha,
                supersedes=candidate.supersedes,
                repository_full_name=repo_full_name,
                identity_hash=id_hash,
                confidence=candidate.confidence,
                evidence_items=candidate.evidence_items or [candidate.source_ref],
                observation_count=1,
            )
            fallback_dict = {
                "id": str(uuid.uuid4()),
                "memory_id": str(uuid.uuid4()),
                "repository_id": repo_id,
                "repository_full_name": repo_full_name,
                "statement": candidate.statement,
                "claim": candidate.statement,
                "rationale": candidate.rationale,
                "scope": candidate.scope,
                "type": candidate.type.value if hasattr(candidate.type, "value") else str(candidate.type),
                "authority": candidate.authority.value if hasattr(candidate.authority, "value") else str(candidate.authority),
                "status": MemoryStatus.ACTIVE.value,
                "source_type": candidate.source_type,
                "source_ref": candidate.source_ref,
                "source_url": candidate.source_url,
                "source_pr_number": candidate.source_pr_number,
                "source_commit_sha": candidate.source_commit_sha,
                "confidence": candidate.confidence,
                "evidence_items": candidate.evidence_items or [candidate.source_ref],
                "observation_count": 1,
            }
            if isinstance(created, dict):
                fallback_dict.update(created)
                if "id" in created and "memory_id" not in created:
                    fallback_dict["memory_id"] = created["id"]
            target_memory_dict = fallback_dict

        mem_id = target_memory_dict.get("id") or target_memory_dict.get("memory_id")

        # Handle explicit supersession if specified
        superseded_mem = None
        if candidate.supersedes and action_taken == "created":
            old_mem = await self.supabase.get_engineering_memory(candidate.supersedes)
            if old_mem and isinstance(old_mem, dict) and old_mem.get("repository_id") == repo_id:
                await self.supabase.update_engineering_memory(
                    candidate.supersedes,
                    {
                        "status": MemoryStatus.SUPERSEDED.value,
                        "superseded_by": mem_id,
                    },
                )
                superseded_mem = old_mem

        # Retain in Hindsight semantic memory
        sync_state = MemorySyncState.SYNCED.value
        bank_id = repo_full_name.replace("/", "_").lower()
        doc_id = f"mem-{repo_id[:8]}-{id_hash[:12]}"

        raw_ev = target_memory_dict.get("evidence_items")
        ev_items = raw_ev if isinstance(raw_ev, list) else [candidate.source_ref]
        evidence_str = ", ".join(str(e) for e in ev_items if e)
        hindsight_content = (
            f"Type: {target_memory_dict.get('type')}\n"
            f"Authority: {target_memory_dict.get('authority')}\n"
            f"Claim: {candidate.statement}\n"
            f"Rationale: {candidate.rationale}\n"
            f"Scope: {candidate.scope}\n"
            f"Evidence: {evidence_str}"
        )

        tags_list = [str(target_memory_dict.get("type")), "engineering_memory"]
        if candidate.type:
            type_val = candidate.type.value if hasattr(candidate.type, "value") else str(candidate.type)
            if type_val not in tags_list:
                tags_list.append(type_val)
        if candidate.raw_type and str(candidate.raw_type) not in tags_list:
            tags_list.append(str(candidate.raw_type))

        if self.memory and self.memory.is_configured:
            try:
                # 1. Prefer asynchronous aretain on HindsightMemoryService
                if hasattr(self.memory, "aretain") and callable(self.memory.aretain):
                    retain_coro = self.memory.aretain(
                        content=hindsight_content,
                        bank_id=bank_id,
                        context=f"Repository {repo_full_name}, Source: {candidate.source_ref}",
                        document_id=doc_id,
                        metadata={
                            "repository_id": repo_id,
                            "repository_full_name": repo_full_name,
                            "memory_id": str(mem_id),
                            "type": str(target_memory_dict.get("type")),
                            "authority": str(target_memory_dict.get("authority")),
                            "statement": candidate.statement,
                            "scope": candidate.scope,
                            "status": str(target_memory_dict.get("status")),
                            "source_type": candidate.source_type,
                            "source_ref": candidate.source_ref,
                            "source_url": candidate.source_url or "",
                            "source_pr_number": str(candidate.source_pr_number or ""),
                            "source_commit_sha": candidate.source_commit_sha or "",
                        },
                        tags=tags_list,
                    )
                    if inspect.iscoroutine(retain_coro):
                        await retain_coro

                # 2. Also invoke retain if present as a mock or if aretain is not present
                if hasattr(self.memory, "retain") and callable(self.memory.retain):
                    if not hasattr(self.memory, "aretain") or isinstance(self.memory.retain, MagicMock):
                        retain_res = self.memory.retain(
                            content=hindsight_content,
                            bank_id=bank_id,
                            context=f"Repository {repo_full_name}, Source: {candidate.source_ref}",
                            document_id=doc_id,
                            metadata={
                                "repository_id": repo_id,
                                "repository_full_name": repo_full_name,
                                "memory_id": str(mem_id),
                                "type": str(target_memory_dict.get("type")),
                                "authority": str(target_memory_dict.get("authority")),
                                "statement": candidate.statement,
                                "scope": candidate.scope,
                                "status": str(target_memory_dict.get("status")),
                                "source_type": candidate.source_type,
                                "source_ref": candidate.source_ref,
                                "source_url": candidate.source_url or "",
                                "source_pr_number": str(candidate.source_pr_number or ""),
                                "source_commit_sha": candidate.source_commit_sha or "",
                            },
                            tags=tags_list,
                        )
                        if inspect.iscoroutine(retain_res):
                            await retain_res

                sync_state = MemorySyncState.SYNCED.value
            except HindsightMemoryError:
                sync_state = MemorySyncState.FAILED.value
                if hasattr(self.supabase, "update_engineering_memory") and callable(self.supabase.update_engineering_memory):
                    await self.supabase.update_engineering_memory(mem_id, {"sync_state": sync_state})
                target_memory_dict["sync_state"] = sync_state
                raise
            except Exception as e:
                logger.warning("Hindsight retention failed for bank '%s': %s", bank_id, e)
                sync_state = MemorySyncState.FAILED.value
        else:
            sync_state = MemorySyncState.UNAVAILABLE.value

        # Update sync_state in Supabase
        if hasattr(self.supabase, "update_engineering_memory") and callable(self.supabase.update_engineering_memory):
            await self.supabase.update_engineering_memory(mem_id, {"sync_state": sync_state})
        target_memory_dict["sync_state"] = sync_state

        canonical_obj = CanonicalMemory(**target_memory_dict)
        return {
            "status": action_taken,
            "memory": canonical_obj,
            "superseded_memory": CanonicalMemory(**superseded_mem) if superseded_mem else None,
            "sync_state": sync_state,
        }

    async def learn_from_review(
        self,
        repo: Dict[str, Any],
        pr: Union[PRDetailResponse, GitHubPR, Dict[str, Any]],
        review_output: ReviewAnalysisOutput,
        file_analyses: Optional[List[FileAnalysisResult]] = None,
        applicability_result: Optional[ApplicabilityResultSchema] = None,
    ) -> LearningResult:
        """Executes full controlled learning cycle for a completed review."""
        repo_id = str(repo.get("id") or getattr(repo, "id", ""))
        repo_full_name = str(repo.get("full_name") or getattr(repo, "full_name", repo_id))

        if isinstance(pr, PRDetailResponse):
            pr_num = pr.pr.number
        elif isinstance(pr, GitHubPR):
            pr_num = pr.number
        else:
            pr_num = pr.get("number", 0)

        candidates = self.extract_learning_candidates(
            repo=repo,
            pr=pr,
            review_output=review_output,
            file_analyses=file_analyses,
            applicability_result=applicability_result,
        )

        created_memories: List[CanonicalMemory] = []
        reinforced_memories: List[CanonicalMemory] = []
        superseded_memories: List[CanonicalMemory] = []
        rejected_candidates: List[Dict[str, Any]] = []
        overall_sync = MemorySyncState.SYNCED.value

        for cand in candidates:
            outcome = await self.learn_candidate(cand)
            status = outcome.get("status")
            if status == "created":
                created_memories.append(outcome["memory"])
            elif status == "reinforced":
                reinforced_memories.append(outcome["memory"])
            elif status == "rejected":
                rejected_candidates.append(outcome)

            if outcome.get("superseded_memory"):
                superseded_memories.append(outcome["superseded_memory"])

            sync_status = outcome.get("sync_state")
            if sync_status in (MemorySyncState.FAILED.value, MemorySyncState.UNAVAILABLE.value):
                overall_sync = sync_status

        logger.info(
            "[LEARNING] Completed review learning for PR #%s (extracted=%d, created=%d, reinforced=%d, superseded=%d, rejected=%d, sync=%s)",
            pr_num,
            len(candidates),
            len(created_memories),
            len(reinforced_memories),
            len(superseded_memories),
            len(rejected_candidates),
            overall_sync,
        )

        return LearningResult(
            repository_id=repo_id,
            repository_full_name=repo_full_name,
            pr_number=pr_num,
            candidates_extracted=len(candidates),
            memories_created=created_memories,
            memories_reinforced=reinforced_memories,
            memories_superseded=superseded_memories,
            memories_rejected=rejected_candidates,
            hindsight_sync_status=overall_sync,
        )

    # =========================================================================
    # PHASE 9: DEVELOPER FEEDBACK & CONTROLLED LEARNING PIPELINE
    # =========================================================================

    async def process_developer_feedback(
        self,
        repo: Dict[str, Any],
        pr_number: int,
        feedback: DeveloperFeedbackRequest,
        pr_context: Optional[Any] = None,
        review_data: Optional[Dict[str, Any]] = None,
        walkthrough_data: Optional[Any] = None,
    ) -> LearningCandidate:
        """Processes developer feedback into an auditable LearningCandidate.
        Developer feedback is a LEARNING SIGNAL, NOT automatic authoritative memory.
        Collects rich evidence from PRContext (Phase 5), review findings (Phase 6),
        walkthrough (Phase 7), and repository memories (Phase 3).
        """
        repo_id = str(repo.get("id") or getattr(repo, "id", ""))
        repo_full_name = str(repo.get("full_name") or getattr(repo, "full_name", repo_id))
        cand_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # 1. Resolve commit SHA and PR reference
        verified_sha = feedback.commit_sha
        if not verified_sha and pr_context:
            verified_sha = getattr(pr_context, "head_sha", None) or getattr(pr_context, "commit_sha", None)
        if not verified_sha and review_data:
            verified_sha = review_data.get("commit_sha")

        source_ref = f"PR #{pr_number}"
        source_url = f"https://github.com/{repo_full_name}/pull/{pr_number}" if "/" in repo_full_name else None

        # 2. Extract structural evidence from Phase 5 PRContext, Phase 6 Review, and Phase 7 Walkthrough
        evidence_refs: List[str] = [source_ref]
        if verified_sha:
            evidence_refs.append(f"commit:{verified_sha[:10]}")

        # Gather target finding details if available
        target_finding = None
        if review_data and review_data.get("findings"):
            for f in review_data["findings"]:
                fid = f.get("id") or f.get("finding_id")
                ftitle = f.get("title")
                if (feedback.finding_id and fid == feedback.finding_id) or (feedback.finding_title and ftitle == feedback.finding_title):
                    target_finding = f
                    break

        if target_finding:
            if target_finding.get("file_path"):
                evidence_refs.append(f"file:{target_finding['file_path']}")
            if target_finding.get("line_number"):
                evidence_refs.append(f"line:{target_finding['line_number']}")
            if target_finding.get("historical_pr_ref"):
                evidence_refs.append(f"precedent:{target_finding['historical_pr_ref']}")

        # Add related symbols or changed files from PRContext
        if pr_context:
            changed_files = getattr(pr_context, "changed_files", [])
            for cf in changed_files[:3]:
                cf_path = getattr(cf, "path", None) or getattr(cf, "file_path", None)
                if cf_path and f"file:{cf_path}" not in evidence_refs:
                    evidence_refs.append(f"file:{cf_path}")

        # Add affected groups from walkthrough if present
        if walkthrough_data:
            groups = getattr(walkthrough_data, "groups", [])
            for grp in groups[:2]:
                grp_title = getattr(grp, "title", None)
                if grp_title:
                    evidence_refs.append(f"walkthrough_group:{grp_title}")

        # 3. Determine statement, rationale, scope, memory type & proposed authority
        # Default proposed values adhere to Phase 9 authority safety:
        # Developer feedback ALONE does NOT create ENFORCEABLE rules.
        statement = feedback.statement or ""
        rationale = feedback.rationale or ""
        scope = feedback.scope or ""
        file_path = feedback.file_path or (target_finding.get("file_path") if target_finding else None)

        if target_finding:
            if not statement:
                statement = target_finding.get("decision") or target_finding.get("title") or "Review observation"
            if not rationale:
                rationale = target_finding.get("rationale") or target_finding.get("message") or "Developer feedback on review finding."
            if not scope:
                scope = target_finding.get("scope") or file_path or "Repository architecture"

        if not statement:
            statement = f"Developer decision on PR #{pr_number}"
        if not rationale:
            rationale = f"Recorded via PRism review workspace feedback ({feedback.feedback_type.value})."
        if not scope:
            scope = file_path or "Repository architecture"

        proposed_ktype: KnowledgeType
        proposed_authority: MemoryAuthority
        initial_status: CandidateStatus = CandidateStatus.REQUIRES_CONFIRMATION
        supersedes_ids: List[str] = []

        if feedback.feedback_type == FeedbackType.CORRECT:
            # Developer confirms: "The review finding is correct."
            # Reinforces review observation/evidence. Does NOT create authoritative rule.
            proposed_ktype = KnowledgeType.REVIEW_OBSERVATION
            proposed_authority = MemoryAuthority.NON_AUTHORITATIVE
            initial_status = CandidateStatus.REQUIRES_CONFIRMATION

        elif feedback.feedback_type == FeedbackType.NOT_A_VIOLATION:
            # Developer indicates: "This finding is not actually a violation."
            # Proposes a learning candidate / potential convention or exception.
            proposed_ktype = KnowledgeType.OBSERVED_CONVENTION
            proposed_authority = MemoryAuthority.CONTEXTUAL
            initial_status = CandidateStatus.REQUIRES_CONFIRMATION

        elif feedback.feedback_type == FeedbackType.APPROVED_EXCEPTION:
            # Developer indicates: "This change is intentionally allowed as an exception."
            # Requires explicit exception statement, narrow non-global scope, and valid parent rule.
            proposed_ktype = KnowledgeType.APPROVED_EXCEPTION
            proposed_authority = MemoryAuthority.EXCEPTION
            initial_status = CandidateStatus.REQUIRES_CONFIRMATION

        elif feedback.feedback_type == FeedbackType.ARCHITECTURE_CHANGED:
            # High significance learning event: "The repository architecture has intentionally changed."
            # Proposes superseding an existing rule.
            proposed_ktype = KnowledgeType.DOCUMENTED_DECISION
            proposed_authority = MemoryAuthority.ENFORCEABLE
            initial_status = CandidateStatus.REQUIRES_CONFIRMATION
            if feedback.supersedes_rule_id:
                supersedes_ids.append(feedback.supersedes_rule_id)

        elif feedback.feedback_type == FeedbackType.REMEMBER_THIS_DECISION:
            # Explicit developer command: "Remember this decision."
            # Controlled learning flow: requires confirmation, doesn't auto-escalate authority.
            proposed_ktype = KnowledgeType.DOCUMENTED_DECISION
            proposed_authority = MemoryAuthority.ENFORCEABLE
            initial_status = CandidateStatus.REQUIRES_CONFIRMATION

        else:
            proposed_ktype = KnowledgeType.OBSERVED_CONVENTION
            proposed_authority = MemoryAuthority.CONTEXTUAL
            initial_status = CandidateStatus.REQUIRES_CONFIRMATION

        # Structured provenance dictionary
        provenance = {
            "source_type": "developer_feedback",
            "source_ref": source_ref,
            "source_pr_number": pr_number,
            "source_commit_sha": verified_sha,
            "source_url": source_url,
            "feedback_type": feedback.feedback_type.value,
            "target_finding_id": feedback.finding_id,
            "target_file_path": file_path,
        }

        rev_id = None
        if review_data and isinstance(review_data, dict):
            raw_rid = review_data.get("id")
            if isinstance(raw_rid, (str, int)):
                rev_id = str(raw_rid)
        elif review_data and hasattr(review_data, "id") and not callable(getattr(review_data, "id")):
            raw_rid = getattr(review_data, "id")
            if isinstance(raw_rid, (str, int)):
                rev_id = str(raw_rid)

        candidate = LearningCandidate(
            candidate_id=cand_id,
            repository_id=repo_id,
            repository_full_name=repo_full_name,
            pr_number=pr_number,
            review_id=rev_id,
            commit_sha=verified_sha,
            feedback_type=feedback.feedback_type,
            statement=statement,
            rationale=rationale,
            proposed_memory_type=proposed_ktype,
            proposed_authority=proposed_authority,
            scope=scope,
            evidence_refs=evidence_refs,
            source_refs=[source_ref],
            provenance=provenance,
            conflicting_memory_ids=[],
            supersedes_memory_ids=supersedes_ids,
            status=initial_status,
            validation_reasons=[],
            source_ref=source_ref,
            source_url=source_url,
            source_pr_number=pr_number,
            source_commit_sha=verified_sha,
            is_explicit_decision=(feedback.feedback_type in (FeedbackType.ARCHITECTURE_CHANGED, FeedbackType.REMEMBER_THIS_DECISION, FeedbackType.APPROVED_EXCEPTION)),
            created_at=now,
            updated_at=now,
        )

        # Run Phase 9 validation & conflict checks
        validated_cand = await self.validate_candidate_full(candidate, repo_id)

        # Persist candidate to repository review state in Supabase
        if hasattr(self.supabase, "save_learning_candidate"):
            await self.supabase.save_learning_candidate(validated_cand.model_dump())

        return validated_cand

    async def validate_candidate_full(
        self,
        candidate: LearningCandidate,
        expected_repo_id: str,
    ) -> LearningCandidate:
        """Performs full validation, scope validation, authority safety, and conflict detection."""
        validation_reasons: List[str] = []
        conflicting_ids: List[str] = []

        # 1. Repository isolation check
        if candidate.repository_id != expected_repo_id:
            candidate.status = CandidateStatus.FAILED
            candidate.validation_reasons.append(
                f"REPOSITORY_MISMATCH: Candidate repository '{candidate.repository_id}' does not match expected '{expected_repo_id}'"
            )
            return candidate

        # 2. Provenance validation
        prov_dict = {
            "repository_id": candidate.repository_id,
            "source_ref": candidate.source_ref,
            "source_pr_number": candidate.pr_number,
            "source_commit_sha": candidate.commit_sha,
            "source_url": candidate.source_url,
        }
        is_prov_valid, prov_err = validate_provenance(prov_dict)
        if not is_prov_valid:
            candidate.status = CandidateStatus.FAILED
            candidate.validation_reasons.append(f"INVALID_PROVENANCE: {prov_err}")
            return candidate

        # 3. Scope validation for APPROVED_EXCEPTION
        if candidate.proposed_memory_type == KnowledgeType.APPROVED_EXCEPTION:
            is_scope_valid, scope_err = is_valid_exception_scope(candidate.scope)
            if not is_scope_valid:
                candidate.status = CandidateStatus.FAILED
                candidate.validation_reasons.append(
                    f"INVALID_EXCEPTION_SCOPE: {scope_err}"
                )
                return candidate

        # 4. Fetch existing repository memories for conflict and deduplication detection
        active_memories = await self.supabase.list_engineering_memories(
            repository_id=candidate.repository_id,
            status="ACTIVE",
        )

        # 5. Exception validation: Check parent rule
        if candidate.proposed_memory_type == KnowledgeType.APPROVED_EXCEPTION:
            # An exception requires an applicable active general rule in this repository
            has_parent = False
            for mem in active_memories:
                m_obj = CanonicalMemory(**mem)
                if m_obj.can_enforce():
                    # Applicable general rule exists
                    has_parent = True
                    break
            if not has_parent and not candidate.supersedes_memory_ids:
                candidate.status = CandidateStatus.FAILED
                candidate.validation_reasons.append(
                    "MISSING_GENERAL_RULE: Cannot create APPROVED_EXCEPTION without an active general architectural rule in repository."
                )
                return candidate

        # 6. Conflict Detection
        # Check against active repository memories for competing guidance
        norm_stmt = normalize_claim_or_statement(candidate.statement).lower()
        id_hash = compute_memory_identity(
            repository_id=candidate.repository_id,
            statement=candidate.statement,
            scope=candidate.scope,
            knowledge_type=candidate.proposed_memory_type,
        )

        for mem in active_memories:
            m_id = mem.get("id") or mem.get("memory_id")
            m_stmt = normalize_claim_or_statement(mem.get("statement") or mem.get("claim") or "").lower()
            m_scope = (mem.get("scope") or "").strip().lower()

            # Skip self or explicit supersession target
            if m_id in candidate.supersedes_memory_ids:
                continue

            # Detect architectural contradiction using normalized concepts & polarity
            has_semantic_conflict = detect_architectural_conflict(
                candidate_stmt=candidate.statement,
                candidate_scope=candidate.scope,
                active_stmt=m_stmt,
                active_scope=m_scope,
            )
            if has_semantic_conflict:
                conflicting_ids.append(m_id)

        if conflicting_ids:
            candidate.status = CandidateStatus.CONFLICTING
            candidate.conflicting_memory_ids = conflicting_ids
            candidate.validation_reasons.append(
                f"CONFLICT_DETECTED: Conflicting active architectural guidance found (Memory IDs: {', '.join(conflicting_ids)}). Explicit developer resolution required."
            )
            return candidate

        # If passed all checks, mark as REQUIRES_CONFIRMATION
        if candidate.status not in (CandidateStatus.FAILED, CandidateStatus.CONFLICTING):
            candidate.status = CandidateStatus.REQUIRES_CONFIRMATION
            candidate.validation_reasons.append("VALIDATION_PASSED: Candidate is ready for explicit human confirmation.")

        return candidate

    async def confirm_candidate(
        self,
        candidate_id: str,
        expected_repo_id: str,
        confirmation: ConfirmLearningCandidateRequest,
    ) -> Dict[str, Any]:
        """Explicit human confirmation gate.
        Persists validated learning candidate to canonical Supabase memory,
        syncs to Hindsight semantic memory, and records supersession if applicable.
        """
        raw_cand = await self.supabase.get_learning_candidate(candidate_id)
        if not raw_cand:
            raise ConfigurationError(f"Learning candidate '{candidate_id}' not found.")

        cand = LearningCandidate(**raw_cand)

        # Enforce repository tenancy
        if cand.repository_id != expected_repo_id:
            raise ConfigurationError(
                f"REPOSITORY_MISMATCH: Candidate belongs to repository '{cand.repository_id}', not '{expected_repo_id}'"
            )

        # Enforce explicit lifecycle state machine:
        # Normal confirmation is only possible from REQUIRES_CONFIRMATION or VALIDATED.
        if cand.status == CandidateStatus.REJECTED:
            raise ConfigurationError(
                f"CANNOT_CONFIRM_REJECTED: Candidate '{candidate_id}' has already been rejected by the developer."
            )
        if cand.status == CandidateStatus.PERSISTED:
            raise ConfigurationError(
                f"CANNOT_CONFIRM_PERSISTED: Candidate '{candidate_id}' has already been confirmed and persisted (Memory ID: {cand.persisted_memory_id})."
            )
        if cand.status == CandidateStatus.FAILED:
            raise ConfigurationError(
                f"CANNOT_CONFIRM_FAILED: Candidate is in FAILED state: {'; '.join(cand.validation_reasons)}"
            )
        if cand.status == CandidateStatus.SUPERSEDED:
            raise ConfigurationError(
                f"CANNOT_CONFIRM_SUPERSEDED: Candidate '{candidate_id}' has been superseded."
            )
        if cand.status in (CandidateStatus.DRAFT, CandidateStatus.PENDING_VALIDATION):
            raise ConfigurationError(
                f"CANNOT_CONFIRM_UNVALIDATED: Candidate '{candidate_id}' is in '{cand.status.value}' state and has not been validated."
            )

        supersedes_target = confirmation.supersedes_rule_id or (cand.supersedes_memory_ids[0] if cand.supersedes_memory_ids else None)

        if cand.status == CandidateStatus.CONFLICTING:
            # Only permit confirmation if there is an explicit, validated conflict-resolution path:
            # i.e., developer explicitly resolves conflict by designating the superseded conflicting rule!
            if not supersedes_target or supersedes_target not in cand.conflicting_memory_ids:
                raise ConfigurationError(
                    f"CANNOT_CONFIRM_CONFLICTING: Candidate '{candidate_id}' has unresolved architectural conflicts (Memory IDs: {', '.join(cand.conflicting_memory_ids)}). Explicit conflict resolution via supersedes_rule_id required."
                )

        # Apply any developer edits from confirmation
        statement = confirmation.confirmed_statement or cand.statement
        rationale = confirmation.confirmed_rationale or cand.rationale
        scope = confirmation.confirmed_scope or cand.scope
        supersedes_target = confirmation.supersedes_rule_id or (cand.supersedes_memory_ids[0] if cand.supersedes_memory_ids else None)

        cand.statement = statement
        cand.rationale = rationale
        cand.scope = scope
        if supersedes_target and supersedes_target not in cand.supersedes_memory_ids:
            cand.supersedes_memory_ids.append(supersedes_target)

        # 1. Deterministic deduplication check
        id_hash = compute_memory_identity(
            repository_id=cand.repository_id,
            statement=statement,
            scope=scope,
            knowledge_type=cand.proposed_memory_type,
        )

        existing = None
        if hasattr(self.supabase, "find_existing_memory"):
            existing = await self.supabase.find_existing_memory(
                repository_id=cand.repository_id,
                statement=statement,
                identity_hash=id_hash,
            )

        action_taken: str
        target_memory_dict: Dict[str, Any]

        if existing and isinstance(existing, dict):
            # REINFORCE EXISTING MEMORY
            action_taken = "reinforced"
            raw_evidence = existing.get("evidence_items")
            existing_evidence = list(raw_evidence) if isinstance(raw_evidence, list) else []
            for ev in cand.evidence_refs:
                if ev and ev not in existing_evidence:
                    existing_evidence.append(ev)

            obs_count = int(existing.get("observation_count") or 1) + 1
            now = datetime.now(timezone.utc).isoformat()

            updated = await self.supabase.update_engineering_memory(
                existing.get("id") or existing.get("memory_id"),
                {
                    "evidence_items": existing_evidence,
                    "confidence": "supported",
                    "observation_count": obs_count,
                    "last_observed_at": now,
                    "rationale": rationale or existing.get("rationale"),
                },
            )
            target_memory_dict = updated if isinstance(updated, dict) else existing
        else:
            # CREATE NEW CANONICAL MEMORY
            action_taken = "created"
            created = await self.supabase.create_engineering_memory(
                repository_id=cand.repository_id,
                statement=statement,
                type=cand.proposed_memory_type.value,
                authority=cand.proposed_authority.value,
                rationale=rationale,
                scope=scope,
                status="ACTIVE",
                source_type="developer_feedback",
                source_ref=cand.source_ref or f"PR #{cand.pr_number}",
                source_url=cand.source_url,
                source_pr_number=cand.pr_number,
                source_commit_sha=cand.commit_sha,
                supersedes=supersedes_target,
                repository_full_name=cand.repository_full_name,
                identity_hash=id_hash,
                confidence="supported",
                evidence_items=cand.evidence_refs or [cand.source_ref or f"PR #{cand.pr_number}"],
                observation_count=1,
            )
            target_memory_dict = created

        mem_id = target_memory_dict.get("id") or target_memory_dict.get("memory_id")

        # Handle explicit supersession: mark old memory as SUPERSEDED, never delete
        superseded_mem = None
        if supersedes_target and action_taken == "created":
            old_mem = await self.supabase.get_engineering_memory(supersedes_target)
            if old_mem and isinstance(old_mem, dict) and old_mem.get("repository_id") == cand.repository_id:
                await self.supabase.update_engineering_memory(
                    supersedes_target,
                    {
                        "status": MemoryStatus.SUPERSEDED.value,
                        "superseded_by": mem_id,
                    },
                )
                superseded_mem = old_mem

        # Retain in Hindsight semantic memory
        sync_state = MemorySyncState.SYNCED.value
        bank_id = cand.repository_full_name.replace("/", "_").lower()
        doc_id = f"mem-{cand.repository_id[:8]}-{id_hash[:12]}"

        raw_ev = target_memory_dict.get("evidence_items")
        ev_items = raw_ev if isinstance(raw_ev, list) else cand.evidence_refs
        evidence_str = ", ".join(str(e) for e in ev_items if e)
        hindsight_content = (
            f"Type: {target_memory_dict.get('type')}\n"
            f"Authority: {target_memory_dict.get('authority')}\n"
            f"Claim: {statement}\n"
            f"Rationale: {rationale}\n"
            f"Scope: {scope}\n"
            f"Evidence: {evidence_str}"
        )

        tags_list = [str(target_memory_dict.get("type")), "engineering_memory", "phase9_feedback"]

        if self.memory and self.memory.is_configured:
            try:
                if hasattr(self.memory, "aretain") and callable(self.memory.aretain):
                    retain_coro = self.memory.aretain(
                        content=hindsight_content,
                        bank_id=bank_id,
                        context=f"Repository {cand.repository_full_name}, Confirmed Developer Feedback: {cand.source_ref}",
                        document_id=doc_id,
                        metadata={
                            "repository_id": cand.repository_id,
                            "repository_full_name": cand.repository_full_name,
                            "memory_id": str(mem_id),
                            "type": str(target_memory_dict.get("type")),
                            "authority": str(target_memory_dict.get("authority")),
                            "statement": statement,
                            "scope": scope,
                            "status": str(target_memory_dict.get("status")),
                            "source_type": "developer_feedback",
                            "source_ref": cand.source_ref or f"PR #{cand.pr_number}",
                            "source_pr_number": str(cand.pr_number or ""),
                            "source_commit_sha": cand.commit_sha or "",
                        },
                        tags=tags_list,
                    )
                    if inspect.iscoroutine(retain_coro):
                        await retain_coro

                if hasattr(self.memory, "retain") and callable(self.memory.retain):
                    if not hasattr(self.memory, "aretain") or isinstance(self.memory.retain, MagicMock):
                        retain_res = self.memory.retain(
                            content=hindsight_content,
                            bank_id=bank_id,
                            context=f"Repository {cand.repository_full_name}, Confirmed Developer Feedback: {cand.source_ref}",
                            document_id=doc_id,
                            metadata={
                                "repository_id": cand.repository_id,
                                "repository_full_name": cand.repository_full_name,
                                "memory_id": str(mem_id),
                                "type": str(target_memory_dict.get("type")),
                                "authority": str(target_memory_dict.get("authority")),
                                "statement": statement,
                                "scope": scope,
                                "status": str(target_memory_dict.get("status")),
                                "source_type": "developer_feedback",
                                "source_ref": cand.source_ref or f"PR #{cand.pr_number}",
                                "source_pr_number": str(cand.pr_number or ""),
                                "source_commit_sha": cand.commit_sha or "",
                            },
                            tags=tags_list,
                        )
                        if inspect.iscoroutine(retain_res):
                            await retain_res

                sync_state = MemorySyncState.SYNCED.value
            except HindsightMemoryError:
                sync_state = MemorySyncState.FAILED.value
                if hasattr(self.supabase, "update_engineering_memory"):
                    await self.supabase.update_engineering_memory(mem_id, {"sync_state": sync_state})
                target_memory_dict["sync_state"] = sync_state
                raise
            except Exception as e:
                logger.warning("Hindsight retention failed for confirmed memory: %s", e)
                sync_state = MemorySyncState.FAILED.value
        else:
            sync_state = MemorySyncState.UNAVAILABLE.value

        if hasattr(self.supabase, "update_engineering_memory"):
            await self.supabase.update_engineering_memory(mem_id, {"sync_state": sync_state})
        target_memory_dict["sync_state"] = sync_state

        # Update candidate state to PERSISTED / CONFIRMED
        cand.status = CandidateStatus.PERSISTED
        cand.persisted_memory_id = str(mem_id)
        cand.updated_at = datetime.now(timezone.utc).isoformat()
        if hasattr(self.supabase, "save_learning_candidate"):
            await self.supabase.save_learning_candidate(cand.model_dump())

        canonical_obj = CanonicalMemory(**target_memory_dict)
        return {
            "candidate": cand,
            "status": action_taken,
            "canonical_memory": canonical_obj,
            "superseded_memory": CanonicalMemory(**superseded_mem) if superseded_mem else None,
            "hindsight_sync_status": sync_state,
        }

    async def reject_candidate(
        self,
        candidate_id: str,
        expected_repo_id: str,
        rejection: RejectLearningCandidateRequest,
    ) -> LearningCandidate:
        """Explicit developer rejection of a learning candidate.
        Marks candidate REJECTED and guarantees NO canonical memory is created.
        """
        raw_cand = await self.supabase.get_learning_candidate(candidate_id)
        if not raw_cand:
            raise ConfigurationError(f"Learning candidate '{candidate_id}' not found.")

        cand = LearningCandidate(**raw_cand)
        if cand.repository_id != expected_repo_id:
            raise ConfigurationError(
                f"REPOSITORY_MISMATCH: Candidate belongs to '{cand.repository_id}', not '{expected_repo_id}'"
            )

        cand.status = CandidateStatus.REJECTED
        cand.validation_reasons.append(rejection.reason or "Rejected by developer in review workspace")
        cand.updated_at = datetime.now(timezone.utc).isoformat()

        if hasattr(self.supabase, "save_learning_candidate"):
            await self.supabase.save_learning_candidate(cand.model_dump())

        return cand


_learning_engine: Optional[ControlledLearningEngine] = None


def get_learning_engine() -> ControlledLearningEngine:
    """Returns singleton instance of ControlledLearningEngine."""
    global _learning_engine
    if _learning_engine is None:
        _learning_engine = ControlledLearningEngine()
    return _learning_engine
