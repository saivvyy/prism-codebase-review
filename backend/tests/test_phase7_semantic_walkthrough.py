"""
Phase 7 — Semantic PR Walkthrough Behavioral & Adversarial Test Suite.

Proves genuine semantic behavior, structural graph evidence consumption,
symbol-aware test mapping, correct finding mapping, bounding, determinism,
repository/SHA isolation, and real FastAPI HTTP endpoint behavior.
"""
import copy
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.services.context_engine import ContextEngine, get_context_engine
from app.services.review_engine import ReviewEngine, get_review_engine
from app.services.supabase import SupabaseService, get_supabase_service
from app.services.walkthrough_engine import WalkthroughEngine, get_walkthrough_engine
from app.schemas.context import (
    ChangeType,
    ChangedFileDetail,
    CodeDependencyGraph,
    ContextBudget,
    ContextReason,
    DependencyGraphEdge,
    HunkSymbolMap,
    PRContext,
    PRIdentity,
    PRIntent,
    RelatedFileItem,
    RelatedTestItem,
    SymbolDefinitionReference,
)
from app.schemas.github import GitHubPR, PRDetailResponse
from app.schemas.review import HistoricalEvidenceSchema, ReviewAnalysisOutput, ReviewFindingSchema
from app.schemas.walkthrough import (
    ChangeGroup,
    ChangeGroupImpact,
    ChangeGroupKind,
    PRWalkthrough,
    SemanticRelationshipType,
)
from app.services.walkthrough_engine import WalkthroughEngine, get_walkthrough_engine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_identity(pr_number: int = 1, repo_id: str = "repo-1", head_sha: str = "sha-abc-123") -> PRIdentity:
    return PRIdentity(
        repository_id=repo_id,
        repository_full_name="org/payments-repo",
        pr_number=pr_number,
        title="Add payment settlement flow",
        author="alice",
        base_sha="sha-base-000",
        head_sha=head_sha,
        changed_files_count=0,
        total_additions=0,
        total_deletions=0,
    )


def _make_intent(summary: str = "Add payment settlement flow") -> PRIntent:
    return PRIntent(
        summary=summary,
        goals=["Process payments", "Notify settlement"],
        likely_change_type=ChangeType.FEATURE,
        affected_domain="payments",
    )


def _make_file(
    path: str,
    status: str = "modified",
    additions: int = 10,
    deletions: int = 2,
    symbols: list | None = None,
    classes: list | None = None,
    functions: list | None = None,
) -> ChangedFileDetail:
    return ChangedFileDetail(
        path=path,
        status=status,
        additions=additions,
        deletions=deletions,
        language="python" if path.endswith(".py") else "typescript",
        affected_symbols=symbols or [],
        classes=classes or [],
        functions=functions or [],
    )


# ---------------------------------------------------------------------------
# 25 Adversarial & Behavioral Tests
# ---------------------------------------------------------------------------

class TestPhase7SemanticBehavior:

    # 1. PaymentController + PaymentService + PaymentRepository + PaymentTest form coherent flow
    def test_payment_flow_coherent_semantic_grouping(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("src/controllers/PaymentController.ts", classes=["PaymentController"]),
            _make_file("src/services/PaymentService.ts", classes=["PaymentService"]),
            _make_file("src/repositories/PaymentRepository.ts", classes=["PaymentRepository"]),
            _make_file("tests/payment/PaymentTest.ts", functions=["test_settlement"]),
        ]
        edges = [
            DependencyGraphEdge(source="PaymentController", target="PaymentService", relationship="calls"),
            DependencyGraphEdge(source="PaymentService", target="PaymentRepository", relationship="calls"),
        ]
        tests = [
            RelatedTestItem(
                test_file_path="tests/payment/PaymentTest.ts",
                target_symbol_or_file="PaymentService",
                is_in_pr_diff=True,
            )
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=edges),
            related_tests=tests,
            context_budget=ContextBudget(),
        )

        res = engine.build_walkthrough(context)
        # All 4 connected files must form 1 coherent semantic group across layers
        assert res.groups_count == 1
        group = res.groups[0]
        group_file_paths = {f.path for f in group.files}
        assert group_file_paths == {
            "src/controllers/PaymentController.ts",
            "src/services/PaymentService.ts",
            "src/repositories/PaymentRepository.ts",
            "tests/payment/PaymentTest.ts",
        }
        assert group.has_test_coverage is True
        assert len(group.relationships) >= 2
        assert group.kind == ChangeGroupKind.FLOW
        assert group.impact == ChangeGroupImpact.CROSS_CUTTING

    # 2. Actual AST dependency edge affects impact
    def test_actual_ast_dependency_edge_affects_impact(self):
        engine = WalkthroughEngine()
        # Internal files
        files = [
            _make_file("src/services/InternalHelper.ts", symbols=["formatReceipt"]),
            _make_file("src/services/HelperConsumer.ts", symbols=["useReceipt"]),
        ]
        edges = [
            DependencyGraphEdge(source="useReceipt", target="formatReceipt", relationship="calls"),
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=edges),
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        # Calls are internal within cluster, no external callers -> CONTAINED
        assert res.groups[0].impact == ChangeGroupImpact.CONTAINED

        # Now add external caller to outside module
        external_edge = DependencyGraphEdge(
            source="external/AppRouter.ts", target="src/services/InternalHelper.ts", relationship="calls"
        )
        context_with_ext = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=edges + [external_edge]),
            context_budget=ContextBudget(),
        )
        res_ext = engine.build_walkthrough(context_with_ext)
        assert res_ext.groups[0].impact == ChangeGroupImpact.CROSS_CUTTING

    # 3. Misleading filename does not determine semantic role
    def test_misleading_filename_does_not_determine_semantic_role(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("src/api/payment_endpoint.py", classes=["PaymentEndpoint"]),
            _make_file("src/utils/payment_migration_helper.py", classes=["PaymentCoreService"]),
        ]
        # Real AST edge connects payment_endpoint to payment_migration_helper
        edges = [
            DependencyGraphEdge(
                source="src/api/payment_endpoint.py",
                target="src/utils/payment_migration_helper.py",
                relationship="calls",
            )
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=edges),
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        # Connected by real AST edge, not split into SCHEMA/UTILITY
        assert res.groups_count == 1
        paths = {f.path for f in res.groups[0].files}
        assert "src/utils/payment_migration_helper.py" in paths

    # 4. Misleading schema filename does not determine semantic role
    def test_misleading_schema_filename_does_not_determine_semantic_role(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("app/api/checkout.py", functions=["checkout_order"]),
            _make_file("app/fake_schema_handler.py", functions=["handle_checkout"]),
        ]
        edges = [
            DependencyGraphEdge(
                source="app/api/checkout.py", target="app/fake_schema_handler.py", relationship="calls"
            )
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=edges),
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        assert res.groups_count == 1
        assert "fake_schema_handler.py" in res.groups[0].files[1].path or "fake_schema_handler.py" in res.groups[0].files[0].path

    # 5. Middleware/gateway files receive meaningful grouping from structural evidence
    def test_middleware_gateway_grouping_from_structural_evidence(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("src/middleware/AuthMiddleware.ts", classes=["AuthMiddleware"]),
            _make_file("src/gateways/AuthGateway.ts", classes=["AuthGateway"]),
            _make_file("src/services/AuthService.ts", classes=["AuthService"]),
        ]
        edges = [
            DependencyGraphEdge(source="AuthMiddleware", target="AuthService", relationship="calls"),
            DependencyGraphEdge(source="AuthGateway", target="AuthService", relationship="imports"),
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent("Refactor auth pipeline"),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=edges),
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        assert res.groups_count == 1
        assert len(res.groups[0].files) == 3

    # 6. Symbol-level test mapping works
    def test_symbol_level_test_mapping(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("src/services/SettlementService.ts", functions=["executeSettlement"]),
        ]
        tests = [
            RelatedTestItem(
                test_file_path="tests/unit/test_settlement.ts",
                target_symbol_or_file="executeSettlement",  # Symbol target, not file path
                is_in_pr_diff=False,
            )
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            related_tests=tests,
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        group = res.groups[0]
        assert group.has_test_coverage is True
        assert len(group.tests) == 1
        assert group.tests[0].target_symbol_or_file == "executeSettlement"
        assert group.tests[0].test_file_path == "tests/unit/test_settlement.ts"

    # 7. Multiple findings on one file count correctly
    def test_multiple_findings_on_one_file_count_correctly(self):
        engine = WalkthroughEngine()
        files = [_make_file("src/services/PaymentService.ts")]
        review = ReviewAnalysisOutput(
            summary="Review with 3 findings on single file",
            verdict="DRIFT",
            findings=[
                ReviewFindingSchema(
                    category="architecture_drift",
                    severity="error",
                    file_path="src/services/PaymentService.ts",
                    line_number=10,
                    title="Finding 1",
                    message="Msg 1",
                ),
                ReviewFindingSchema(
                    category="rejected_pattern",
                    severity="warning",
                    file_path="src/services/PaymentService.ts",
                    line_number=25,
                    title="Finding 2",
                    message="Msg 2",
                ),
                ReviewFindingSchema(
                    category="convention_violation",
                    severity="info",
                    file_path="src/services/PaymentService.ts",
                    line_number=40,
                    title="Finding 3",
                    message="Msg 3",
                ),
            ],
        )
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context, review=review)
        assert res.groups[0].related_findings_count == 3
        assert res.surfaced_finding_count == 3
        assert len(res.groups[0].findings) == 3

    # 8. Finding details are attached
    def test_finding_details_are_attached(self):
        engine = WalkthroughEngine()
        files = [_make_file("src/services/PaymentService.ts")]
        review = ReviewAnalysisOutput(
            summary="Review",
            verdict="DRIFT",
            findings=[
                ReviewFindingSchema(
                    category="architecture_drift",
                    severity="error",
                    file_path="src/services/PaymentService.ts",
                    line_number=42,
                    title="Persistence boundary violation",
                    message="Controllers must not query DB directly",
                    historical_pr_ref="PR #142",
                    rationale="Keep persistence abstracted",
                )
            ],
        )
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context, review=review)
        finding = res.groups[0].findings[0]
        assert finding.title == "Persistence boundary violation"
        assert finding.historical_pr_ref == "PR #142"
        assert finding.line_number == 42
        assert finding.rationale == "Keep persistence abstracted"

    # 9. 300 modified files are bounded
    def test_300_modified_files_are_bounded(self):
        engine = WalkthroughEngine()
        # 300 files in one group
        files = [_make_file(f"src/generated/file_{i}.py", additions=1, deletions=0) for i in range(300)]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent("Bulk generated code"),
            changed_files=files,
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        group = res.groups[0]
        assert len(group.files) <= 50
        assert group.total_files_count == 300
        assert group.truncated_files >= 250

    # 10. Actual walkthrough HTTP endpoint works (Real FastAPI TestClient)
    def test_walkthrough_http_endpoint_real_route(self):
        client = TestClient(app)
        mock_supabase = MagicMock()
        mock_supabase.get_repository_by_id = AsyncMock(return_value={
            "id": "repo-real-123",
            "full_name": "org/real-repo",
            "default_branch": "main",
        })
        mock_supabase.get_latest_review_for_pr = AsyncMock(return_value={
            "summary": "Review OK",
            "verdict": "CONFORMING",
            "findings": [
                {
                    "category": "note",
                    "severity": "info",
                    "file_path": "src/services/payment.py",
                    "line_number": 5,
                    "title": "Clean structure",
                    "message": "Looks good",
                }
            ],
        })

        mock_context_engine = MagicMock()
        mock_context = PRContext(
            pr=_make_identity(pr_number=99, repo_id="repo-real-123", head_sha="sha-real-999"),
            intent=_make_intent("Real API PR Walkthrough"),
            changed_files=[
                _make_file("src/services/payment.py", symbols=["settle_charge"]),
                _make_file("tests/test_payment.py", functions=["test_settle"]),
            ],
            related_tests=[
                RelatedTestItem(
                    test_file_path="tests/test_payment.py",
                    target_symbol_or_file="settle_charge",
                    is_in_pr_diff=True,
                )
            ],
            context_budget=ContextBudget(),
        )
        mock_context_engine.build_pr_context = AsyncMock(return_value=mock_context)

        mock_review_engine = MagicMock()
        mock_review_engine.github = MagicMock()
        mock_review_engine.github.get_pr_details_full = AsyncMock(return_value=PRDetailResponse(
            pr=GitHubPR(
                id=101,
                number=99,
                title="Real API PR Walkthrough",
                body="Description",
                author="bob",
                head_sha="sha-real-999",
                base_sha="sha-base",
                base_branch="main",
                state="open",
            ),
            files=[],
        ))

        app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
        app.dependency_overrides[get_context_engine] = lambda: mock_context_engine
        app.dependency_overrides[get_review_engine] = lambda: mock_review_engine

        try:
            response = client.get("/api/v1/reviews/repo-real-123/99/walkthrough")
            assert response.status_code == 200
            data = response.json()
            assert data["pr_number"] == 99
            assert data["repository_id"] == "repo-real-123"
            assert data["head_sha"] == "sha-real-999"
            assert "groups" in data
            assert len(data["groups"]) > 0
            group = data["groups"][0]
            assert len(group["files"]) > 0
            assert "settle_charge" in group["symbols_changed"]
            assert group["has_test_coverage"] is True
            assert len(group["tests"]) == 1
            assert group["tests"][0]["target_symbol_or_file"] == "settle_charge"
            assert data["surfaced_finding_count"] == 1
            assert len(group["findings"]) == 1
            assert group["findings"][0]["title"] == "Clean structure"
            assert "relationships" in group
        finally:
            app.dependency_overrides.clear()

    # 11. Same PR number across repositories remains isolated
    def test_repo_isolation_same_pr_number(self):
        engine = WalkthroughEngine()
        ctx_repo_a = PRContext(
            pr=_make_identity(pr_number=5, repo_id="repo-A", head_sha="sha-A"),
            intent=_make_intent("Repo A PR"),
            changed_files=[_make_file("src/a.py")],
            context_budget=ContextBudget(),
        )
        ctx_repo_b = PRContext(
            pr=_make_identity(pr_number=5, repo_id="repo-B", head_sha="sha-B"),
            intent=_make_intent("Repo B PR"),
            changed_files=[_make_file("src/b.py")],
            context_budget=ContextBudget(),
        )
        res_a = engine.build_walkthrough(ctx_repo_a)
        res_b = engine.build_walkthrough(ctx_repo_b)

        assert res_a.repository_id == "repo-A"
        assert res_b.repository_id == "repo-B"
        assert res_a.groups[0].files[0].path == "src/a.py"
        assert res_b.groups[0].files[0].path == "src/b.py"

    # 12. Renamed/deleted files are represented correctly
    def test_renamed_deleted_files_represented_correctly(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("old_auth.py", status="removed", additions=0, deletions=50),
            _make_file("new_auth.py", status="added", additions=60, deletions=0),
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        file_entries = {f.path: f for g in res.groups for f in g.files}
        assert file_entries["old_auth.py"].status == "removed"
        assert file_entries["old_auth.py"].note == "File deleted."
        assert file_entries["new_auth.py"].status == "added"
        assert file_entries["new_auth.py"].note == "New file added."

    # 13. Indirect dependency blast radius is detected where Phase 5 evidence supports it
    def test_indirect_dependency_blast_radius(self):
        engine = WalkthroughEngine()
        files = [_make_file("src/services/CoreTokenService.ts", symbols=["TokenService"])]
        edges = [
            DependencyGraphEdge(source="src/api/ExternalGateway.ts", target="TokenService", relationship="calls"),
            DependencyGraphEdge(source="src/cli/AdminTool.ts", target="TokenService", relationship="calls"),
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=edges),
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        assert res.groups[0].impact == ChangeGroupImpact.CROSS_CUTTING
        assert "cross-boundary" in res.groups[0].impact_justification.lower() or "external" in res.groups[0].impact_justification.lower()

    # 14. Auth and Payments changes can form separate semantic groups
    def test_auth_and_payments_form_separate_semantic_groups(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("src/auth/auth_controller.py", symbols=["login"]),
            _make_file("src/auth/auth_service.py", symbols=["verify_creds"]),
            _make_file("src/payment/payment_controller.py", symbols=["charge"]),
            _make_file("src/payment/payment_service.py", symbols=["execute_payment"]),
        ]
        edges = [
            DependencyGraphEdge(source="login", target="verify_creds", relationship="calls"),
            DependencyGraphEdge(source="charge", target="execute_payment", relationship="calls"),
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent("Multi-feature PR: Auth & Payment"),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=edges),
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        # Structural edges connect Auth together and Payment together -> 2 distinct groups!
        assert res.groups_count == 2
        labels = {g.label for g in res.groups}
        assert any("Auth" in l for l in labels)
        assert any("Payment" in l for l in labels)

    # 15. Empty/malformed context does not crash
    def test_empty_or_malformed_context_does_not_crash(self):
        engine = WalkthroughEngine()
        empty_ctx = PRContext(
            pr=_make_identity(),
            intent=PRIntent(summary=""),
            changed_files=[],
            dependencies=CodeDependencyGraph(),
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(empty_ctx)
        assert res.groups_count == 0
        assert res.total_files_changed == 0

    # 16. Same SHA produces identical walkthrough
    def test_same_sha_produces_identical_walkthrough(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("app/api.py", symbols=["fn_a"]),
            _make_file("app/service.py", symbols=["fn_b"]),
        ]
        edges = [DependencyGraphEdge(source="fn_a", target="fn_b", relationship="calls")]
        context1 = PRContext(
            pr=_make_identity(head_sha="fixed-sha-42"),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=edges),
            context_budget=ContextBudget(),
        )
        context2 = copy.deepcopy(context1)

        res1 = engine.build_walkthrough(context1)
        res2 = engine.build_walkthrough(context2)
        assert res1.model_dump_json() == res2.model_dump_json()

    # 17. Changed SHA produces different walkthrough context
    def test_changed_sha_produces_different_walkthrough_context(self):
        engine = WalkthroughEngine()
        files = [_make_file("app/service.py")]
        ctx_sha1 = PRContext(
            pr=_make_identity(head_sha="sha-version-1"),
            intent=_make_intent(),
            changed_files=files,
            context_budget=ContextBudget(),
        )
        ctx_sha2 = PRContext(
            pr=_make_identity(head_sha="sha-version-2"),
            intent=_make_intent(),
            changed_files=files,
            context_budget=ContextBudget(),
        )
        res1 = engine.build_walkthrough(ctx_sha1)
        res2 = engine.build_walkthrough(ctx_sha2)
        assert res1.head_sha == "sha-version-1"
        assert res2.head_sha == "sha-version-2"
        assert res1.head_sha != res2.head_sha

    # 18. Actual CALLS edge produces a relationship
    def test_actual_calls_edge_produces_relationship(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("app/controller.py", symbols=["run_job"]),
            _make_file("app/worker.py", symbols=["perform_work"]),
        ]
        edges = [DependencyGraphEdge(source="run_job", target="perform_work", relationship="calls")]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=edges),
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        rels = res.groups[0].relationships
        assert len(rels) >= 1
        assert any(r.relationship == SemanticRelationshipType.CALLS for r in rels)

    # 19. Unrelated API + Service do not become cross-cutting merely because of category
    def test_unrelated_api_and_service_do_not_become_cross_cutting_merely_by_category(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("app/api/health.py"),  # isolated health check endpoint
            _make_file("app/services/pdf_generator.py"),  # isolated worker
        ]
        # No dependency edges connecting them
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=[]),
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        # Each group has no external callers or cross-boundary dependencies
        for g in res.groups:
            assert g.impact == ChangeGroupImpact.CONTAINED

    # 20. Single file with multiple changed symbols is handled safely
    def test_single_file_multiple_changed_symbols_handled_safely(self):
        engine = WalkthroughEngine()
        hunks = [
            HunkSymbolMap(
                file_path="app/auth.py",
                start_line=1,
                end_line=20,
                enclosing_class="AuthManager",
                changed_symbols=["login", "logout"],
                changed_calls=["hash_password"],
            ),
            HunkSymbolMap(
                file_path="app/auth.py",
                start_line=50,
                end_line=80,
                enclosing_function="refresh_token",
                changed_symbols=["refresh_token"],
                changed_calls=["sign_jwt"],
            ),
        ]
        files = [
            _make_file(
                "app/auth.py",
                classes=["AuthManager"],
                functions=["login", "logout", "refresh_token"],
                symbols=["AuthManager", "login", "logout", "refresh_token"],
            )
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            changed_hunks=hunks,
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        f_entry = res.groups[0].files[0]
        assert len(f_entry.hunks) == 2
        assert "login" in f_entry.affected_symbols

    # 21. Missing dependency graph is handled safely
    def test_missing_dependency_graph_handled_safely(self):
        engine = WalkthroughEngine()
        files = [_make_file("app/service.py")]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(nodes=[], edges=[]),
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        assert res.groups_count == 1
        assert res.groups[0].relationships == []

    # 22. Missing definitions/references are handled safely
    def test_missing_definitions_references_handled_safely(self):
        engine = WalkthroughEngine()
        files = [_make_file("app/service.py")]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            definitions=[],
            references=[],
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        assert res.groups_count == 1

    # 23. Duplicate relationships are eliminated
    def test_duplicate_relationships_eliminated(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("app/a.py", symbols=["fn_a"]),
            _make_file("app/b.py", symbols=["fn_b"]),
        ]
        # Duplicate edges
        edges = [
            DependencyGraphEdge(source="fn_a", target="fn_b", relationship="calls"),
            DependencyGraphEdge(source="fn_a", target="fn_b", relationship="calls"),
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=edges),
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        rels = res.groups[0].relationships
        keys = [(r.source, r.relationship, r.target) for r in rels]
        assert len(keys) == len(set(keys))

    # 24. Finding is mapped to the correct group
    def test_finding_is_mapped_to_correct_group(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("app/payment.py", symbols=["payment_fn"]),
            _make_file("app/auth.py", symbols=["auth_fn"]),
        ]
        review = ReviewAnalysisOutput(
            summary="Review",
            verdict="DRIFT",
            findings=[
                ReviewFindingSchema(
                    category="architecture_drift",
                    severity="error",
                    file_path="app/payment.py",
                    title="Payment bug",
                    message="Error in payment",
                )
            ],
        )
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context, review=review)
        payment_group = next(g for g in res.groups if any(f.path == "app/payment.py" for f in g.files))
        auth_group = next(g for g in res.groups if any(f.path == "app/auth.py" for f in g.files))

        assert payment_group.related_findings_count == 1
        assert len(payment_group.findings) == 1
        assert payment_group.findings[0].title == "Payment bug"
        assert auth_group.related_findings_count == 0
        assert len(auth_group.findings) == 0

    # 25. Test can map through a symbol rather than exact file path
    def test_test_can_map_through_symbol_rather_than_exact_file_path(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("src/domain/ledger/TransactionEntry.ts", classes=["TransactionEntry"]),
        ]
        tests = [
            RelatedTestItem(
                test_file_path="tests/ledger_spec.ts",
                target_symbol_or_file="TransactionEntry",  # Class symbol name
                is_in_pr_diff=True,
            )
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            related_tests=tests,
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)
        group = res.groups[0]
        assert group.has_test_coverage is True
        assert len(group.tests) == 1
        assert group.tests[0].target_symbol_or_file == "TransactionEntry"
        assert group.tests[0].is_in_pr_diff is True

    # 26. Behavioral Test A: API -> Service -> Repository connected flow
    def test_flow_classification_for_connected_api_service_repo(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("app/api/order_controller.py", symbols=["create_order_endpoint"]),
            _make_file("app/services/order_service.py", symbols=["OrderService.process"]),
            _make_file("app/repositories/order_repo.py", symbols=["OrderRepository.save"]),
        ]
        edges = [
            DependencyGraphEdge(source="create_order_endpoint", target="OrderService.process", relationship="calls"),
            DependencyGraphEdge(source="OrderService.process", target="OrderRepository.save", relationship="calls"),
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=edges),
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)

        # A: Must produce exactly ONE connected group
        assert len(res.groups) == 1
        group = res.groups[0]
        # Must be classified as FLOW because structural edges connect multi-layer changes
        assert group.kind == ChangeGroupKind.FLOW
        # FLOW impact logic exercised: multi-layer flow has cross-cutting impact
        assert group.impact == ChangeGroupImpact.CROSS_CUTTING
        assert "Multi-layer flow" in group.impact_justification or "flow" in group.impact_justification.lower()

    # 27. Behavioral Test B: Unrelated API file + unrelated Service file with no structural edge
    def test_unrelated_api_service_different_stems_not_flow(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("app/api/billing_controller.py", symbols=["get_bill"]),
            _make_file("app/services/email_notification_service.py", symbols=["send_email"]),
        ]
        # No edges connecting them
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=[]),
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)

        # B: Must produce separate groups, NEITHER falsely classified as FLOW
        assert len(res.groups) == 2
        for g in res.groups:
            assert g.kind != ChangeGroupKind.FLOW
            assert g.impact == ChangeGroupImpact.CONTAINED

    # 28. Behavioral Test C: API -> Service flow with an external caller
    def test_flow_with_external_caller_cross_cutting(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("app/api/auth_controller.py", symbols=["login_handler"]),
            _make_file("app/services/auth_service.py", symbols=["AuthService.authenticate"]),
        ]
        edges = [
            DependencyGraphEdge(source="login_handler", target="AuthService.authenticate", relationship="calls"),
        ]
        definitions = [
            SymbolDefinitionReference(
                symbol="login_handler",
                file_path="app/api/auth_controller.py",
                definition_type="function",
                callers=["external_gateway_router"],  # External caller outside changed files
            )
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            dependencies=CodeDependencyGraph(edges=edges),
            definitions=definitions,
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)

        assert len(res.groups) == 1
        group = res.groups[0]
        assert group.kind == ChangeGroupKind.FLOW
        assert group.impact == ChangeGroupImpact.CROSS_CUTTING
        assert "flow" in group.impact_justification.lower() or "cross-cutting" in group.impact_justification.lower()

    # 29. Secondary evidence from context.related_files is consumed without overriding AST edges
    def test_related_files_secondary_evidence_consumed(self):
        engine = WalkthroughEngine()
        files = [
            _make_file("app/services/payment_service.py", symbols=["PaymentService.charge"]),
        ]
        from app.schemas.context import RelatedFileItem, ContextReason
        related = [
            RelatedFileItem(
                path="config/payment_gateway.yaml",
                reason=ContextReason.DIRECT_DEPENDENCY,
                reason_detail="Payment gateway configuration required by app/services/payment_service.py",
            )
        ]
        context = PRContext(
            pr=_make_identity(),
            intent=_make_intent(),
            changed_files=files,
            related_files=related,
            context_budget=ContextBudget(),
        )
        res = engine.build_walkthrough(context)

        group = res.groups[0]
        # Should contain a secondary relationship for the related file
        rel_files_rels = [r for r in group.relationships if r.evidence_source == "related_file"]
        assert len(rel_files_rels) >= 1
        rel = rel_files_rels[0]
        assert rel.relationship == SemanticRelationshipType.DEPENDS_ON
        assert rel.target == "config/payment_gateway.yaml"

