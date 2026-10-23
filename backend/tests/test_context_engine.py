import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.context import (
    ChangeType,
    ContextReason,
    PRContext,
    PRIdentity,
    PRIntent,
)
from app.schemas.github import (
    GitHubCommit,
    GitHubFileDiff,
    GitHubPR,
    PRDetailResponse,
)
from app.services.context_engine import ContextEngine, get_context_engine
from app.services.review_engine import ReviewEngine


@pytest.fixture
def mock_pr_detail():
    pr = GitHubPR(
        id=101,
        number=42,
        title="feat: add payment processing controller and webhook handler",
        state="open",
        author="alice",
        head_sha="sha-head-42",
        base_sha="sha-base-42",
        base_branch="main",
        body="""Closes #15 and refs #18.
- [x] Implement PaymentController
- [ ] Add webhook verification
- [x] Write unit tests
""",
    )

    diff1 = GitHubFileDiff(
        filename="app/controllers/payment_controller.py",
        status="modified",
        additions=25,
        deletions=2,
        changes=27,
        patch="""@@ -1,5 +1,15 @@
+from app.services.payment_service import PaymentService
+from app.models.invoice import Invoice
+
 class PaymentController:
-    pass
+    def process_payment(self, invoice_id: str):
+        service = PaymentService()
+        result = service.charge(invoice_id)
+        return result
""",
    )

    diff2 = GitHubFileDiff(
        filename="tests/test_payment_controller.py",
        status="added",
        additions=15,
        deletions=0,
        changes=15,
        patch="""@@ -0,0 +1,10 @@
+def test_process_payment():
+    assert True
""",
    )

    commits = [
        GitHubCommit(
            sha="sha-c1",
            author_name="Alice",
            message="feat: payment controller (fixes #15)",
        )
    ]

    return PRDetailResponse(
        pr=pr,
        files=[diff1, diff2],
        commits=commits,
        comments=[],
    )


@pytest.mark.asyncio
async def test_pr_identity(mock_pr_detail):
    engine = ContextEngine()
    context = await engine.build_pr_context(
        repository_id="repo-123",
        repository_full_name="org/payment-service",
        pr_detail=mock_pr_detail,
    )

    assert isinstance(context, PRContext)
    assert context.pr.repository_id == "repo-123"
    assert context.pr.repository_full_name == "org/payment-service"
    assert context.pr.pr_number == 42
    assert context.pr.title == "feat: add payment processing controller and webhook handler"
    assert context.pr.author == "alice"
    assert context.pr.head_sha == "sha-head-42"
    assert context.pr.base_sha == "sha-base-42"
    assert context.pr.changed_files_count == 2
    assert context.pr.total_additions == 40
    assert context.pr.total_deletions == 2


@pytest.mark.asyncio
async def test_pr_intent_classification(mock_pr_detail):
    engine = ContextEngine()
    intent = engine.extract_pr_intent(
        pr_title=mock_pr_detail.pr.title,
        pr_body=mock_pr_detail.pr.body,
        files=mock_pr_detail.files,
    )

    assert intent.likely_change_type == ChangeType.FEATURE
    assert intent.affected_domain == "payment"
    assert "Implement PaymentController" in intent.goals
    assert "Write unit tests" in intent.goals

    # Test unknown fallback
    intent_unknown = engine.extract_pr_intent(
        pr_title="random change",
        pr_body="",
        files=[],
    )
    assert intent_unknown.likely_change_type == ChangeType.UNKNOWN


@pytest.mark.asyncio
async def test_changed_file_and_hunk_symbol_mapping(mock_pr_detail):
    engine = ContextEngine()
    context = await engine.build_pr_context(
        repository_id="repo-123",
        repository_full_name="org/payment-service",
        pr_detail=mock_pr_detail,
    )

    assert len(context.changed_files) == 2
    controller_file = next(f for f in context.changed_files if f.path == "app/controllers/payment_controller.py")
    assert "PaymentController" in controller_file.classes
    assert any("process_payment" in m for m in controller_file.methods)
    assert "app.services.payment_service" in controller_file.imports or any("payment_service" in imp for imp in controller_file.imports)

    # Check hunk symbol mapping
    assert len(context.changed_hunks) > 0
    hunk = context.changed_hunks[0]
    assert hunk.file_path == "app/controllers/payment_controller.py"
    assert hunk.enclosing_class == "PaymentController" or "PaymentController" in hunk.changed_symbols
    assert any("charge" in c or "PaymentService" in c for c in hunk.changed_calls)


@pytest.mark.asyncio
async def test_definitions_and_references(mock_pr_detail):
    engine = ContextEngine()
    context = await engine.build_pr_context(
        repository_id="repo-123",
        repository_full_name="org/payment-service",
        pr_detail=mock_pr_detail,
    )

    assert len(context.definitions) > 0
    class_def = next((d for d in context.definitions if d.symbol == "PaymentController"), None)
    assert class_def is not None
    assert class_def.definition_type == "class"
    assert class_def.file_path == "app/controllers/payment_controller.py"


@pytest.mark.asyncio
async def test_dependency_call_graph(mock_pr_detail):
    engine = ContextEngine()
    context = await engine.build_pr_context(
        repository_id="repo-123",
        repository_full_name="org/payment-service",
        pr_detail=mock_pr_detail,
    )

    graph = context.dependencies
    assert len(graph.nodes) > 0
    # Node types should include file, class, method, or module
    node_types = {n.type for n in graph.nodes}
    assert "file" in node_types or "class" in node_types

    # Should have imports or calls edges
    edge_types = {e.relationship for e in graph.edges}
    assert "imports" in edge_types or "calls" in edge_types or "depends_on" in edge_types


@pytest.mark.asyncio
async def test_related_file_and_test_discovery(mock_pr_detail):
    engine = ContextEngine()
    context = await engine.build_pr_context(
        repository_id="repo-123",
        repository_full_name="org/payment-service",
        pr_detail=mock_pr_detail,
    )

    # test_payment_controller.py was in PR diff
    assert len(context.related_tests) > 0
    test_item = context.related_tests[0]
    assert test_item.reason == ContextReason.RELATED_TEST
    assert test_item.test_file_path == "tests/test_payment_controller.py"

    # Direct dependencies discovered from imports
    assert len(context.related_files) > 0
    assert any(rf.reason == ContextReason.DIRECT_DEPENDENCY for rf in context.related_files)


@pytest.mark.asyncio
async def test_related_issues_discovery(mock_pr_detail):
    engine = ContextEngine()
    issues = engine.discover_related_issues(
        pr_title=mock_pr_detail.pr.title,
        pr_body=mock_pr_detail.pr.body,
        commits=mock_pr_detail.commits,
    )

    issue_nums = {iss.issue_number for iss in issues}
    assert 15 in issue_nums  # Closes #15
    assert 18 in issue_nums  # refs #18
    assert all(iss.reason == ContextReason.RELATED_ISSUE for iss in issues)


@pytest.mark.asyncio
async def test_context_reasons_presence(mock_pr_detail):
    engine = ContextEngine()
    context = await engine.build_pr_context(
        repository_id="repo-123",
        repository_full_name="org/payment-service",
        pr_detail=mock_pr_detail,
    )

    # Every item must have a machine-readable reason
    assert ContextReason.DIRECT_CHANGE in context.context_reasons
    assert ContextReason.RELATED_TEST in context.context_reasons
    assert ContextReason.RELATED_ISSUE in context.context_reasons


@pytest.mark.asyncio
async def test_context_budget_and_bounding(mock_pr_detail):
    engine = ContextEngine()
    # Test strict bounding with a very small budget (e.g. 50 tokens)
    context = await engine.build_pr_context(
        repository_id="repo-123",
        repository_full_name="org/payment-service",
        pr_detail=mock_pr_detail,
        max_context_tokens=50,
    )

    assert context.context_budget.truncated is True
    assert context.context_budget.total_allowed_tokens == 50
    assert isinstance(context.to_formatted_context_block(), str)


@pytest.mark.asyncio
async def test_review_engine_integrates_context_engine(mock_pr_detail):
    """Verifies ReviewEngine calls ContextEngine and populates review_output.pr_context."""
    mock_github = MagicMock()
    mock_github.get_pr_details_full = AsyncMock(return_value=mock_pr_detail)

    mock_supabase = MagicMock()
    mock_supabase.get_repository_by_id = AsyncMock(return_value={"id": "repo-123", "full_name": "org/payment-service"})
    mock_supabase.upsert_pull_request = AsyncMock(return_value={"id": "pr-uuid-1"})
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[])
    mock_supabase.create_review = AsyncMock(return_value={"id": "rev-uuid-1"})
    mock_supabase.insert_findings = AsyncMock(return_value=[])

    mock_memory = MagicMock()
    mock_memory.recall = AsyncMock(return_value=MagicMock(items=[]))

    mock_groq = MagicMock()
    mock_groq.is_configured = False
    mock_groq.generate_review_analysis.return_value = MagicMock(
        summary="Context-aware review completed.",
        drift_detected=False,
        exception_approved=False,
        confidence_score=0.9,
        findings=[],
        durable_memory_candidates=[],
    )

    review_engine = ReviewEngine(
        github=mock_github,
        memory=mock_memory,
        supabase=mock_supabase,
        groq=mock_groq,
    )

    result = await review_engine.review_pull_request("repo-123", 42)

    assert result is not None
    assert result.pr_context is not None
    assert isinstance(result.pr_context, PRContext)
    assert result.pr_context.pr.pr_number == 42
    assert result.pr_context.intent.likely_change_type == ChangeType.FEATURE


@pytest.mark.asyncio
async def test_gate_1_and_24_and_25_cache_sha_and_repo_isolation(mock_pr_detail):
    """Gates 1, 23, 24, 25: Context identity and cache isolation by (repo, pr_number, sha)."""
    engine = ContextEngine()
    engine.clear_cache()

    # Context for repo-A, PR 42, head SHA AAA
    mock_pr_detail.pr.head_sha = "sha-AAA"
    ctx_a1 = await engine.build_pr_context("repo-A", "org/repo-A", mock_pr_detail, use_cache=True)
    assert engine.get_cached_context("repo-A", 42, "sha-AAA") is ctx_a1

    # Same repo-A, same PR 42, NEW head SHA BBB -> must invalidate cache and build fresh
    mock_pr_detail.pr.head_sha = "sha-BBB"
    ctx_a2 = await engine.build_pr_context("repo-A", "org/repo-A", mock_pr_detail, use_cache=True)
    assert ctx_a2 is not ctx_a1
    assert ctx_a2.pr.head_sha == "sha-BBB"
    assert engine.get_cached_context("repo-A", 42, "sha-AAA") is not ctx_a2

    # Different repo-B with same PR 42 and SHA BBB -> completely isolated context
    ctx_b = await engine.build_pr_context("repo-B", "org/repo-B", mock_pr_detail, use_cache=True)
    assert ctx_b is not ctx_a2
    assert ctx_b.pr.repository_id == "repo-B"
    assert engine.get_cached_context("repo-A", 42, "sha-BBB").pr.repository_id == "repo-A"


@pytest.mark.asyncio
async def test_gate_7_bounded_recursive_graph_traversal(mock_pr_detail):
    """Gate 7: Ensure recursive dependency cycles do not cause infinite traversal."""
    engine = ContextEngine()
    # Cyclic calls: A -> B -> C -> A
    cyclic_diff = GitHubFileDiff(
        filename="app/cycle.py",
        status="modified",
        additions=10,
        deletions=0,
        changes=10,
        patch="""@@ -1,5 +1,10 @@
+def func_a():
+    return func_b()
+def func_b():
+    return func_c()
+def func_c():
+    return func_a()
""",
    )
    mock_pr_detail.files = [cyclic_diff]
    context = await engine.build_pr_context("repo-1", "org/repo", mock_pr_detail)

    # Graph must be finite and bounded
    assert len(context.dependencies.nodes) <= 30
    assert len(context.dependencies.edges) <= 30


@pytest.mark.asyncio
async def test_gate_15_duplicate_context_merging_and_scenario_g():
    """Gate 15 & Scenario G: PR #142 discovered through 4 routes merges into 1 item."""
    engine = ContextEngine()
    mock_supabase = MagicMock()
    # Mock reviews matching same_file and same_symbol for PR 142
    mock_supabase.get_recent_reviews = AsyncMock(return_value=[
        {
            "pr_number": 142,
            "pr_title": "PR #142 Architecture Rule",
            "summary": "payment_controller.py and PaymentController refactoring",
            "verdict": "CONFORMING",
        }
    ])
    engine.supabase = mock_supabase

    # Discover related PRs with all 4 signals for PR 142:
    # 1. same_file ("payment_controller.py")
    # 2. same_symbol ("PaymentController")
    # 3. memory_retrieval (142 in memory_pr_numbers)
    # 4. explicit_reference (142 in explicit_pr_numbers)
    related = await engine.discover_related_prs(
        repository_id="repo-1",
        repository_full_name="org/repo",
        current_pr_number=999,
        changed_files=["payment_controller.py"],
        symbols=["PaymentController"],
        explicit_pr_numbers=[142],
        memory_pr_numbers=[142],
    )

    # Must be merged into ONE item
    pr_142_items = [p for p in related if p.pr_number == 142]
    assert len(pr_142_items) == 1
    item = pr_142_items[0]
    # Check that multiple discovery sources are captured
    assert "explicit_reference" in item.discovery_sources
    assert "memory_retrieval" in item.discovery_sources
    assert "same_file" in item.discovery_sources
    assert "same_symbol" in item.discovery_sources


@pytest.mark.asyncio
async def test_gate_16_deterministic_ranking(mock_pr_detail):
    """Gate 16: Two consecutive runs produce the exact same deterministic output."""
    engine = ContextEngine()
    ctx1 = await engine.build_pr_context("repo-1", "org/repo", mock_pr_detail, use_cache=False)
    ctx2 = await engine.build_pr_context("repo-1", "org/repo", mock_pr_detail, use_cache=False)

    assert ctx1.to_formatted_context_block() == ctx2.to_formatted_context_block()
    assert [n.id for n in ctx1.dependencies.nodes] == [n.id for n in ctx2.dependencies.nodes]


@pytest.mark.asyncio
async def test_gate_20_and_21_priority_aware_truncation_observability(mock_pr_detail):
    """Gate 20, 21 & Scenario M: Strict budget drops low-priority observations, records observability."""
    engine = ContextEngine()
    # Create context with low and high priority items
    context = await engine.build_pr_context("repo-1", "org/repo", mock_pr_detail)

    # Artificially populate items across all priority levels
    from app.schemas.context import RankedMemoryItem, RelevantDocItem, RelatedIssueItem, RelatedPRItem
    context.relevant_memories = [
        RankedMemoryItem(claim="Authoritative rule", category="ARCHITECTURAL_RULE", authority="ENFORCEABLE", relevance_score=1.0),
        RankedMemoryItem(claim="Low priority observation", category="OBSERVATION", authority="CONVERSATIONAL", relevance_score=0.4),
    ]
    context.related_issues = [RelatedIssueItem(issue_number=99, title="Issue 99", relevance_explanation="Ref")]
    context.relevant_docs = [RelevantDocItem(path="docs/arch.md", title="Arch", relevance_explanation="Matches")]
    context.related_prs = [RelatedPRItem(pr_number=50, title="PR 50", relevance_explanation="Overlaps")]

    # Enforce tight budget
    engine.enforce_context_budget(context, max_tokens=150)

    # Observability checks (Gate 21)
    assert context.context_budget.truncated is True
    assert context.context_budget.limit_reached is True
    assert len(context.context_budget.truncated_sections) > 0
    assert len(context.context_budget.items_removed) > 0

    # Priority checks (Gate 20): Observation should be truncated before Authoritative rule
    rule_claims = [m.claim for m in context.relevant_memories]
    assert "Authoritative rule" in rule_claims
    assert "Low priority observation" not in rule_claims


@pytest.mark.asyncio
async def test_gate_36_to_40_no_fabrication_when_sources_empty(mock_pr_detail):
    """Gate 36-40, Scenarios H, I, J: Verify no fabricated files, tests, docs, or issues."""
    mock_github = MagicMock()
    mock_github.is_configured = False
    mock_supabase = MagicMock()
    mock_supabase.get_recent_reviews = AsyncMock(return_value=[])
    mock_memory = MagicMock()
    mock_memory.recall = AsyncMock(return_value=MagicMock(items=[]))

    engine = ContextEngine(github=mock_github, supabase=mock_supabase, memory=mock_memory)

    empty_pr = GitHubPR(
        id=200,
        number=77,
        title="refactor: clean up variable names",
        state="open",
        author="bob",
        head_sha="sha-77",
        base_branch="main",
        body="Pure cleanup with no issue references or doc changes",
    )
    diff = GitHubFileDiff(
        filename="app/utils.py",
        status="modified",
        additions=2,
        deletions=2,
        changes=4,
        patch="""@@ -1,2 +1,2 @@
-x = 1
+variable_name = 1
""",
    )
    detail = PRDetailResponse(pr=empty_pr, files=[diff], commits=[], comments=[])

    context = await engine.build_pr_context("repo-empty", "org/empty", detail)

    assert len(context.related_issues) == 0
    assert len(context.relevant_docs) == 0
    assert len(context.related_prs) == 0


@pytest.mark.asyncio
async def test_adversarial_scenarios_a_b_c_bounding():
    """Adversarial Scenarios A, B, C: 500 files, 500 historical PRs, 50 memories bounded."""
    # 500 files (Scenario A)
    files = [
        GitHubFileDiff(
            filename=f"app/file_{i}.py",
            status="modified",
            additions=1,
            deletions=0,
            changes=1,
            patch="""@@ -0,0 +1,1 @@
+x = 1
""",
        )
        for i in range(500)
    ]
    pr = GitHubPR(
        id=300,
        number=88,
        title="chore: bulk update",
        state="open",
        author="carol",
        head_sha="sha-88",
        base_branch="main",
    )
    detail = PRDetailResponse(pr=pr, files=files, commits=[], comments=[])

    # 500 reviews in Supabase (Scenario B)
    mock_supabase = MagicMock()
    mock_supabase.get_recent_reviews = AsyncMock(return_value=[
        {"pr_number": i, "summary": f"app/file_{i}.py change", "verdict": "CONFORMING"}
        for i in range(500)
    ])
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[])

    # 50 memories in Hindsight (Scenario C)
    mock_memory = MagicMock()
    mock_memory.recall = AsyncMock(return_value=MagicMock(
        items=[
            MagicMock(
                id=f"mem-{i}",
                text=f"Rule {i}: must do X",
                metadata={"category": "DOCUMENTED_DECISION", "scope": "app/*"},
                score=0.9,
            )
            for i in range(50)
        ]
    ))

    mock_github = MagicMock()
    mock_github.is_configured = False

    engine = ContextEngine(github=mock_github, supabase=mock_supabase, memory=mock_memory)
    context = await engine.build_pr_context("repo-bulk", "org/bulk", detail, max_context_tokens=4000)

    # Verify strict bounding:
    assert len(context.related_prs) <= 5
    assert len(context.relevant_memories) <= 8
    assert context.context_budget.used_tokens <= 4000
