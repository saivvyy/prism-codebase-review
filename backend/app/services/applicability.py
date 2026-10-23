"""
Applicability Engine for PRism.

Deterministic validation and filtering layer between memory retrieval (Hindsight/Supabase)
and LLM reasoning (Groq).

Pipeline:
RECALLED CANDIDATE MEMORIES
  -> Deduplication & Model Normalization
  -> Repository Isolation Filter
  -> Lifecycle Status Filter (ACTIVE only)
  -> Provenance Validity Filter
  -> Temporal Validity Filter (valid_from / valid_until)
  -> Scope Applicability Filter (files & Tree-sitter AST symbols)
  -> Authority Partitioning (Enforceable vs Exception vs Contextual)
  -> Conflict Detection
  -> Structured Applicability Result
"""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.schemas.canonical_memory import (
    CanonicalMemory,
    KnowledgeType,
    MemoryAuthority,
    MemoryStatus,
    compute_memory_identity,
    get_default_authority,
    normalize_claim_or_statement,
    normalize_knowledge_type,
    validate_approved_exception,
    validate_provenance,
)
from app.schemas.review import ApplicabilityResultSchema

logger = logging.getLogger(__name__)


class ApplicabilityEngine:
    """Deterministic evaluation engine determining which recalled memories are actually
    applicable, authoritative evidence for a specific pull request.
    """

    def evaluate(
        self,
        repository_id: str,
        repository_full_name: Optional[str],
        changed_files: List[str],
        structural_symbols: Optional[Dict[str, List[str]]] = None,
        candidate_memories: Optional[List[Union[CanonicalMemory, Dict[str, Any]]]] = None,
        review_time: Optional[Union[datetime, str]] = None,
        hindsight_status: str = "available",
    ) -> ApplicabilityResultSchema:
        """Deterministically evaluates candidate memories against the target PR context."""
        structural_symbols = structural_symbols or {}
        raw_candidates = candidate_memories or []
        
        # Flatten all structural symbols for repo/PR scope evaluation
        all_symbols: List[str] = []
        for file_symbols in structural_symbols.values():
            all_symbols.extend(file_symbols)

        # Output collections
        recalled_count = len(raw_candidates)
        rejected_memories: List[Dict[str, Any]] = []
        applicable_rules: List[Dict[str, Any]] = []
        applicable_exceptions: List[Dict[str, Any]] = []
        contextual_memories: List[Dict[str, Any]] = []
        conflicts: List[Dict[str, Any]] = []
        matched_scopes: Set[str] = set()
        provenance_references: Set[str] = set()
        supporting_evidence: List[Dict[str, Any]] = []

        # 1. Normalize and Deduplicate Candidates
        normalized_memories: List[CanonicalMemory] = []
        seen_identities: Dict[str, CanonicalMemory] = {}

        for item in raw_candidates:
            cm: Optional[CanonicalMemory] = None
            if isinstance(item, CanonicalMemory):
                cm = item
            elif isinstance(item, dict):
                try:
                    cm = CanonicalMemory(**item)
                except Exception as e:
                    logger.debug("Failed parsing candidate memory: %s", e)
                    rejected_memories.append({
                        "memory_id": item.get("memory_id") or item.get("id") or "unknown",
                        "statement": item.get("statement") or item.get("claim") or "",
                        "rejection_reason": "malformed_memory_model",
                        "details": str(e),
                    })
                    continue

            if not cm:
                continue

            ident = cm.identity_hash or compute_memory_identity(
                repository_id=cm.repository_id,
                statement=cm.statement,
                scope=cm.scope,
            )

            if ident in seen_identities:
                # Merge evidence references into the existing instance
                existing = seen_identities[ident]
                merged_ev = list(existing.evidence_items)
                for ev in cm.evidence_items:
                    if ev not in merged_ev:
                        merged_ev.append(ev)
                if cm.source_ref and cm.source_ref not in merged_ev and "hindsight" not in cm.source_ref.lower():
                    merged_ev.append(cm.source_ref)
                existing.evidence_items = merged_ev
                continue

            seen_identities[ident] = cm
            normalized_memories.append(cm)

        # 2. Sequential Deterministic Filtering for each memory
        for mem in normalized_memories:
            # Stage A: Repository Isolation Filter
            # Canonical repository_id must match current PR repository
            repo_matches = (
                mem.repository_id == repository_id
                or (
                    repository_full_name
                    and mem.repository_full_name
                    and mem.repository_full_name.lower() == repository_full_name.lower()
                    and mem.repository_id == repository_id
                )
            )
            # If repo_id doesn't match, strictly reject
            if mem.repository_id != repository_id:
                rejected_memories.append({
                    "memory_id": mem.memory_id,
                    "statement": mem.statement,
                    "rejection_reason": "repository_mismatch",
                    "details": f"Memory repository '{mem.repository_id}' does not match PR repository '{repository_id}'",
                })
                continue

            # Stage B: Lifecycle Status Filter
            # Only ACTIVE memories may enforce architecture or cause DRIFT
            all_superseded_ids = {
                m.supersedes for m in normalized_memories if m.supersedes
            } | {
                m.memory_id for m in normalized_memories if m.superseded_by
            }
            if mem.status == MemoryStatus.SUPERSEDED or mem.superseded_by or mem.memory_id in all_superseded_ids:
                rejected_memories.append({
                    "memory_id": mem.memory_id,
                    "statement": mem.statement,
                    "rejection_reason": "superseded",
                    "details": f"Memory was superseded by '{mem.superseded_by or 'another decision'}'",
                })
                continue
            if mem.status == MemoryStatus.REVOKED:
                rejected_memories.append({
                    "memory_id": mem.memory_id,
                    "statement": mem.statement,
                    "rejection_reason": "revoked",
                    "details": "Memory has been revoked and cannot enforce architecture",
                })
                continue
            if mem.status != MemoryStatus.ACTIVE:
                rejected_memories.append({
                    "memory_id": mem.memory_id,
                    "statement": mem.statement,
                    "rejection_reason": "inactive_lifecycle",
                    "details": f"Memory status is '{mem.status}', expected 'ACTIVE'",
                })
                continue

            # Stage C: Provenance Validity Filter
            # Authoritative or exception guidance must have real provenance
            is_prov_valid, prov_reason = validate_provenance(mem.model_dump())
            if not is_prov_valid:
                # If memory attempts to enforce or claim exception without valid provenance, reject from enforcement
                if mem.authority in (MemoryAuthority.ENFORCEABLE, MemoryAuthority.EXCEPTION, MemoryAuthority.REJECTED_PATTERN):
                    rejected_memories.append({
                        "memory_id": mem.memory_id,
                        "statement": mem.statement,
                        "rejection_reason": "invalid_provenance",
                        "details": prov_reason or "Missing traceable provenance reference",
                    })
                    continue

            # Stage D: Temporal Validity Filter
            is_temp_valid, temp_reason = mem.is_temporally_valid(review_time)
            if not is_temp_valid:
                reason_code = "future_guidance" if "not yet valid" in (temp_reason or "") else "expired_guidance"
                rejected_memories.append({
                    "memory_id": mem.memory_id,
                    "statement": mem.statement,
                    "rejection_reason": reason_code,
                    "details": temp_reason,
                })
                continue

            # Stage E: Scope Applicability Filter
            # Check if scope matches any changed file or AST structural symbol
            file_match_found = False
            for f in changed_files:
                symbols_for_file = structural_symbols.get(f, [])
                if mem.is_applicable_to_path(f, symbols_for_file or all_symbols):
                    file_match_found = True
                    matched_scopes.add(f)
                    break

            if not file_match_found:
                rejected_memories.append({
                    "memory_id": mem.memory_id,
                    "statement": mem.statement,
                    "rejection_reason": "scope_mismatch",
                    "details": f"Scope '{mem.scope}' does not match any changed files in PR",
                })
                continue

            # Stage F: Authority Partitioning
            # Only ACTIVE memories with ENFORCEABLE or REJECTED_PATTERN authority can cause DRIFT.
            # OBSERVED_CONVENTION and REVIEW_OBSERVATION cannot independently cause DRIFT.
            mem_dict = mem.model_dump()
            if mem.source_ref and "hindsight" not in mem.source_ref.lower():
                provenance_references.add(mem.source_ref)

            if mem.authority == MemoryAuthority.EXCEPTION and mem.type == KnowledgeType.APPROVED_EXCEPTION:
                # Validate approved exception structure
                is_valid_exc, exc_err = validate_approved_exception(mem)
                if is_valid_exc:
                    applicable_exceptions.append(mem_dict)
                    supporting_evidence.append({
                        "type": "approved_exception",
                        "memory_id": mem.memory_id,
                        "statement": mem.statement,
                        "scope": mem.scope,
                        "provenance": mem.source_ref,
                    })
                else:
                    rejected_memories.append({
                        "memory_id": mem.memory_id,
                        "statement": mem.statement,
                        "rejection_reason": "invalid_exception_structure",
                        "details": exc_err,
                    })
            elif mem.authority in (MemoryAuthority.ENFORCEABLE, MemoryAuthority.REJECTED_PATTERN):
                applicable_rules.append(mem_dict)
                supporting_evidence.append({
                    "type": "enforceable_rule",
                    "memory_id": mem.memory_id,
                    "statement": mem.statement,
                    "scope": mem.scope,
                    "authority": mem.authority.value,
                    "provenance": mem.source_ref,
                })
            else:
                # Contextual (e.g. OBSERVED_CONVENTION) or Non-authoritative (e.g. REVIEW_OBSERVATION)
                contextual_memories.append(mem_dict)
                supporting_evidence.append({
                    "type": "contextual_observation",
                    "memory_id": mem.memory_id,
                    "statement": mem.statement,
                    "scope": mem.scope,
                    "authority": mem.authority.value,
                    "provenance": mem.source_ref,
                })

        # 3. Conflict Detection Among Applicable Enforceable Rules
        # If two or more ACTIVE authoritative memories apply to current scope and materially conflict:
        # Cannot be resolved by Hindsight score, order, or Groq preference.
        # Resolved only if explicit supersession/revocation exists.
        for i in range(len(applicable_rules)):
            for j in range(i + 1, len(applicable_rules)):
                r1_dict = applicable_rules[i]
                r2_dict = applicable_rules[j]

                r1_id = r1_dict.get("memory_id")
                r2_id = r2_dict.get("memory_id")

                # Check if explicit supersession links them
                is_superseded = (
                    r1_dict.get("supersedes") == r2_id
                    or r2_dict.get("supersedes") == r1_id
                    or r1_dict.get("superseded_by") == r2_id
                    or r2_dict.get("superseded_by") == r1_id
                )
                if is_superseded:
                    continue

                if r1_dict.get("source_pr_number") and r1_dict.get("source_pr_number") == r2_dict.get("source_pr_number"):
                    continue
                if r1_dict.get("source_ref") and r1_dict.get("source_ref") == r2_dict.get("source_ref") and "pr" in str(r1_dict.get("source_ref", "")).lower():
                    continue

                s1 = normalize_claim_or_statement(r1_dict.get("statement", "")).lower()
                s2 = normalize_claim_or_statement(r2_dict.get("statement", "")).lower()

                # If statements are essentially identical or substrings of each other, they don't conflict
                if s1 in s2 or s2 in s1:
                    continue

                # Semantic / keyword contradiction check
                words1 = set(re.findall(r"\w{3,}", s1))
                words2 = set(re.findall(r"\w{3,}", s2))
                common_keywords = words1.intersection(words2)

                negation_markers = {
                    "never", "not", "must not", "don't", "prohibit",
                    "disallow", "deprecated", "forbid", "disallowed",
                }
                has_neg_1 = any(nw in s1 for nw in negation_markers)
                has_neg_2 = any(nw in s2 for nw in negation_markers)

                # Check for direct contradiction on overlapping topics
                is_contradictory = (
                    (len(common_keywords) >= 2 and (has_neg_1 != has_neg_2))
                    or (len(common_keywords) >= 1 and (has_neg_1 != has_neg_2))
                    or ("service" in s1 and ("directly access" in s2 or "raw sql" in s2) and not has_neg_2)
                    or ("service" in s2 and ("directly access" in s1 or "raw sql" in s1) and not has_neg_1)
                )
                if is_contradictory:
                    # Check whether scopes intersect on the current PR's changed files
                    r1_mem = CanonicalMemory(**r1_dict)
                    r2_mem = CanonicalMemory(**r2_dict)
                    overlapping_files = [
                        f for f in changed_files
                        if r1_mem.is_applicable_to_path(f, structural_symbols.get(f, []))
                        and r2_mem.is_applicable_to_path(f, structural_symbols.get(f, []))
                    ]
                    if overlapping_files:
                        conflicts.append({
                            "rule_1_id": r1_id,
                            "rule_1_statement": r1_dict.get("statement"),
                            "rule_2_id": r2_id,
                            "rule_2_statement": r2_dict.get("statement"),
                            "overlapping_files": overlapping_files,
                            "conflict_type": "material_contradiction",
                        })

        all_applicable = applicable_rules + applicable_exceptions + contextual_memories

        return ApplicabilityResultSchema(
            repository_id=repository_id,
            repository_full_name=repository_full_name,
            recalled_memories_count=recalled_count,
            applicable_memories=all_applicable,
            rejected_memories=rejected_memories,
            matched_scopes=sorted(list(matched_scopes)),
            applicable_rules=applicable_rules,
            applicable_exceptions=applicable_exceptions,
            contextual_memories=contextual_memories,
            conflicts=conflicts,
            supporting_evidence=supporting_evidence,
            provenance_references=sorted(list(provenance_references)),
            hindsight_status=hindsight_status,
        )


_applicability_engine: Optional[ApplicabilityEngine] = None


def get_applicability_engine() -> ApplicabilityEngine:
    global _applicability_engine
    if _applicability_engine is None:
        _applicability_engine = ApplicabilityEngine()
    return _applicability_engine
