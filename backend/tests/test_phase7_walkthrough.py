"""
Phase 7 — Semantic PR Walkthrough Tests.

Tests the WalkthroughEngine's deterministic grouping, ordering,
impact classification, and narrative generation without any external service.

All tests run purely against the WalkthroughEngine with minimal mocked PRContext
objects. No GitHub, Groq, Hindsight, or Supabase calls are made.
"""
import pytest
from unittest.mock import MagicMock

from app.schemas.context import (
    PRContext,
    PRIdentity,
    PRIntent,
    ChangeType,
    ChangedFileDetail,
    CodeDependencyGraph,
    ContextBudget,
    ContextReason,
    DependencyGraphEdge,
    RelatedTestItem,
)
from app.schemas.review import ReviewAnalysisOutput, ReviewFindingSchema
from app.schemas.walkthrough import ChangeGroupKind, ChangeGroupImpact, PRWalkthrough
from app.services.walkthrough_engine import (
    WalkthroughEngine,
    _classify_file,
    get_walkthrough_engine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_identity(pr_number: int = 1, repo_id: str = "repo-1") -> PRIdentity:
    return PRIdentity(
        repository_id=repo_id,
        repository_full_name="org/repo",
        pr_number=pr_number,
        title="Test PR",
        author="dev",
        head_sha="abc123",
        changed_files_count=0,
        total_additions=0,
        total_deletions=0,
    )


def _make_intent(change_type: ChangeType = ChangeType.FEATURE) -> PRIntent:
    return PRIntent(
        summary="Test summary",
        goals=["Goal 1"],
        likely_change_type=change_type,
        affected_domain="auth",
    )


def _make_file(
    path: str,
    status: str = "modified",
    additions: int = 10,
    deletions: int = 5,
    language: str = "python",
    symbols: list | None = None,
) -> ChangedFileDetail:
    return ChangedFileDetail(
        path=path,
        status=status,
        additions=additions,
        deletions=deletions,
        language=language,
        affected_symbols=symbols or [],
    )


def _make_context(
    files: list[ChangedFileDetail],
    pr_number: int = 1,
    repo_id: str = "repo-1",
    change_type: ChangeType = ChangeType.FEATURE,
    related_tests: list | None = None,
) -> PRContext:
    identity = _make_identity(pr_number=pr_number, repo_id=repo_id)
    identity.changed_files_count = len(files)
    identity.total_additions = sum(f.additions for f in files)
    identity.total_deletions = sum(f.deletions for f in files)

    return PRContext(
        pr=identity,
        intent=_make_intent(change_type),
        changed_files=files,
        related_tests=related_tests or [],
        dependencies=CodeDependencyGraph(),
        context_budget=ContextBudget(),
        context_reasons=[ContextReason.DIRECT_CHANGE],
    )


# ---------------------------------------------------------------------------
# 1. File classification
# ---------------------------------------------------------------------------

class TestFileClassification:
    """WN-01 to WN-10: Path-pattern classifiers are correct and deterministic."""

    @pytest.mark.parametrize("path,expected_kind", [
        ("tests/test_auth.py", ChangeGroupKind.TEST),
        ("src/test_utils.py", ChangeGroupKind.TEST),
        ("frontend/components/Button.tsx", ChangeGroupKind.UI),
        ("frontend/pages/Login.tsx", ChangeGroupKind.UI),
        ("app/api/routes.py", ChangeGroupKind.API),
        ("app/api/router.py", ChangeGroupKind.API),
        ("app/controllers/auth.py", ChangeGroupKind.API),
        ("app/services/auth_service.py", ChangeGroupKind.SERVICE),
        ("app/services/review_engine.py", ChangeGroupKind.SERVICE),
        ("migrations/001_add_users.sql", ChangeGroupKind.SCHEMA),
        ("app/models/user.py", ChangeGroupKind.SCHEMA),  # "model" in path → SCHEMA
        ("alembic/versions/abc123.py", ChangeGroupKind.SCHEMA),
        ("requirements.txt", ChangeGroupKind.DEPENDENCY),
        ("package.json", ChangeGroupKind.DEPENDENCY),
        ("pyproject.toml", ChangeGroupKind.DEPENDENCY),    # pyproject.toml is a dependency file
        (".github/workflows/ci.yml", ChangeGroupKind.CONFIGURATION),
        ("README.md", ChangeGroupKind.DOCUMENTATION),
        ("docs/architecture.md", ChangeGroupKind.DOCUMENTATION),
        ("app/types/index.ts", ChangeGroupKind.TYPES),
        ("app/schemas/review.py", ChangeGroupKind.SCHEMA), # "schema" in path → SCHEMA
        ("app/utils/helpers.py", ChangeGroupKind.UTILITY),
        ("lib/constants.ts", ChangeGroupKind.UTILITY),
    ])
    def test_classify_file(self, path: str, expected_kind: ChangeGroupKind):
        """WN-01: File classification is correct for known path patterns."""
        result = _classify_file(path)
        assert result == expected_kind, f"Expected {expected_kind} for '{path}', got {result}"

    def test_unknown_file_returns_unknown(self):
        """WN-02: Files that don't match any pattern return UNKNOWN."""
        result = _classify_file("random_file_with_no_pattern.xyz")
        assert result == ChangeGroupKind.UNKNOWN

    def test_classification_is_case_insensitive(self):
        """WN-03: Classification is case-insensitive."""
        assert _classify_file("TESTS/TEST_AUTH.PY") == ChangeGroupKind.TEST
        assert _classify_file("MIGRATIONS/001.SQL") == ChangeGroupKind.SCHEMA


# ---------------------------------------------------------------------------
# 2. WalkthroughEngine.build_walkthrough — structure
# ---------------------------------------------------------------------------

class TestWalkthroughStructure:
    """WN-11 to WN-20: Walkthrough has correct structural properties."""

    def test_empty_pr_returns_valid_walkthrough(self):
        """WN-11: An empty PR (no files) still returns a valid walkthrough."""
        engine = WalkthroughEngine()
        context = _make_context(files=[])
        result = engine.build_walkthrough(context)

        assert isinstance(result, PRWalkthrough)
        assert result.pr_number == 1
        assert result.groups == []
        assert result.steps == []
        assert result.groups_count == 0

    def test_single_file_pr_creates_one_group(self):
        """WN-12: A PR with one file creates exactly one group."""
        engine = WalkthroughEngine()
        context = _make_context(files=[_make_file("app/services/auth_service.py")])
        result = engine.build_walkthrough(context)

        assert result.groups_count == 1
        assert len(result.groups) == 1
        assert len(result.steps) == 1

    def test_groups_ordered_by_render_order(self):
        """WN-13: Groups are rendered in architectural order (schema before API before tests)."""
        engine = WalkthroughEngine()
        files = [
            _make_file("tests/test_auth.py"),
            _make_file("app/api/routes.py"),
            _make_file("migrations/001.sql"),
        ]
        context = _make_context(files=files)
        result = engine.build_walkthrough(context)

        # Schema (ordering=2) < API (ordering=5) < Test (ordering=9)
        orderings = [g.ordering for g in result.groups]
        assert orderings == sorted(orderings), "Groups are not in ascending render order"

    def test_steps_number_matches_groups(self):
        """WN-14: Number of steps always equals number of groups."""
        engine = WalkthroughEngine()
        files = [
            _make_file("app/services/auth.py"),
            _make_file("app/api/routes.py"),
            _make_file("tests/test_auth.py"),
        ]
        context = _make_context(files=files)
        result = engine.build_walkthrough(context)

        assert len(result.steps) == len(result.groups)

    def test_step_numbers_are_sequential(self):
        """WN-15: Step numbers start at 1 and are sequential."""
        engine = WalkthroughEngine()
        files = [
            _make_file("app/services/auth.py"),
            _make_file("tests/test_auth.py"),
        ]
        context = _make_context(files=files)
        result = engine.build_walkthrough(context)

        for i, step in enumerate(result.steps, start=1):
            assert step.step_number == i

    def test_all_files_are_accounted_for(self):
        """WN-16: Every changed file appears in exactly one group."""
        engine = WalkthroughEngine()
        files = [
            _make_file("app/services/auth.py"),
            _make_file("app/api/routes.py"),
            _make_file("tests/test_auth.py"),
            _make_file("README.md"),
        ]
        context = _make_context(files=files)
        result = engine.build_walkthrough(context)

        grouped_paths = {
            entry.path
            for group in result.groups
            for entry in group.files
        }
        original_paths = {f.path for f in files}
        assert grouped_paths == original_paths

    def test_group_ids_are_unique(self):
        """WN-17: All group IDs are unique."""
        engine = WalkthroughEngine()
        files = [
            _make_file("app/services/auth.py"),
            _make_file("app/api/routes.py"),
        ]
        context = _make_context(files=files)
        result = engine.build_walkthrough(context)

        ids = [g.id for g in result.groups]
        assert len(ids) == len(set(ids)), "Duplicate group IDs detected"

    def test_walkthrough_metadata_correct(self):
        """WN-18: Walkthrough metadata correctly reflects context identity."""
        engine = WalkthroughEngine()
        files = [_make_file("app/services/auth.py", additions=20, deletions=5)]
        context = _make_context(files=files, pr_number=42, repo_id="test-repo-id")
        context.pr.total_additions = 20
        context.pr.total_deletions = 5
        result = engine.build_walkthrough(context)

        assert result.pr_number == 42
        assert result.repository_id == "test-repo-id"
        assert result.total_additions == 20
        assert result.total_deletions == 5
        assert result.derived_from == "context_engine_v5"

    def test_intent_summary_propagated(self):
        """WN-19: Intent summary from PRContext is propagated without modification."""
        engine = WalkthroughEngine()
        context = _make_context(files=[_make_file("app/services/auth.py")])
        context.intent.summary = "Add JWT authentication"
        result = engine.build_walkthrough(context)

        assert result.intent_summary == "Add JWT authentication"

    def test_change_type_propagated(self):
        """WN-20: Change type is propagated from context intent."""
        engine = WalkthroughEngine()
        context = _make_context(
            files=[_make_file("app/services/auth.py")],
            change_type=ChangeType.SECURITY,
        )
        result = engine.build_walkthrough(context)
        assert result.change_type == "SECURITY"


# ---------------------------------------------------------------------------
# 3. Impact estimation
# ---------------------------------------------------------------------------

class TestImpactEstimation:
    """WN-21 to WN-27: Impact levels are correctly estimated via active WalkthroughEngine."""

    def test_schema_changes_are_foundational(self):
        """WN-21: Multi-file schema changes alter persistence boundaries and are FOUNDATIONAL."""
        engine = WalkthroughEngine()
        context = _make_context(files=[
            _make_file("migrations/001.sql"),
            _make_file("app/models/user.py"),
        ])
        result = engine.build_walkthrough(context)
        schema_group = next(g for g in result.groups if g.kind == ChangeGroupKind.SCHEMA)
        assert schema_group.impact == ChangeGroupImpact.FOUNDATIONAL

    def test_dependency_changes_are_foundational_with_structural_consumers(self):
        """WN-22: Dependency group with verified downstream structural consumers is FOUNDATIONAL."""
        engine = WalkthroughEngine()
        context = _make_context(files=[_make_file("requirements.txt")])
        context.dependencies = CodeDependencyGraph(edges=[
            DependencyGraphEdge(source="requirements.txt", target="app/db/session.py", relationship="imports")
        ])
        result = engine.build_walkthrough(context)
        assert result.groups[0].impact == ChangeGroupImpact.FOUNDATIONAL

    def test_unreferenced_dependency_changes_are_contained(self):
        """Bug F: Unreferenced dependency changes without structural consumers are CONTAINED."""
        engine = WalkthroughEngine()
        context = _make_context(files=[_make_file("requirements.txt")])
        result = engine.build_walkthrough(context)
        assert result.groups[0].impact == ChangeGroupImpact.CONTAINED

    def test_types_are_cross_cutting(self):
        """WN-23: Types group with class definitions is CROSS_CUTTING."""
        engine = WalkthroughEngine()
        f = _make_file("app/types/index.ts")
        f.classes = ["UserProfile"]
        context = _make_context(files=[f])
        result = engine.build_walkthrough(context)
        assert result.groups[0].impact == ChangeGroupImpact.CROSS_CUTTING

    def test_api_changes_with_external_caller_are_cross_cutting(self):
        """WN-24: API group with external caller is CROSS_CUTTING."""
        engine = WalkthroughEngine()
        f = _make_file("app/api/routes.py", symbols=["login_endpoint"])
        f.functions = ["login_endpoint"]
        context = _make_context(files=[f])
        context.dependencies = CodeDependencyGraph(edges=[
            DependencyGraphEdge(source="login_endpoint", target="external_auth_provider", relationship="calls")
        ])
        result = engine.build_walkthrough(context)
        assert result.groups[0].impact == ChangeGroupImpact.CROSS_CUTTING

    def test_service_with_api_flow_is_cross_cutting(self):
        """WN-25: Connected Service + API multi-layer flow is FLOW and CROSS_CUTTING."""
        engine = WalkthroughEngine()
        f_api = _make_file("app/controllers/auth_controller.py", symbols=["login"])
        f_api.functions = ["login"]
        f_svc = _make_file("app/services/auth_service.py", symbols=["authenticate"])
        f_svc.functions = ["authenticate"]
        context = _make_context(files=[f_api, f_svc])
        context.dependencies = CodeDependencyGraph(edges=[
            DependencyGraphEdge(source="login", target="authenticate", relationship="calls")
        ])
        result = engine.build_walkthrough(context)
        assert result.groups[0].kind == ChangeGroupKind.FLOW
        assert result.groups[0].impact == ChangeGroupImpact.CROSS_CUTTING

    def test_service_alone_is_contained(self):
        """WN-26: Self-contained Service group is CONTAINED."""
        engine = WalkthroughEngine()
        context = _make_context(files=[_make_file("app/services/auth.py")])
        result = engine.build_walkthrough(context)
        assert result.groups[0].impact == ChangeGroupImpact.CONTAINED

    def test_test_group_is_contained(self):
        """WN-27: Test group is CONTAINED."""
        engine = WalkthroughEngine()
        context = _make_context(files=[_make_file("tests/test_auth.py")])
        result = engine.build_walkthrough(context)
        assert result.groups[0].impact == ChangeGroupImpact.CONTAINED


# ---------------------------------------------------------------------------
# 4. Aggregate flags
# ---------------------------------------------------------------------------

class TestAggregateFlags:
    """WN-28 to WN-33: Aggregate flags correctly reflect group composition."""

    def test_has_schema_changes_flag(self):
        """WN-28: has_schema_changes is True when a schema file is present."""
        engine = WalkthroughEngine()
        context = _make_context(files=[
            _make_file("migrations/001.sql"),
            _make_file("app/services/auth.py"),
        ])
        result = engine.build_walkthrough(context)
        assert result.has_schema_changes is True

    def test_no_schema_flag_when_absent(self):
        """WN-29: has_schema_changes is False when no schema files present."""
        engine = WalkthroughEngine()
        context = _make_context(files=[_make_file("app/services/auth.py")])
        result = engine.build_walkthrough(context)
        assert result.has_schema_changes is False

    def test_has_test_changes_flag(self):
        """WN-30: has_test_changes is True when a test file is present."""
        engine = WalkthroughEngine()
        context = _make_context(files=[
            _make_file("tests/test_auth.py"),
            _make_file("app/services/auth.py"),
        ])
        result = engine.build_walkthrough(context)
        assert result.has_test_changes is True

    def test_has_api_changes_flag(self):
        """WN-31: has_api_changes is True when an API file is present."""
        engine = WalkthroughEngine()
        context = _make_context(files=[_make_file("app/api/routes.py")])
        result = engine.build_walkthrough(context)
        assert result.has_api_changes is True

    def test_has_config_changes_flag(self):
        """WN-32: has_config_changes is True when a config file is present."""
        engine = WalkthroughEngine()
        context = _make_context(files=[_make_file(".github/workflows/ci.yml")])
        result = engine.build_walkthrough(context)
        assert result.has_config_changes is True

    def test_total_files_changed_count(self):
        """WN-33: total_files_changed reflects the number of files in context."""
        engine = WalkthroughEngine()
        files = [
            _make_file("app/services/auth.py"),
            _make_file("tests/test_auth.py"),
            _make_file("README.md"),
        ]
        context = _make_context(files=files)
        result = engine.build_walkthrough(context)
        assert result.total_files_changed == 3


# ---------------------------------------------------------------------------
# 5. Test coverage detection
# ---------------------------------------------------------------------------

class TestCoverageDetection:
    """WN-34 to WN-37: Test coverage flags are correctly derived."""

    def test_group_has_test_coverage_from_related_tests(self):
        """WN-34: A non-test group has has_test_coverage=True when a related test exists."""
        engine = WalkthroughEngine()
        related_tests = [
            RelatedTestItem(
                test_file_path="tests/test_auth.py",
                target_symbol_or_file="app/services/auth.py",
                exists_in_repo=True,
                is_in_pr_diff=False,
            )
        ]
        context = _make_context(
            files=[_make_file("app/services/auth.py")],
            related_tests=related_tests,
        )
        result = engine.build_walkthrough(context)

        service_group = next((g for g in result.groups if g.kind == ChangeGroupKind.SERVICE), None)
        assert service_group is not None
        assert service_group.has_test_coverage is True

    def test_group_no_test_coverage_when_absent(self):
        """WN-35: A service group has has_test_coverage=False when no tests exist."""
        engine = WalkthroughEngine()
        context = _make_context(
            files=[_make_file("app/services/auth.py")],
            related_tests=[],
        )
        result = engine.build_walkthrough(context)

        service_group = next((g for g in result.groups if g.kind == ChangeGroupKind.SERVICE), None)
        assert service_group is not None
        assert service_group.has_test_coverage is False

    def test_test_group_always_has_coverage(self):
        """WN-36: TEST groups always have has_test_coverage=True."""
        engine = WalkthroughEngine()
        context = _make_context(files=[_make_file("tests/test_something.py")])
        result = engine.build_walkthrough(context)

        test_group = next((g for g in result.groups if g.kind == ChangeGroupKind.TEST), None)
        assert test_group is not None
        assert test_group.has_test_coverage is True

    def test_test_in_pr_diff_detected(self):
        """WN-37: A test file that appears in the PR diff itself is detected as covering."""
        engine = WalkthroughEngine()
        related_tests = [
            RelatedTestItem(
                test_file_path="tests/test_auth.py",
                target_symbol_or_file="app/services/auth.py",
                exists_in_repo=True,
                is_in_pr_diff=True,
            )
        ]
        context = _make_context(
            files=[
                _make_file("app/services/auth.py"),
                _make_file("tests/test_auth.py"),
            ],
            related_tests=related_tests,
        )
        result = engine.build_walkthrough(context)
        service_group = next((g for g in result.groups if g.kind == ChangeGroupKind.SERVICE), None)
        assert service_group is not None
        assert service_group.has_test_coverage is True


# ---------------------------------------------------------------------------
# 6. Finding overlap
# ---------------------------------------------------------------------------

class TestFindingOverlap:
    """WN-38 to WN-40: Review finding overlap is counted correctly."""

    def test_finding_overlap_counted(self):
        """WN-38: related_findings_count reflects how many finding files are in the group."""
        engine = WalkthroughEngine()
        context = _make_context(files=[
            _make_file("app/services/auth.py"),
            _make_file("app/api/routes.py"),
        ])

        review = ReviewAnalysisOutput(
            summary="test",
            drift_detected=False,
            exception_approved=False,
            verdict="DRIFT",
            findings=[
                ReviewFindingSchema(
                    category="architecture_drift",
                    severity="error",
                    file_path="app/services/auth.py",
                    title="Drift",
                    message="Detected drift",
                )
            ],
        )

        result = engine.build_walkthrough(context, review=review)

        service_group = next((g for g in result.groups if g.kind == ChangeGroupKind.SERVICE), None)
        assert service_group is not None
        assert service_group.related_findings_count == 1

    def test_no_review_zero_finding_overlap(self):
        """WN-39: When review is None, all groups have related_findings_count=0."""
        engine = WalkthroughEngine()
        context = _make_context(files=[_make_file("app/services/auth.py")])
        result = engine.build_walkthrough(context, review=None)

        for group in result.groups:
            assert group.related_findings_count == 0

    def test_surfaced_finding_count_is_aggregate(self):
        """WN-40: surfaced_finding_count is the sum across all groups."""
        engine = WalkthroughEngine()
        context = _make_context(files=[
            _make_file("app/services/auth.py"),
            _make_file("app/api/routes.py"),
        ])
        review = ReviewAnalysisOutput(
            summary="test",
            drift_detected=True,
            exception_approved=False,
            verdict="DRIFT",
            findings=[
                ReviewFindingSchema(
                    category="architecture_drift",
                    severity="error",
                    file_path="app/services/auth.py",
                    title="Drift A",
                    message="Detected drift",
                ),
                ReviewFindingSchema(
                    category="architecture_drift",
                    severity="warning",
                    file_path="app/api/routes.py",
                    title="Drift B",
                    message="Detected drift",
                ),
            ],
        )
        result = engine.build_walkthrough(context, review=review)
        assert result.surfaced_finding_count == 2


# ---------------------------------------------------------------------------
# 7. Singleton factory
# ---------------------------------------------------------------------------

class TestFactory:
    """WN-41: Factory returns a WalkthroughEngine singleton."""

    def test_get_walkthrough_engine_returns_instance(self):
        """WN-41: get_walkthrough_engine() returns a WalkthroughEngine."""
        engine = get_walkthrough_engine()
        assert isinstance(engine, WalkthroughEngine)

    def test_get_walkthrough_engine_is_singleton(self):
        """WN-42: Repeated calls return the same object."""
        e1 = get_walkthrough_engine()
        e2 = get_walkthrough_engine()
        assert e1 is e2
