from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class FixCandidateStatus(str, Enum):
    """Lifecycle states of a proposed context-aware fix."""
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class FixValidationStatus(str, Enum):
    """Pre-apply and structural validation states."""
    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class FindingResolutionStatus(str, Enum):
    """Post-apply verification result for original finding."""
    RESOLVED = "RESOLVED"
    STILL_PRESENT = "STILL_PRESENT"
    UNKNOWN = "UNKNOWN"


class ValidationCheckResult(BaseModel):
    """Single validation check outcome."""
    check_name: str
    passed: bool
    message: str

    @property
    def check_id(self) -> str:
        return self.check_name

    @property
    def status(self) -> FixValidationStatus:
        return FixValidationStatus.PASSED if self.passed else FixValidationStatus.FAILED


class PostApplyValidation(BaseModel):
    """Verification results evaluated after patch application."""
    finding_status: FindingResolutionStatus = FindingResolutionStatus.UNKNOWN
    validation_status: FixValidationStatus = FixValidationStatus.NOT_RUN
    new_findings_count: int = 0
    details: List[str] = Field(default_factory=list)

    @property
    def original_finding_status(self) -> FindingResolutionStatus:
        return self.finding_status


class FixCandidate(BaseModel):
    """
    A typed, context-aware fix candidate proposed for an architectural review finding.

    Strictly scoped to a specific repository and base commit SHA.
    Contains grounded structural constraints, unified diff patch, validation checks,
    and approval state.
    """
    fix_id: str
    finding_id: str
    repository_id: str
    pr_number: int
    base_sha: str
    title: str
    explanation: str
    affected_files: List[str] = Field(default_factory=list)
    affected_symbols: List[str] = Field(default_factory=list)
    proposed_change: str
    rationale: str
    applicable_architectural_constraint: Optional[str] = None
    precedent_source: Optional[str] = None
    confidence_status: str = "verified"  # verified | unverified | low_confidence
    patch: str  # Unified diff format
    status: FixCandidateStatus = FixCandidateStatus.DRAFT
    validation_status: FixValidationStatus = FixValidationStatus.NOT_RUN
    validation_checks: List[ValidationCheckResult] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    relevant_tests: List[str] = Field(default_factory=list)
    post_apply: Optional[PostApplyValidation] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def model_validate(cls, obj: Any, *args, **kwargs):
        if isinstance(obj, dict):
            if "fix_id" not in obj and "id" in obj:
                obj["fix_id"] = obj["id"]
            elif "fix_id" not in obj:
                obj["fix_id"] = "fix-candidate"
            if "patch" not in obj and "patch_diff" in obj:
                obj["patch"] = obj["patch_diff"]
        return super().model_validate(obj, *args, **kwargs)

    def __init__(self, **data: Any):
        if "fix_id" not in data and "id" in data:
            data["fix_id"] = data["id"]
        elif "fix_id" not in data:
            data["fix_id"] = "fix-candidate"
        if "patch" not in data and "patch_diff" in data:
            data["patch"] = data["patch_diff"]
        super().__init__(**data)

    @property
    def id(self) -> str:
        return self.fix_id

    @property
    def patch_diff(self) -> str:
        return self.patch

    @patch_diff.setter
    def patch_diff(self, value: str) -> None:
        self.patch = value

    @property
    def applicable_constraint(self) -> Optional[str]:
        return self.applicable_architectural_constraint

    @property
    def is_approved(self) -> bool:
        return self.status == FixCandidateStatus.APPROVED

    @property
    def post_apply_validation(self) -> Optional[PostApplyValidation]:
        return self.post_apply


class GenerateFixesRequest(BaseModel):
    """Request payload to generate fix candidates for specific or all findings."""
    finding_ids: Optional[List[str]] = None
    finding_id: Optional[str] = None
    force_regenerate: bool = False

    def get_effective_finding_ids(self) -> Optional[List[str]]:
        if self.finding_ids:
            return self.finding_ids
        if self.finding_id:
            return [self.finding_id]
        return None


class FixListResponse(BaseModel):
    """Container for all fixes associated with a PR review."""
    repository_id: str
    pr_number: int
    head_sha: str
    fixes: List[FixCandidate] = Field(default_factory=list)
    total_count: int = 0
    can_apply: bool = False
    unavailable_reasons: Dict[str, str] = Field(default_factory=dict)

