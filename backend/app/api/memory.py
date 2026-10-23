import hashlib
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.auth import get_optional_current_user
from app.core.exceptions import ConfigurationError, HindsightMemoryError
from app.schemas.auth import UserSessionProfile
from app.schemas.memory import (
    EngineeringMemorySchema,
    ListEngineeringMemoriesResponse,
    MemoryItemSchema,
    RecallMemoryResponse,
    RecordMemoryRequest,
    RetainMemoryRequest,
    RetainMemoryResponse,
)
from app.schemas.canonical_memory import (
    CanonicalMemory,
    KnowledgeType,
    MemoryAuthority,
    MemoryStatus,
    RecordCanonicalMemoryRequest,
    SupersedeMemoryRequest,
    RevokeMemoryRequest,
    ListCanonicalMemoriesResponse,
    compute_memory_identity,
    get_default_authority,
    normalize_knowledge_type,
    normalize_status,
    validate_provenance,
    check_exception_scope,
    validate_approved_exception,
)
from app.schemas.bootstrap import (
    BootstrapCandidate,
    BootstrapSourceRequest,
    BootstrapSummaryResponse,
)
from app.services.bootstrap import HistoricalBootstrapService, get_bootstrap_service
from app.services.memory import HindsightMemoryService, get_hindsight_service
from app.services.supabase import SupabaseService, get_supabase_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Engineering Memory"])


def get_repo_bank_id(repo_id: str, full_name: Optional[str] = None) -> str:
    """Returns deterministic bank_id scoped strictly to a repository (<owner>_<repo>)."""
    if full_name and "/" in full_name:
        return full_name.replace("/", "_").lower()
    if repo_id and "/" in repo_id:
        return repo_id.replace("/", "_").lower()
    if full_name:
        return full_name.replace("/", "_").lower()
    return f"repo_{repo_id.replace('-', '').lower()}"


def _clean_claim_text(text: str) -> str:
    """Strips common prefix artifacts (e.g. In repo/name, Type: ..., Claim: ...) from raw text."""
    import re
    claim = text.strip()
    claim = re.sub(r"^In\s+(?:the\s+)?[\w\-]+/[\w\-]+(?:\s+repository)?,\s*", "", claim, flags=re.IGNORECASE)
    claim = re.sub(r"^Rule:\s*", "", claim, flags=re.IGNORECASE)
    claim = re.sub(r"^Type:[^\n]+\n+Claim:\s*", "", claim, flags=re.IGNORECASE)
    claim = re.sub(r"^Claim:\s*", "", claim, flags=re.IGNORECASE)
    return claim.strip()


# ==============================================================================
# Repository-Scoped Engineering Memory Endpoints
# ==============================================================================

def _convert_item_to_engineering_schema(
    it: MemoryItemSchema,
    repo_id: str,
    default_cat: Optional[str] = None,
    repo_obj: Optional[Dict[str, Any]] = None,
) -> EngineeringMemorySchema:
    import re
    from datetime import datetime, timezone

    meta = it.metadata or {}
    raw_type = (meta.get("category") or meta.get("type") or (it.tags[0] if it.tags else it.type) or default_cat or "architecture_decision")
    norm_type = "architecture_decision"
    if raw_type in ("rejected_pattern", "architecture_drift"):
        norm_type = "rejected_pattern"
    elif raw_type == "approved_exception":
        norm_type = "approved_exception"
    elif raw_type == "review_decision":
        norm_type = "review_decision"
    elif raw_type == "lesson_learned":
        norm_type = "lesson_learned"

    raw_claim = meta.get("claim") or it.text
    rationale = meta.get("rationale")
    scope = meta.get("scope")

    if not meta.get("claim") and it.text:
        if "Rationale:" in it.text:
            parts = it.text.split("Rationale:")
            raw_claim = parts[0].strip()
            rest = parts[1]
            if "Scope:" in rest:
                r_parts = rest.split("Scope:")
                rationale = r_parts[0].strip()
                scope = r_parts[1].strip()
            elif "Evidence:" in rest:
                ev_parts = rest.split("Evidence:")
                rationale = ev_parts[0].strip()
            else:
                rationale = rest.strip()
        else:
            raw_claim = it.text

    claim = _clean_claim_text(raw_claim)

    evidence = list(meta.get("evidence_items") or [])
    if not evidence and meta.get("source_ref"):
        evidence = [meta["source_ref"]]
    elif not evidence and meta.get("pr_number"):
        evidence = [f"PR #{meta['pr_number']}"]

    pr_number = None
    if meta.get("pr_number") and str(meta["pr_number"]).isdigit():
        pr_number = int(meta["pr_number"])
    elif meta.get("source_ref"):
        m = re.search(r"#(\d+)", str(meta["source_ref"]))
        if m:
            pr_number = int(m.group(1))

    # Resolve canonical repository provenance
    canonical_repo_id = repo_obj.get("id") if repo_obj else repo_id
    canonical_full_name = repo_obj.get("full_name") if repo_obj else meta.get("repository_full_name")
    canonical_github_id = repo_obj.get("github_repo_id") if repo_obj else meta.get("github_repo_id")

    now_iso = datetime.now(timezone.utc).isoformat()
    return EngineeringMemorySchema(
        id=it.id or f"mem-{abs(hash(claim))}",
        repository_id=canonical_repo_id,
        type=norm_type,
        claim=claim,
        rationale=rationale or "Established in organizational repository memory.",
        scope=scope or "Repository architecture",
        status="active",
        source_type="pr_review" if pr_number or meta.get("source_ref") else "manual",
        source_ref=meta.get("source_ref") or (f"PR #{pr_number}" if pr_number else "Repository Decision"),
        source_url=meta.get("source_url"),
        review_id=meta.get("review_id"),
        pr_number=pr_number,
        repository_full_name=canonical_full_name,
        github_repo_id=canonical_github_id,
        confidence="supported",
        evidence_items=evidence,
        hindsight_document_id=it.id,
        created_at=meta.get("created_at") or now_iso,
        updated_at=now_iso,
    )


@router.get(
    "/repositories/{repo_id}/memory",
    response_model=ListEngineeringMemoriesResponse,
    status_code=status.HTTP_200_OK,
)
async def list_repository_memories(
    repo_id: str,
    category: Optional[str] = Query(None, description="Category filter (e.g. architecture_decision)"),
    type: Optional[str] = Query(None, description="Alias for category filter"),
    limit: int = Query(50, ge=1, le=100),
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    memory: HindsightMemoryService = Depends(get_hindsight_service),
) -> ListEngineeringMemoriesResponse:
    """Lists verified engineering memories strictly scoped to the repository, syncing from Hindsight."""
    user_id = current_user.id if current_user else None
    repo_obj = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )
    effective_category = category or type
    schemas: List[EngineeringMemorySchema] = []

    # 1. First fetch direct from Hindsight when configured, falling back to Supabase on failure
    if memory.is_configured:
        try:
            recall_resp = await recall_memories(
                query="architectural decisions conventions rejected patterns",
                repo_id=repo_id,
                category=effective_category,
                current_user=current_user,
                memory=memory,
                supabase=supabase,
            )
            schemas = [
                _convert_item_to_engineering_schema(it, repo_id, effective_category, repo_obj=repo_obj)
                for it in recall_resp.items
            ]
        except Exception as e:
            logger.warning("Hindsight memory service failure in list_repository_memories, falling back to Supabase: %s", e)
            records = await supabase.list_engineering_memories(
                repository_id=repo_id,
                memory_type=effective_category,
                limit=limit,
            )
            schemas = [EngineeringMemorySchema(**r) for r in records]
    else:
        # Fallback only when Hindsight service is not configured
        records = await supabase.list_engineering_memories(
            repository_id=repo_id,
            memory_type=effective_category,
            limit=limit,
        )
        for r in records:
            schemas.append(EngineeringMemorySchema(**r))

    if effective_category and effective_category != "all":
        schemas = [s for s in schemas if s.type == effective_category]

    return ListEngineeringMemoriesResponse(
        repository_id=repo_id,
        memories=schemas[:limit],
        total_count=len(schemas),
    )


@router.post(
    "/repositories/{repo_id}/memory",
    response_model=EngineeringMemorySchema,
    status_code=status.HTTP_201_CREATED,
)
async def record_repository_memory(
    repo_id: str,
    req: RecordMemoryRequest,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    memory: HindsightMemoryService = Depends(get_hindsight_service),
) -> EngineeringMemorySchema:
    """Explicitly records and retains an evidence-backed engineering decision, pattern, or exception."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )
    repo_full_name = repo.get("full_name") if repo else repo_id
    bank_id = get_repo_bank_id(repo_id, repo_full_name)

    effective_claim = (req.claim or req.statement or "").strip()
    if not effective_claim:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A statement or claim is required for durable memory.",
        )

    norm_ktype = normalize_knowledge_type(req.type)

    # Rule 9, 13, 14: Every durable memory requires verifiable real provenance
    prov_payload = {
        "repository_id": repo_id,
        "source_ref": req.source_ref,
        "source_type": getattr(req, "source_type", "manual"),
        "source_url": req.source_url,
        "source_pr_number": getattr(req, "source_pr_number", None) or getattr(req, "pr_number", None),
        "source_commit_sha": getattr(req, "source_commit_sha", None),
    }
    is_valid_prov, prov_err = validate_provenance(prov_payload)
    if not is_valid_prov:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provenance: {prov_err}. Every durable memory requires verifiable real provenance.",
        )

    # Rule 6: APPROVED_EXCEPTION requires explicit, restricted scope (cannot be universal wildcard or repo-wide)
    if norm_ktype == KnowledgeType.APPROVED_EXCEPTION:
        if not req.scope or req.scope.strip() in ("Repository architecture", "*", "all", ""):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="APPROVED_EXCEPTION requires an explicit, restricted scope matching specific components or files.",
            )

    # 1. Deduplication check within the repository
    existing = await supabase.find_existing_memory(repo_id, effective_claim)
    if existing:
        existing_evidence = list(existing.get("evidence_items") or [])
        new_ref = req.source_ref
        if new_ref and new_ref not in existing_evidence:
            existing_evidence.append(new_ref)

        updated = await supabase.update_engineering_memory(
            existing["id"],
            {
                "evidence_items": existing_evidence,
                "confidence": "supported",
                "status": "active",
                "rationale": req.rationale or existing.get("rationale"),
                "scope": req.scope or existing.get("scope"),
            },
        )
        doc_id = existing.get("hindsight_document_id") or f"mem-{repo_id[:8]}-{hashlib.sha256(effective_claim.encode()).hexdigest()[:12]}"
        hindsight_content = (
            f"Type: {existing.get('type')}\n"
            f"Claim: {existing.get('claim')}\n"
            f"Rationale: {updated.get('rationale')}\n"
            f"Scope: {updated.get('scope')}\n"
            f"Evidence: {', '.join(existing_evidence)}"
        )
        try:
            await memory.aretain(
                content=hindsight_content,
                bank_id=bank_id,
                context=f"Repository: {repo_full_name}, Confirmed in: {new_ref or 'Consensus'}",
                document_id=doc_id,
                metadata={
                    "repository_id": repo_id,
                    "type": existing.get("type", "architecture_decision"),
                    "source_ref": new_ref or existing.get("source_ref") or "",
                    "claim": existing.get("claim", ""),
                },
                tags=[existing.get("type", "architecture_decision"), "engineering_memory"],
            )
        except Exception as e:
            logger.warning("Hindsight aretain update notice: %s", e)

        return EngineeringMemorySchema(**updated)

    # 2. Create new memory record
    doc_id = f"mem-{repo_id[:8]}-{hashlib.sha256(effective_claim.encode()).hexdigest()[:12]}"
    evidence = req.evidence_items or ([req.source_ref] if req.source_ref else [])

    rationale = req.rationale.strip() if req.rationale and req.rationale.strip() else "Not explicitly documented in available GitHub evidence."
    scope = req.scope.strip() if req.scope and req.scope.strip() else "Repository architecture"

    record = await supabase.create_engineering_memory(
        repository_id=repo_id,
        claim=effective_claim,
        statement=effective_claim,
        type=req.type,
        authority=req.authority or get_default_authority(norm_ktype).value,
        rationale=rationale,
        scope=scope,
        status="active",
        source_type="manual" if not req.source_ref else "pr_review",
        source_ref=req.source_ref,
        source_url=req.source_url,
        source_pr_number=req.source_pr_number or getattr(req, "pr_number", None),
        source_commit_sha=req.source_commit_sha,
        repository_full_name=repo_full_name,
        confidence=req.confidence,
        evidence_items=evidence,
        hindsight_document_id=doc_id,
    )

    hindsight_content = (
        f"Type: {req.type}\n"
        f"Claim: {effective_claim}\n"
        f"Rationale: {rationale}\n"
        f"Scope: {scope}\n"
        f"Evidence: {', '.join(evidence) if evidence else 'Initial record'}"
    )

    try:
        await memory.aretain(
            content=hindsight_content,
            bank_id=bank_id,
            context=f"Repository: {repo_full_name}, Ref: {req.source_ref or 'Manual'}",
            document_id=doc_id,
            metadata={
                "repository_id": repo_id,
                "category": req.type,
                "type": req.type,
                "source_ref": req.source_ref or "",
                "claim": effective_claim,
            },
            tags=[req.type, "engineering_memory"],
        )
    except Exception as e:
        logger.warning("Hindsight aretain notice: %s", e)

    return EngineeringMemorySchema(**record)


@router.post(
    "/repositories/{repo_id}/memory/{memory_id}/supersede",
    response_model=EngineeringMemorySchema,
    status_code=status.HTTP_200_OK,
)
async def supersede_repository_memory(
    repo_id: str,
    memory_id: str,
    req: SupersedeMemoryRequest,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    memory: HindsightMemoryService = Depends(get_hindsight_service),
) -> EngineeringMemorySchema:
    """Supersedes an existing engineering memory with a new active memory with strict provenance."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )
    existing = await supabase.get_engineering_memory(memory_id)
    if not existing or existing.get("repository_id") != repo_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Engineering memory '{memory_id}' not found in repository.",
        )

    # Validate provenance of superseding memory
    is_valid_prov, prov_err = validate_provenance({
        "repository_id": repo_id,
        "source_ref": req.source_ref,
        "source_url": req.source_url,
        "source_pr_number": req.source_pr_number,
        "source_commit_sha": req.source_commit_sha,
    })
    if not is_valid_prov:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provenance for superseding memory: {prov_err}",
        )

    repo_full_name = repo.get("full_name") or repo_id
    new_data = {
        "statement": req.new_statement,
        "claim": req.new_statement,
        "type": existing.get("type", "DOCUMENTED_DECISION"),
        "rationale": req.rationale,
        "scope": req.scope or existing.get("scope", "Repository architecture"),
        "source_ref": req.source_ref,
        "source_url": req.source_url,
        "source_pr_number": req.source_pr_number,
        "source_commit_sha": req.source_commit_sha,
        "repository_full_name": repo_full_name,
    }

    created = await supabase.supersede_memory(memory_id, new_data)

    if memory.is_configured:
        bank_id = get_repo_bank_id(repo_id, repo_full_name)
        doc_id = f"mem-{repo_id[:8]}-{hashlib.sha256(req.new_statement.encode()).hexdigest()[:12]}"
        try:
            await memory.aretain(
                content=f"Type: {created.get('type')}\nClaim: {req.new_statement}\nRationale: {req.rationale}\nScope: {new_data['scope']}\nEvidence: {req.source_ref} (supersedes {memory_id})",
                bank_id=bank_id,
                context=f"Repository: {repo_full_name}, Ref: {req.source_ref}",
                document_id=doc_id,
                metadata={
                    "repository_id": repo_id,
                    "type": created.get("type"),
                    "source_ref": req.source_ref,
                    "claim": req.new_statement,
                    "supersedes": memory_id,
                },
                tags=[created.get("type", "DOCUMENTED_DECISION"), "engineering_memory"],
            )
        except Exception as e:
            logger.warning("Hindsight supersede notice: %s", e)

    return EngineeringMemorySchema(**created)


@router.post(
    "/repositories/{repo_id}/memory/{memory_id}/revoke",
    response_model=EngineeringMemorySchema,
    status_code=status.HTTP_200_OK,
)
async def revoke_repository_memory(
    repo_id: str,
    memory_id: str,
    req: Optional[RevokeMemoryRequest] = None,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> EngineeringMemorySchema:
    """Revokes an active engineering memory."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )
    existing = await supabase.get_engineering_memory(memory_id)
    if not existing or existing.get("repository_id") != repo_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Engineering memory '{memory_id}' not found in repository.",
        )

    reason = req.reason if req else None
    updated = await supabase.revoke_memory(memory_id, reason=reason)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to revoke engineering memory.",
        )
    return EngineeringMemorySchema(**updated)


@router.get(
    "/repositories/{repo_id}/canonical-memory",
    response_model=ListCanonicalMemoriesResponse,
    status_code=status.HTTP_200_OK,
)
async def list_canonical_memories(
    repo_id: str,
    status_filter: Optional[str] = Query("ACTIVE", alias="status", description="Status filter: ACTIVE, SUPERSEDED, REVOKED, or all"),
    type: Optional[str] = Query(None, description="Knowledge type filter"),
    authority: Optional[str] = Query(None, description="Authority filter"),
    limit: int = Query(50, ge=1, le=100),
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> ListCanonicalMemoriesResponse:
    """Lists canonical engineering memories with structured provenance and authority semantics."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )

    records = await supabase.list_engineering_memories(
        repository_id=repo_id,
        memory_type=type,
        status=status_filter,
        authority=authority,
        limit=limit,
    )

    canonical_list: List[CanonicalMemory] = []
    for r in records:
        try:
            canonical_list.append(CanonicalMemory(**r))
        except Exception as e:
            logger.warning("Could not convert memory row to CanonicalMemory: %s", e)

    return ListCanonicalMemoriesResponse(
        repository_id=repo_id,
        repository_full_name=repo.get("full_name"),
        memories=canonical_list,
        total_count=len(canonical_list),
    )


@router.post(
    "/repositories/{repo_id}/canonical-memory",
    response_model=CanonicalMemory,
    status_code=status.HTTP_201_CREATED,
)
async def create_canonical_memory_endpoint(
    repo_id: str,
    req: RecordCanonicalMemoryRequest,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    memory: HindsightMemoryService = Depends(get_hindsight_service),
) -> CanonicalMemory:
    """Explicitly creates a canonical engineering memory with structured provenance and authority semantics."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )

    # Validate provenance
    prov_payload = {
        "repository_id": repo_id,
        "source_ref": req.source_ref,
        "source_url": req.source_url,
        "source_pr_number": req.source_pr_number,
        "source_commit_sha": req.source_commit_sha,
    }
    is_valid_prov, prov_err = validate_provenance(prov_payload)
    if not is_valid_prov:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provenance: {prov_err}. Every durable memory requires verifiable real provenance.",
        )

    # Scope validation for exceptions
    if req.type == KnowledgeType.APPROVED_EXCEPTION:
        if not req.scope or req.scope.strip() in ("Repository architecture", "*", "all", "global", ""):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="APPROVED_EXCEPTION requires an explicit, restricted scope matching specific components or files.",
            )

    repo_full_name = repo.get("full_name") or repo_id
    effective_auth = req.authority or get_default_authority(req.type)

    id_hash = compute_memory_identity(
        repository_id=repo_id,
        statement=req.statement,
        scope=req.scope,
        knowledge_type=req.type,
    )

    created = await supabase.create_engineering_memory(
        repository_id=repo_id,
        statement=req.statement,
        claim=req.statement,
        type=req.type.value,
        authority=effective_auth.value,
        rationale=req.rationale,
        scope=req.scope,
        status="ACTIVE",
        source_type=req.source_type,
        source_ref=req.source_ref,
        source_url=req.source_url,
        source_pr_number=req.source_pr_number,
        source_commit_sha=req.source_commit_sha,
        supersedes=req.supersedes,
        repository_full_name=repo_full_name,
        identity_hash=id_hash,
    )

    # Retain in Hindsight
    if memory.is_configured:
        bank_id = get_repo_bank_id(repo_id, repo_full_name)
        doc_id = f"mem-{repo_id[:8]}-{id_hash[:12]}"
        try:
            await memory.aretain(
                content=f"Type: {created.get('type')}\nAuthority: {created.get('authority')}\nClaim: {req.statement}\nRationale: {req.rationale}\nScope: {req.scope}\nEvidence: {req.source_ref}",
                bank_id=bank_id,
                context=f"Repository: {repo_full_name}, Source: {req.source_ref}",
                document_id=doc_id,
                metadata={
                    "repository_id": repo_id,
                    "repository_full_name": repo_full_name,
                    "memory_id": str(created.get("id")),
                    "type": str(created.get("type")),
                    "authority": str(created.get("authority")),
                    "source_ref": req.source_ref,
                    "claim": req.statement,
                },
                tags=[created.get("type"), "engineering_memory"],
            )
            await supabase.update_engineering_memory(created["id"], {"sync_state": "SYNCED"})
            created["sync_state"] = "SYNCED"
        except Exception as e:
            logger.warning("Hindsight aretain notice on canonical create: %s", e)
            await supabase.update_engineering_memory(created["id"], {"sync_state": "FAILED"})
            created["sync_state"] = "FAILED"
    else:
        await supabase.update_engineering_memory(created["id"], {"sync_state": "UNAVAILABLE"})
        created["sync_state"] = "UNAVAILABLE"

    return CanonicalMemory(**created)


@router.get(
    "/repositories/{repo_id}/memory/search",
    response_model=ListEngineeringMemoriesResponse,
    status_code=status.HTTP_200_OK,
)
async def search_repository_memories(
    repo_id: str,
    q: str = Query(..., min_length=1, description="Search query across decisions, rationale, and scope"),
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    memory: HindsightMemoryService = Depends(get_hindsight_service),
) -> ListEngineeringMemoriesResponse:
    """Searches engineering memory for the repository using Hindsight semantic recall and relational match."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )
    repo_full_name = repo.get("full_name") if repo else repo_id
    bank_id = get_repo_bank_id(repo_id, repo_full_name)

    all_records = await supabase.list_engineering_memories(repository_id=repo_id, limit=100)
    query_lower = q.lower().strip()

    # Direct match in Supabase
    matched = [
        r for r in all_records
        if query_lower in r.get("claim", "").lower()
        or query_lower in (r.get("rationale") or "").lower()
        or query_lower in (r.get("scope") or "").lower()
        or any(query_lower in ev.lower() for ev in (r.get("evidence_items") or []))
    ]

    schemas: List[EngineeringMemorySchema] = []
    seen_claims = set()

    # 1. Semantic recall from Hindsight
    if memory.is_configured:
        try:
            recall_res = await memory.arecall(query=q, bank_id=bank_id, max_tokens=2048)
            recalled_items = getattr(recall_res, "items", []) or []
            for item in recalled_items:
                schema = _convert_item_to_engineering_schema(item, repo_id, repo_obj=repo)
                claim_key = schema.claim.strip().lower()
                if claim_key not in seen_claims:
                    schemas.append(schema)
                    seen_claims.add(claim_key)
        except Exception as e:
            logger.warning("Hindsight semantic search notice: %s", e)

    # 2. Add relational text matches from Supabase
    for r in matched:
        claim_key = r.get("claim", "").strip().lower()
        if claim_key not in seen_claims:
            schemas.append(EngineeringMemorySchema(**r))
            seen_claims.add(claim_key)

    return ListEngineeringMemoriesResponse(
        repository_id=repo_id,
        memories=schemas,
        total_count=len(schemas),
    )


@router.get(
    "/repositories/{repo_id}/memory/{memory_id}",
    response_model=EngineeringMemorySchema,
    status_code=status.HTTP_200_OK,
)
async def get_repository_memory(
    repo_id: str,
    memory_id: str,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> EngineeringMemorySchema:
    """Gets details of an individual engineering memory."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )
    rec = await supabase.get_engineering_memory(memory_id)
    if not rec or rec.get("repository_id") != repo_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Engineering memory not found")
    return EngineeringMemorySchema(**rec)


@router.delete(
    "/repositories/{repo_id}/memory/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_repository_memory(
    repo_id: str,
    memory_id: str,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> None:
    """Deletes an engineering memory."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )
    rec = await supabase.get_engineering_memory(memory_id)
    if not rec or rec.get("repository_id") != repo_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Engineering memory not found")
    await supabase.delete_engineering_memory(memory_id)


# ==============================================================================
# Hindsight Integration / Review Engine Bridge Endpoints
# ==============================================================================

@router.get("/memory/recall", response_model=RecallMemoryResponse, status_code=status.HTTP_200_OK)
async def recall_memories(
    query: Optional[str] = Query("architectural decisions conventions rejected patterns", description="Search query"),
    bank_id: Optional[str] = Query(None, description="Memory bank identifier"),
    category: Optional[str] = Query(None, description="Category filter"),
    repo_id: Optional[str] = Query(None, description="Scope memory recall to specific repository ID"),
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    memory: HindsightMemoryService = Depends(get_hindsight_service),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> RecallMemoryResponse:
    """Recalls organizational engineering memory via Hindsight, scoped strictly to repository."""
    category_str = category if isinstance(category, str) else None
    tags = [category_str] if category_str and category_str != "all" else None

    # Derive repository-scoped bank_id if repo_id provided
    effective_bank_id = bank_id if isinstance(bank_id, str) else None
    repo_id_str = repo_id if isinstance(repo_id, str) else None
    user_id = current_user.id if current_user else None
    if not effective_bank_id and repo_id_str:
        repo = await supabase.get_repository_by_id(repo_id_str, user_id=user_id)
        if not repo:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Repository '{repo_id_str}' not found or access denied.",
            )
        repo_full_name = repo.get("full_name") if repo else (repo_id_str if "/" in repo_id_str else None)
        effective_bank_id = get_repo_bank_id(repo_id_str, repo_full_name)

    target_bank = effective_bank_id or memory.default_bank_id

    if memory.is_configured:
        try:
            effective_query = (query or "").strip()
            is_default_query = not effective_query or effective_query == "architectural decisions conventions rejected patterns"

            if is_default_query:
                combined_units = await memory.alist_memories(bank_id=target_bank, category=category)
                recalled = RecallMemoryResponse(
                    bank_id=target_bank,
                    query=query or "",
                    results_count=len(combined_units),
                    items=combined_units,
                    prompt_context="",
                )
            else:
                recalled = await memory.arecall(
                    query=effective_query,
                    bank_id=target_bank,
                    tags=tags,
                )
                combined_units = list(recalled.items)

            # Deduplicate units by normalized claim, prioritizing items with rich metadata (world units over observation units)
            dedup_by_claim: Dict[str, MemoryItemSchema] = {}
            for it in combined_units:
                if not it.metadata:
                    it.metadata = {}
                # Determine raw claim
                raw_claim = it.metadata.get("claim") or ""
                if not raw_claim and it.text:
                    if "Rationale:" in it.text:
                        raw_claim = it.text.split("Rationale:")[0].strip()
                    else:
                        raw_claim = it.text
                clean_claim = _clean_claim_text(raw_claim).lower()
                if not clean_claim:
                    continue

                if clean_claim not in dedup_by_claim:
                    dedup_by_claim[clean_claim] = it
                else:
                    existing = dedup_by_claim[clean_claim]
                    # If current item has richer metadata (e.g. pr_number or claim), prefer it
                    has_rich_meta = bool(it.metadata.get("pr_number") or it.metadata.get("claim"))
                    existing_rich = bool((existing.metadata or {}).get("pr_number") or (existing.metadata or {}).get("claim"))
                    if has_rich_meta and not existing_rich:
                        dedup_by_claim[clean_claim] = it

            final_items = list(dedup_by_claim.values())

            # Ensure every item has metadata populated for frontend rendering
            for it in final_items:
                if not it.metadata:
                    it.metadata = {}
                if "category" not in it.metadata:
                    it.metadata["category"] = it.tags[0] if it.tags else (it.type or "architecture_decision")
                if "claim" not in it.metadata:
                    if "Rationale:" in it.text:
                        parts = it.text.split("Rationale:")
                        it.metadata["claim"] = _clean_claim_text(parts[0].strip())
                        rest = parts[1]
                        if "Scope:" in rest:
                            r_parts = rest.split("Scope:")
                            it.metadata["rationale"] = r_parts[0].strip()
                            it.metadata["scope"] = r_parts[1].strip()
                        elif "Evidence:" in rest:
                            ev_parts = rest.split("Evidence:")
                            it.metadata["rationale"] = ev_parts[0].strip()
                        else:
                            it.metadata["rationale"] = rest.strip()
                    else:
                        it.metadata["claim"] = _clean_claim_text(it.text)
                else:
                    it.metadata["claim"] = _clean_claim_text(it.metadata["claim"])

            if category and category != "all":
                final_items = [
                    it for it in final_items
                    if it.metadata.get("category") == category or category in (it.tags or [])
                ]

            recalled.items = final_items
            recalled.results_count = len(final_items)
            recalled.prompt_context = "\n".join([f"- {it.text}" for it in final_items])

            return recalled
        except HindsightMemoryError as e:
            logger.error("Hindsight recall error: %s", e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Hindsight memory service unavailable: {e}",
            )
        except Exception as e:
            logger.error("Unexpected Hindsight error: %s", e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Hindsight query failed: {e}",
            )

    # Scoped fallback from genuine engineering_memories table
    items: List[MemoryItemSchema] = []
    if repo_id:
        mem_records = await supabase.list_engineering_memories(
            repository_id=repo_id,
            memory_type=category,
        )
        for m in mem_records:
            evidence_str = ", ".join(m.get("evidence_items") or [])
            text = f"Rule: {m.get('claim')}. Rationale: {m.get('rationale')}. Evidence: {evidence_str}"
            if query and query.lower() not in text.lower() and query != "architectural decisions conventions rejected patterns":
                continue
            items.append(
                MemoryItemSchema(
                    id=m.get("id"),
                    text=text,
                    type=m.get("type"),
                    score=0.95,
                    metadata={
                        "category": m.get("type"),
                        "claim": m.get("claim"),
                        "rationale": m.get("rationale"),
                        "scope": m.get("scope"),
                        "evidence_items": m.get("evidence_items"),
                        "source_ref": m.get("source_ref"),
                        "created_at": m.get("created_at"),
                    },
                    tags=[m.get("type", "architecture_decision"), "engineering_memory"],
                )
            )

    return RecallMemoryResponse(
        bank_id=effective_bank_id or memory.default_bank_id,
        query=query,
        results_count=len(items),
        items=items,
        prompt_context="\n".join([f"- {it.text}" for it in items]),
    )


@router.post("/memory/retain", response_model=RetainMemoryResponse, status_code=status.HTTP_201_CREATED)
async def retain_memory(
    req: RetainMemoryRequest,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    memory: HindsightMemoryService = Depends(get_hindsight_service),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> RetainMemoryResponse:
    """Retains a new engineering convention, decision, or approved exception into Hindsight."""
    user_id = current_user.id if current_user else None
    repo_id = (req.metadata or {}).get("repository_id")
    if repo_id:
        repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
        if not repo:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Repository '{repo_id}' not found or access denied.",
            )

    if not memory.is_configured:
        return RetainMemoryResponse(
            success=True,
            bank_id=req.bank_id or memory.default_bank_id,
            items_count=1,
            operation_id="local-mem-simulated",
        )

    try:
        return await memory.aretain(
            content=req.content,
            bank_id=req.bank_id,
            context=req.context,
            document_id=req.document_id,
            metadata=req.metadata,
            tags=req.tags,
        )
    except ConfigurationError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except HindsightMemoryError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post(
    "/repositories/{repo_id}/memory/bootstrap",
    response_model=BootstrapSummaryResponse,
    status_code=status.HTTP_200_OK,
)
async def bootstrap_repository_memory(
    repo_id: str,
    req: Optional[BootstrapSourceRequest] = None,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    bootstrap: HistoricalBootstrapService = Depends(get_bootstrap_service),
) -> BootstrapSummaryResponse:
    """Discovers, parses, classifies, deduplicates, and retains repository architectural memory."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo and "/" in repo_id:
        repo = await supabase.get_repository_by_name(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )

    try:
        summary = await bootstrap.bootstrap_repository(
            repository_id=repo["id"],
            repository_full_name=repo["full_name"],
            req=req,
        )
        return summary
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        logger.error("Repository memory bootstrap failed for %s: %s", repo_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Repository memory bootstrap failed: {e}",
        )

