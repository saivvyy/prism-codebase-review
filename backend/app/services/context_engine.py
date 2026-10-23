import asyncio
import inspect
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.core.exceptions import HindsightMemoryError
from app.schemas.analysis import FileAnalysisResult
from app.schemas.canonical_memory import (
    CanonicalMemory,
    KnowledgeType,
    MemoryAuthority,
    MemoryStatus,
    get_default_authority,
    normalize_knowledge_type,
)
from app.schemas.context import (
    ChangeType,
    ChangedFileDetail,
    CheckEvidence,
    CodeDependencyGraph,
    CommitEvidence,
    ContextBudget,
    ContextReason,
    ConversationEvidence,
    DependencyGraphEdge,
    DependencyGraphNode,
    HistoricalPREvidence,
    HunkSymbolMap,
    PRContext,
    PREvidenceSummary,
    PRIdentity,
    PRIntent,
    RankedMemoryItem,
    RelatedFileItem,
    RelatedIssueItem,
    RelatedPRItem,
    RelatedTestItem,
    RelevantDocItem,
    SymbolDefinitionReference,
)
from app.schemas.github import GitHubFileDiff, PRDetailResponse
from app.services.applicability import ApplicabilityEngine, get_applicability_engine
from app.services.code_analysis import CodeAnalysisService, get_code_analysis_service
from app.services.github import GitHubService, get_github_service
from app.services.memory import HindsightMemoryService, get_hindsight_service
from app.services.supabase import SupabaseService, get_supabase_service

logger = logging.getLogger(__name__)


class ContextEngine:
    """Dedicated Context Engineering layer for deep PR understanding.
    
    Transforms raw PR diffs into bounded, deterministic, symbol-aware,
    dependency-grounded, and institutionally-informed review context.
    
    Pipeline:
    PR -> PR Intent -> Changed Files -> Changed Hunks -> Tree-sitter AST ->
    Symbols -> Definitions/References -> Dependencies/Call Graph -> Related Files/Tests ->
    Related PRs/Issues/Docs -> Relevant Institutional Memory -> Context Ranking ->
    Bounded Review Context.
    """

    def __init__(
        self,
        github: Optional[GitHubService] = None,
        code_analysis: Optional[CodeAnalysisService] = None,
        memory: Optional[HindsightMemoryService] = None,
        supabase: Optional[SupabaseService] = None,
        applicability_engine: Optional[ApplicabilityEngine] = None,
    ):
        self.github = github or get_github_service()
        self.code_analysis = code_analysis or get_code_analysis_service()
        self.memory = memory or get_hindsight_service()
        self.supabase = supabase or get_supabase_service()
        self.applicability_engine = applicability_engine or get_applicability_engine()
        # In-memory context cache strictly keyed by (repository_id, pr_number, head_sha)
        self._context_cache: Dict[Tuple[str, int, str], PRContext] = {}

    def get_cached_context(self, repository_id: str, pr_number: int, head_sha: str) -> Optional[PRContext]:
        """Retrieves cached context strictly matching (repository_id, pr_number, head_sha)."""
        return self._context_cache.get((repository_id, pr_number, head_sha))

    def clear_cache(self) -> None:
        """Clears the in-memory context cache."""
        self._context_cache.clear()

    async def build_pr_context(
        self,
        repository_id: str,
        repository_full_name: str,
        pr_detail: PRDetailResponse,
        max_context_tokens: int = 6000,
        use_cache: bool = True,
        recalled_memories: Optional[List[Any]] = None,
        candidate_memories: Optional[List[Any]] = None,
    ) -> PRContext:
        """Assembles a complete, structured, and bounded PRContext with SHA-scoped caching."""
        pr = pr_detail.pr
        cache_key = (repository_id, pr.number, pr.head_sha)
        if use_cache:
            cached_ctx = self._context_cache.get(cache_key) or self._context_cache.get((repository_full_name, pr.number, pr.head_sha))
            if cached_ctx:
                return cached_ctx

        files = pr_detail.files or []

        # 1. PR Identity
        total_additions = sum(f.additions for f in files)
        total_deletions = sum(f.deletions for f in files)
        identity = PRIdentity(
            repository_id=repository_id,
            repository_full_name=repository_full_name,
            pr_number=pr.number,
            title=pr.title,
            description=pr.body,
            author=pr.author,
            base_sha=pr.base_sha,
            head_sha=pr.head_sha,
            changed_files_count=len(files),
            total_additions=total_additions,
            total_deletions=total_deletions,
        )

        # 2. PR Intent Extraction (deterministic heuristics)
        intent = self.extract_pr_intent(pr.title, pr.body, files)

        # 3 & 4. Changed File Analysis & Changed Hunk -> Symbol Mapping
        changed_files_detail, all_hunks = self.analyze_changed_files_and_hunks(files)

        # Collect all affected symbols, imports, and calls
        all_symbols: Set[str] = set()
        all_imports: Set[str] = set()
        all_calls: Set[str] = set()
        for f in changed_files_detail:
            all_symbols.update(f.affected_symbols)
            all_imports.update(f.imports)
            all_calls.update(f.calls)

        # 5. Definitions and References
        definitions, references = self.extract_definitions_and_references(
            changed_files_detail, all_hunks
        )

        # 6. Dependency / Call Graph
        dependency_graph = self.build_dependency_graph(
            changed_files_detail, all_hunks, definitions
        )

        # 10 & 11. Related PRs & Issues Discovery setup
        explicit_pr_numbers: List[int] = []
        if pr.body:
            for m in re.finditer(r"PR\s*#(\d+)", pr.body, re.IGNORECASE):
                explicit_pr_numbers.append(int(m.group(1)))
        if pr.title:
            for m in re.finditer(r"PR\s*#(\d+)", pr.title, re.IGNORECASE):
                explicit_pr_numbers.append(int(m.group(1)))

        related_issues = self.discover_related_issues(
            pr_title=pr.title,
            pr_body=pr.body,
            commits=pr_detail.commits,
        )

        # Phase 10 — Deep PR Evidence & History Retrieval setup
        commits_evidence: List[CommitEvidence] = []
        conversation_evidence: List[ConversationEvidence] = []
        checks_evidence: List[CheckEvidence] = []
        historical_prs_evidence: List[HistoricalPREvidence] = []
        commits_truncated = False
        conversation_truncated = False
        checks_truncated = False
        historical_prs_truncated = False

        owner = ""
        repo_name = ""
        file_paths = [f.path for f in changed_files_detail]
        if self.github and "/" in repository_full_name:
            owner, repo_name = repository_full_name.split("/", 1)

        async def _safe_commits():
            if owner and repo_name and hasattr(self.github, "get_pr_commits_evidence"):
                try:
                    return await self.github.get_pr_commits_evidence(owner, repo_name, pr.number)
                except Exception as e:
                    logger.warning(f"Error fetching commits evidence for {repository_full_name} PR #{pr.number}: {e}")
            return [], False

        async def _safe_conversation():
            if owner and repo_name and hasattr(self.github, "get_pr_conversation_evidence"):
                try:
                    return await self.github.get_pr_conversation_evidence(owner, repo_name, pr.number)
                except Exception as e:
                    logger.warning(f"Error fetching conversation evidence for {repository_full_name} PR #{pr.number}: {e}")
            return [], False

        async def _safe_checks():
            if owner and repo_name and hasattr(self.github, "get_pr_checks_evidence"):
                try:
                    return await self.github.get_pr_checks_evidence(owner, repo_name, pr.head_sha)
                except Exception as e:
                    logger.warning(f"Error fetching checks evidence for {repository_full_name} PR #{pr.number}: {e}")
            return [], False

        async def _safe_historical():
            if owner and repo_name and hasattr(self.github, "get_historical_prs_evidence"):
                try:
                    return await self.github.get_historical_prs_evidence(
                        owner, repo_name, pr.number, file_paths, list(all_symbols)
                    )
                except Exception as e:
                    logger.warning(f"Error fetching historical PRs evidence for {repository_full_name} PR #{pr.number}: {e}")
            return [], False

        # Phase 5 & Phase 10: Run all independent discovery operations concurrently
        (
            (related_files, related_tests),
            relevant_docs,
            related_prs,
            ranked_memories,
            (commits_evidence, commits_truncated),
            (conversation_evidence, conversation_truncated),
            (checks_evidence, checks_truncated),
            (historical_prs_evidence, historical_prs_truncated),
        ) = await asyncio.gather(
            self.discover_related_files_and_tests(
                repository_full_name=repository_full_name,
                changed_files=changed_files_detail,
                pr_files=files,
                ref=pr.head_sha,
            ),
            self.discover_relevant_docs(
                repository_full_name=repository_full_name,
                intent=intent,
                changed_files=changed_files_detail,
                ref=pr.head_sha,
            ),
            self.discover_related_prs(
                repository_id=repository_id,
                repository_full_name=repository_full_name,
                current_pr_number=pr.number,
                changed_files=[f.path for f in changed_files_detail],
                title_keywords=intent.summary,
                symbols=list(all_symbols),
                explicit_pr_numbers=explicit_pr_numbers,
            ),
            self.retrieve_and_rank_memories(
                repository_id=repository_id,
                repository_full_name=repository_full_name,
                pr_title=pr.title,
                changed_files=[f.path for f in changed_files_detail],
                symbols=list(all_symbols),
                domain=intent.affected_domain,
                review_time=pr.created_at,
                recalled_memories=recalled_memories,
                candidate_memories=candidate_memories,
            ),
            _safe_commits(),
            _safe_conversation(),
            _safe_checks(),
            _safe_historical(),
        )

        # Context Reasons tracking
        reasons: Set[ContextReason] = {ContextReason.DIRECT_CHANGE}
        if dependency_graph.edges:
            reasons.add(ContextReason.DIRECT_DEPENDENCY)
        if related_tests:
            reasons.add(ContextReason.RELATED_TEST)
        if relevant_docs:
            reasons.add(ContextReason.RELATED_DOCUMENT)
        if related_prs:
            reasons.add(ContextReason.RELATED_PR)
        if related_issues:
            reasons.add(ContextReason.RELATED_ISSUE)
        for m in ranked_memories:
            reasons.add(m.reason)

        # 15. Context Budget & Bounding
        budget = ContextBudget(
            total_allowed_tokens=max_context_tokens,
            allocations={
                "diff_and_ast": int(max_context_tokens * 0.45),
                "institutional_memory": int(max_context_tokens * 0.30),
                "dependencies": int(max_context_tokens * 0.12),
                "related_context": int(max_context_tokens * 0.13),
            },
        )

        # Compute evidence summary
        checks_success = sum(1 for c in checks_evidence if c.conclusion in ("success", "neutral"))
        checks_failed = sum(1 for c in checks_evidence if c.conclusion in ("failure", "timed_out", "action_required"))
        checks_pending = sum(1 for c in checks_evidence if c.status in ("in_progress", "queued", "pending") or c.conclusion is None)

        evidence_summary = PREvidenceSummary(
            total_commits=len(commits_evidence),
            total_conversation_items=len(conversation_evidence),
            total_checks=len(checks_evidence),
            checks_success_count=checks_success,
            checks_failed_count=checks_failed,
            checks_pending_count=checks_pending,
            total_historical_prs=len(historical_prs_evidence),
            commits_truncated=commits_truncated,
            conversation_truncated=conversation_truncated,
            checks_truncated=checks_truncated,
            historical_prs_truncated=historical_prs_truncated,
        )

        context = PRContext(
            pr=identity,
            intent=intent,
            changed_files=changed_files_detail,
            changed_hunks=all_hunks,
            symbols=sorted(list(all_symbols)),
            definitions=definitions,
            references=references,
            imports=sorted(list(all_imports)),
            calls=sorted(list(all_calls)),
            dependencies=dependency_graph,
            related_files=related_files,
            related_tests=related_tests,
            related_prs=related_prs,
            related_issues=related_issues,
            relevant_docs=relevant_docs,
            relevant_memories=ranked_memories,
            context_reasons=sorted(list(reasons), key=lambda r: r.value),
            context_budget=budget,
            commits=commits_evidence,
            conversation=conversation_evidence,
            checks=checks_evidence,
            historical_prs=historical_prs_evidence,
            evidence_summary=evidence_summary,
        )

        # Enforce bounding
        self.enforce_context_budget(context, max_context_tokens)

        self._context_cache[cache_key] = context
        if repository_full_name and repository_full_name != repository_id:
            self._context_cache[(repository_full_name, pr.number, pr.head_sha)] = context

        return context

    def extract_pr_intent(
        self,
        pr_title: str,
        pr_body: Optional[str],
        files: List[GitHubFileDiff],
    ) -> PRIntent:
        """Extracts deterministic structured intent from PR metadata without hallucination."""
        title_lower = (pr_title or "").lower().strip()
        body_text = (pr_body or "").strip()
        body_lower = body_text.lower()

        # Classify change type
        change_type = ChangeType.UNKNOWN
        if any(title_lower.startswith(p) for p in ("feat:", "feature:", "feat/", "add ", "implement ")):
            change_type = ChangeType.FEATURE
        elif any(title_lower.startswith(p) for p in ("fix:", "bugfix:", "bug:", "hotfix:", "fix ", "resolve ")):
            change_type = ChangeType.BUG_FIX
        elif any(title_lower.startswith(p) for p in ("refactor:", "cleanup:", "clean:", "reorg:")):
            change_type = ChangeType.REFACTOR
        elif any(title_lower.startswith(p) for p in ("migrat", "db:migrate", "schema:")):
            change_type = ChangeType.MIGRATION
        elif any(title_lower.startswith(p) for p in ("perf:", "performance:", "optimize:", "opt:")):
            change_type = ChangeType.PERFORMANCE
        elif any(title_lower.startswith(p) for p in ("sec:", "security:", "auth:", "audit:")):
            change_type = ChangeType.SECURITY
        elif any(title_lower.startswith(p) for p in ("test:", "tests:", "testing:", "coverage:")):
            change_type = ChangeType.TESTING
        elif any(title_lower.startswith(p) for p in ("docs:", "doc:", "readme:")):
            change_type = ChangeType.DOCUMENTATION
        elif any(title_lower.startswith(p) for p in ("chore(deps):", "dep:", "deps:", "bump ")):
            change_type = ChangeType.DEPENDENCY_UPDATE
        elif any(title_lower.startswith(p) for p in ("ci:", "config:", "build:", "infra:")):
            change_type = ChangeType.CONFIGURATION
        else:
            # Fallback heuristics based on keywords in body or file patterns
            valid_files = [f for f in files if f.filename]
            if valid_files and all(f.filename.startswith("docs/") or f.filename.endswith(".md") for f in valid_files):
                change_type = ChangeType.DOCUMENTATION
            elif valid_files and all("test" in f.filename.lower() for f in valid_files):
                change_type = ChangeType.TESTING
            elif "fix" in title_lower or "bug" in title_lower:
                change_type = ChangeType.BUG_FIX
            elif "refactor" in title_lower:
                change_type = ChangeType.REFACTOR
            elif "feature" in title_lower:
                change_type = ChangeType.FEATURE

        # Extract goals from body markdown lists
        goals: List[str] = []
        for line in body_text.splitlines():
            line_s = line.strip()
            if line_s.startswith(("- [ ]", "- [x]", "* [ ]", "* [x]")):
                item = line_s[5:].strip()
                if item:
                    goals.append(item)
            elif (line_s.startswith("- ") or line_s.startswith("* ")) and len(line_s) > 4:
                item = line_s[2:].strip()
                # filter out sub-bullet headers or non-goal lines
                if len(item) > 3 and not item.endswith(":") and len(goals) < 6:
                    goals.append(item)

        # Detect affected domain
        affected_domain = None
        domain_keywords = {
            "auth": ["auth", "login", "jwt", "token", "permission", "security"],
            "payment": ["payment", "billing", "invoice", "stripe", "checkout"],
            "persistence": ["database", "db", "repository", "sql", "migration", "query", "model"],
            "controller": ["controller", "api", "endpoint", "route", "handler"],
            "analytics": ["analytics", "metrics", "telemetry", "tracking"],
            "health": ["health", "liveness", "readiness", "diagnostic"],
        }

        combined_text = f"{title_lower} {body_lower} {' '.join(f.filename for f in files)}"
        for domain, kw_list in domain_keywords.items():
            if any(k in combined_text for k in kw_list):
                affected_domain = domain
                break

        summary = pr_title if pr_title else "Unspecified PR changes"

        return PRIntent(
            summary=summary,
            goals=goals[:6],
            behavioral_change=body_text[:300] if body_text else None,
            affected_domain=affected_domain,
            likely_change_type=change_type,
        )

    def analyze_changed_files_and_hunks(
        self,
        files: List[GitHubFileDiff],
    ) -> Tuple[List[ChangedFileDetail], List[HunkSymbolMap]]:
        """Analyzes changed files with Tree-sitter and maps hunks directly to structural entities."""
        file_details: List[ChangedFileDetail] = []
        all_hunks: List[HunkSymbolMap] = []

        # Cap tree-sitter analysis to bounded limit to prevent linear explosion on bulk changes (Gate 41/44)
        files_to_analyze = files[:30]
        for f in files_to_analyze:
            file_path = f.filename
            patch = f.patch or ""

            # Run Tree-sitter diff analysis
            analysis: FileAnalysisResult = self.code_analysis.analyze_diff(file_path, patch)

            classes_list = [c.name for c in analysis.classes]
            if not classes_list:
                for cls_name in re.findall(r"^[ +]class\s+([A-Za-z0-9_]+)", patch, re.MULTILINE):
                    if cls_name not in classes_list:
                        classes_list.append(cls_name)

            functions_list = [fn.name for fn in analysis.functions if not fn.class_name]
            methods_list = [f"{fn.class_name}.{fn.name}" for fn in analysis.functions if fn.class_name]
            if not methods_list and classes_list and functions_list:
                cls_primary = classes_list[0]
                methods_list = [f"{cls_primary}.{fn}" for fn in functions_list]

            imports_list = [imp.source for imp in analysis.imports]
            calls_list = [call.target for call in analysis.calls]

            # Build HunkSymbolMap for each changed region in the patch
            hunks_for_file: List[HunkSymbolMap] = []
            for region in analysis.changed_regions:
                if not region.enclosing_class and classes_list:
                    region.enclosing_class = classes_list[0]

                # Find symbols matching lines of this region
                region_syms: List[str] = []
                if region.enclosing_class and region.enclosing_function:
                    region_syms.append(f"{region.enclosing_class}.{region.enclosing_function}")
                elif region.enclosing_class:
                    region_syms.append(region.enclosing_class)
                elif region.enclosing_function:
                    region_syms.append(region.enclosing_function)

                # Calls made inside this region
                region_calls = [
                    c.target for c in analysis.calls
                    if region.start_line <= c.line_number <= region.end_line
                ]

                # Imports within this region
                region_imports = [
                    i.source for i in analysis.imports
                    if region.start_line <= i.line_number <= region.end_line
                ]

                snippet = "\n".join(region.added_lines[:10])

                hunk_map = HunkSymbolMap(
                    file_path=file_path,
                    start_line=region.start_line,
                    end_line=region.end_line,
                    enclosing_class=region.enclosing_class,
                    enclosing_function=region.enclosing_function,
                    changed_symbols=region_syms,
                    changed_calls=region_calls,
                    changed_imports=region_imports,
                    lines_snippet=snippet,
                )
                hunks_for_file.append(hunk_map)
                all_hunks.append(hunk_map)

            # Collect affected symbols
            affected: Set[str] = set()
            affected.update(classes_list)
            affected.update(functions_list)
            affected.update(methods_list)
            for h in hunks_for_file:
                affected.update(h.changed_symbols)

            detail = ChangedFileDetail(
                path=file_path,
                status=f.status,
                additions=f.additions,
                deletions=f.deletions,
                language=analysis.language,
                changed_hunks=hunks_for_file,
                affected_symbols=sorted(list(affected)),
                classes=classes_list,
                functions=functions_list,
                methods=methods_list,
                imports=imports_list,
                calls=calls_list,
            )
            file_details.append(detail)

        return file_details, all_hunks

    def extract_definitions_and_references(
        self,
        files: List[ChangedFileDetail],
        hunks: List[HunkSymbolMap],
    ) -> Tuple[List[SymbolDefinitionReference], List[SymbolDefinitionReference]]:
        """Discovers definitions and caller/callee references at controlled depth 1-2."""
        definitions: List[SymbolDefinitionReference] = []
        references: List[SymbolDefinitionReference] = []

        # Map of symbol -> list of callees and callers
        symbol_callees: Dict[str, List[str]] = {}
        symbol_callers: Dict[str, List[str]] = {}

        for f in files:
            # Classes defined
            for cls in f.classes:
                callees = [c for c in f.calls if c not in f.classes]
                definitions.append(
                    SymbolDefinitionReference(
                        symbol=cls,
                        file_path=f.path,
                        definition_type="class",
                        callees=callees[:8],
                        imported_symbols=f.imports[:8],
                    )
                )

            # Functions / Methods defined
            for fn in f.functions + f.methods:
                callees = [c for c in f.calls if c != fn]
                definitions.append(
                    SymbolDefinitionReference(
                        symbol=fn,
                        file_path=f.path,
                        definition_type="method" if "." in fn else "function",
                        callees=callees[:8],
                        imported_symbols=f.imports[:8],
                    )
                )

        # Cross-reference caller references across hunks
        for h in hunks:
            for call in h.changed_calls:
                caller = (
                    f"{h.enclosing_class}.{h.enclosing_function}"
                    if h.enclosing_class and h.enclosing_function
                    else (h.enclosing_function or h.enclosing_class or h.file_path)
                )
                references.append(
                    SymbolDefinitionReference(
                        symbol=call,
                        file_path=h.file_path,
                        definition_type="function",
                        callers=[caller],
                    )
                )

        return definitions[:20], references[:20]

    def build_dependency_graph(
        self,
        files: List[ChangedFileDetail],
        hunks: List[HunkSymbolMap],
        definitions: List[SymbolDefinitionReference],
    ) -> CodeDependencyGraph:
        """Builds a lightweight, deterministic dependency and call graph based on Tree-sitter evidence."""
        nodes: Dict[str, DependencyGraphNode] = {}
        edges: List[DependencyGraphEdge] = []
        seen_edges: Set[Tuple[str, str, str]] = set()

        # Add file nodes
        for f in files:
            file_id = f"file:{f.path}"
            nodes[file_id] = DependencyGraphNode(
                id=file_id,
                label=f.path,
                type="file",
                file_path=f.path,
            )

            # Add class/function nodes
            for cls in f.classes:
                cls_id = f"class:{cls}"
                nodes[cls_id] = DependencyGraphNode(
                    id=cls_id,
                    label=cls,
                    type="class",
                    file_path=f.path,
                )
                edges.append(DependencyGraphEdge(source=file_id, target=cls_id, relationship="depends_on"))

            # Add import edges
            for imp in f.imports:
                imp_id = f"module:{imp}"
                if imp_id not in nodes:
                    nodes[imp_id] = DependencyGraphNode(
                        id=imp_id,
                        label=imp,
                        type="module",
                    )
                edge_key = (file_id, imp_id, "imports")
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append(DependencyGraphEdge(source=file_id, target=imp_id, relationship="imports"))

        # Add call edges from changed hunks
        for h in hunks:
            source_label = (
                f"{h.enclosing_class}.{h.enclosing_function}"
                if h.enclosing_class and h.enclosing_function
                else (h.enclosing_function or h.enclosing_class or h.file_path)
            )
            source_id = f"symbol:{source_label}"
            if source_id not in nodes:
                nodes[source_id] = DependencyGraphNode(
                    id=source_id,
                    label=source_label,
                    type="method" if "." in source_label else "function",
                    file_path=h.file_path,
                )

            for call in h.changed_calls:
                target_id = f"symbol:{call}"
                if target_id not in nodes:
                    nodes[target_id] = DependencyGraphNode(
                        id=target_id,
                        label=call,
                        type="function",
                    )
                edge_key = (source_id, target_id, "calls")
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append(DependencyGraphEdge(source=source_id, target=target_id, relationship="calls"))

        return CodeDependencyGraph(
            nodes=list(nodes.values())[:30],
            edges=edges[:30],
        )

    async def discover_related_files_and_tests(
        self,
        repository_full_name: str,
        changed_files: List[ChangedFileDetail],
        pr_files: List[GitHubFileDiff],
        ref: Optional[str] = None,
    ) -> Tuple[List[RelatedFileItem], List[RelatedTestItem]]:
        """Discovers verified related files and tests matching changed symbols."""
        related_files: List[RelatedFileItem] = []
        related_tests: List[RelatedTestItem] = []

        pr_filenames = {f.filename for f in pr_files if f.filename}
        owner, repo_name = (
            repository_full_name.split("/", 1)
            if "/" in repository_full_name
            else ("unknown", repository_full_name)
        )

        for f in changed_files:
            path = f.path
            base_name = os.path.basename(path)
            root_name, ext = os.path.splitext(base_name)

            # Identify if the file itself is a test
            if "test" in path.lower():
                related_tests.append(
                    RelatedTestItem(
                        test_file_path=path,
                        target_symbol_or_file=path,
                        reason=ContextReason.RELATED_TEST,
                        exists_in_repo=True,
                        is_in_pr_diff=True,
                    )
                )
                continue

            # Candidate test locations
            candidate_test_paths = [
                f"tests/test_{base_name}",
                f"tests/{root_name}_test{ext}",
                f"test_{base_name}",
                f"{root_name}.test{ext}",
                f"{root_name}.spec{ext}",
                f"tests/unit/test_{base_name}",
                f"test/{base_name}",
            ]

            found_test = False
            for test_candidate in candidate_test_paths:
                if test_candidate in pr_filenames:
                    related_tests.append(
                        RelatedTestItem(
                            test_file_path=test_candidate,
                            target_symbol_or_file=path,
                            reason=ContextReason.RELATED_TEST,
                            exists_in_repo=True,
                            is_in_pr_diff=True,
                        )
                    )
                    found_test = True
                    break

            # If not in PR diff, check remote repo if GitHub service configured (bounded to first 3 files to avoid latency)
            CODE_EXTENSIONS = {".py", ".ts", ".js", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb", ".cpp", ".c", ".cs"}
            is_code_file = ext.lower() in CODE_EXTENSIONS and base_name.lower() not in ("package.json", "package-lock.json", "requirements.txt", "yarn.lock", "pnpm-lock.yaml")
            if not found_test and is_code_file and self.github and self.github.is_configured and len(related_tests) < 3:
                async def _check_remote_test(test_cand: str):
                    try:
                        content = await self.github.get_file_contents(
                            owner=owner,
                            repo=repo_name,
                            path=test_cand,
                            ref=ref,
                        )
                        if content:
                            return test_cand, content
                    except Exception as e:
                        logger.debug("Failed checking test candidate %s: %s", test_cand, e)
                    return None

                results = await asyncio.gather(*[_check_remote_test(tc) for tc in candidate_test_paths[:2]], return_exceptions=True)
                for res in results:
                    if res and isinstance(res, tuple):
                        test_cand, content = res
                        related_tests.append(
                            RelatedTestItem(
                                test_file_path=test_cand,
                                target_symbol_or_file=path,
                                reason=ContextReason.RELATED_TEST,
                                exists_in_repo=True,
                                is_in_pr_diff=False,
                                snippet=content[:200],
                            )
                        )
                        break

            # Discover internal imported files as DIRECT_DEPENDENCY
            for imp in f.imports:
                clean_imp = imp.replace("from ", "").replace("import ", "").split()[0].replace(".", "/")
                candidate_source = f"{clean_imp}{ext}"
                if candidate_source != path and not candidate_source.startswith("tests/"):
                    related_files.append(
                        RelatedFileItem(
                            path=candidate_source,
                            reason=ContextReason.DIRECT_DEPENDENCY,
                            reason_detail=f"Imported by changed file {path}",
                            relationship="imports",
                        )
                    )

        # Deduplicate
        seen_files: Set[str] = set()
        deduped_files: List[RelatedFileItem] = []
        for rf in related_files:
            if rf.path not in seen_files and rf.path not in pr_filenames:
                seen_files.add(rf.path)
                deduped_files.append(rf)

        seen_tests: Set[str] = set()
        deduped_tests: List[RelatedTestItem] = []
        for rt in related_tests:
            if rt.test_file_path not in seen_tests:
                seen_tests.add(rt.test_file_path)
                deduped_tests.append(rt)

        return deduped_files[:8], deduped_tests[:6]

    async def discover_relevant_docs(
        self,
        repository_full_name: str,
        intent: PRIntent,
        changed_files: List[ChangedFileDetail],
        ref: Optional[str] = None,
    ) -> List[RelevantDocItem]:
        """Discovers relevant repository docs or ADRs matching the change domain."""
        docs: List[RelevantDocItem] = []
        if not self.github or not self.github.is_configured:
            return docs

        owner, repo_name = (
            repository_full_name.split("/", 1)
            if "/" in repository_full_name
            else ("unknown", repository_full_name)
        )

        domain = (intent.affected_domain or "").lower()
        candidate_doc_paths = [
            "README.md",
            "docs/architecture.md",
            "ARCHITECTURE.md",
        ]

        if domain:
            candidate_doc_paths.insert(0, f"docs/{domain}.md")

        async def _check_doc(doc_path: str) -> Optional[RelevantDocItem]:
            try:
                content = await self.github.get_file_contents(
                    owner=owner,
                    repo=repo_name,
                    path=doc_path,
                    ref=ref,
                )
                if content:
                    title = os.path.basename(doc_path).replace(".md", "").replace("_", " ").title()
                    return RelevantDocItem(
                        path=doc_path,
                        title=title,
                        reason=ContextReason.RELATED_DOCUMENT,
                        relevance_explanation=f"Matches affected domain '{domain or 'general'}'",
                        excerpt=content[:300].strip(),
                    )
            except Exception as e:
                logger.debug("Failed discovering doc %s: %s", doc_path, e)
            return None

        doc_results = await asyncio.gather(*[_check_doc(p) for p in candidate_doc_paths[:4]], return_exceptions=True)
        for res in doc_results:
            if isinstance(res, RelevantDocItem):
                docs.append(res)
                if len(docs) >= 2:
                    break

        return docs

    async def discover_related_prs(
        self,
        repository_id: str,
        repository_full_name: str,
        current_pr_number: int,
        changed_files: List[str],
        title_keywords: str = "",
        symbols: Optional[List[str]] = None,
        explicit_pr_numbers: Optional[List[int]] = None,
        memory_pr_numbers: Optional[List[int]] = None,
    ) -> List[RelatedPRItem]:
        """Finds historical pull requests touching the same files, symbols, memory precedents, or explicit references.
        Guarantees deduplication: a PR discovered across multiple routes merges into ONE logical item.
        """
        merged_map: Dict[int, RelatedPRItem] = {}

        def _merge_pr(
            pr_num: int,
            title: str,
            source: str,
            explanation: str,
            overlapping_files: Optional[List[str]] = None,
            verdict: Optional[str] = None,
        ):
            if pr_num == current_pr_number:
                return
            if pr_num in merged_map:
                existing = merged_map[pr_num]
                if source not in existing.discovery_sources:
                    existing.discovery_sources.append(source)
                if overlapping_files:
                    for f in overlapping_files:
                        if f not in existing.overlapping_files:
                            existing.overlapping_files.append(f)
                if verdict and not existing.verdict:
                    existing.verdict = verdict
                existing.relevance_explanation = f"Matched via {', '.join(existing.discovery_sources)}"
            else:
                merged_map[pr_num] = RelatedPRItem(
                    pr_number=pr_num,
                    title=title,
                    reason=ContextReason.RELATED_PR,
                    relevance_explanation=explanation,
                    overlapping_files=overlapping_files or [],
                    verdict=verdict,
                    discovery_sources=[source],
                )

        # 1. Explicit PR references from PR description/title
        if explicit_pr_numbers:
            for num in explicit_pr_numbers:
                _merge_pr(
                    pr_num=num,
                    title=f"PR #{num}",
                    source="explicit_reference",
                    explanation=f"Explicitly referenced in PR description/title",
                )

        # 2. Memory PR numbers
        if memory_pr_numbers:
            for num in memory_pr_numbers:
                _merge_pr(
                    pr_num=num,
                    title=f"PR #{num}",
                    source="memory_retrieval",
                    explanation="Associated with recalled architectural memory",
                )

        # 3. Reviews from Supabase matching files and symbols
        if hasattr(self.supabase, "get_recent_reviews"):
            try:
                recent_reviews = await self.supabase.get_recent_reviews(
                    repository_id=repository_id,
                    limit=20,
                )
                file_set = set(changed_files)
                symbol_set = set(symbols or [])
                for rev in recent_reviews:
                    raw_num = rev.get("pr_number") or rev.get("github_pr_number")
                    if not raw_num:
                        continue
                    try:
                        pr_num = int(raw_num)
                    except (ValueError, TypeError):
                        continue
                    if pr_num == current_pr_number:
                        continue

                    rev_summary = str(rev.get("summary") or "")
                    overlap_files = [f for f in file_set if f in rev_summary]
                    overlap_syms = [s for s in symbol_set if s in rev_summary]
                    verdict = rev.get("verdict")
                    title = rev.get("pr_title") or f"PR #{pr_num}"

                    if overlap_files:
                        _merge_pr(
                            pr_num=pr_num,
                            title=title,
                            source="same_file",
                            explanation="Touched same files in past review",
                            overlapping_files=overlap_files,
                            verdict=verdict,
                        )
                    if overlap_syms:
                        _merge_pr(
                            pr_num=pr_num,
                            title=title,
                            source="same_symbol",
                            explanation="Touched same architectural symbols in past review",
                            verdict=verdict,
                        )
                    if not overlap_files and not overlap_syms and verdict in ("DRIFT", "APPROVED_EXCEPTION"):
                        _merge_pr(
                            pr_num=pr_num,
                            title=title,
                            source="historical_precedent",
                            explanation=f"Historical precedent (Verdict: {verdict})",
                            verdict=verdict,
                        )
            except Exception as e:
                logger.debug("Failed discovering related PRs: %s", e)

        return list(merged_map.values())[:5]

    def discover_related_issues(
        self,
        pr_title: str,
        pr_body: Optional[str],
        commits: Optional[List[Any]],
    ) -> List[RelatedIssueItem]:
        """Extracts linked issues explicitly referenced in PR title, body, or commits."""
        issues: List[RelatedIssueItem] = []
        seen_numbers: Set[int] = set()

        issue_regex = re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|ref[s]?|issue)\s*#(\d+)", re.IGNORECASE)

        # Check body
        if pr_body:
            for match in issue_regex.finditer(pr_body):
                num = int(match.group(1))
                if num not in seen_numbers:
                    seen_numbers.add(num)
                    issues.append(
                        RelatedIssueItem(
                            issue_number=num,
                            title=f"Issue #{num}",
                            reason=ContextReason.RELATED_ISSUE,
                            relevance_explanation=f"Referenced via '{match.group(0)}'",
                            referenced_in="pr_body",
                        )
                    )

        # Check title
        if pr_title:
            for match in issue_regex.finditer(pr_title):
                num = int(match.group(1))
                if num not in seen_numbers:
                    seen_numbers.add(num)
                    issues.append(
                        RelatedIssueItem(
                            issue_number=num,
                            title=f"Issue #{num}",
                            reason=ContextReason.RELATED_ISSUE,
                            relevance_explanation=f"Referenced in PR title",
                            referenced_in="pr_title",
                        )
                    )

        # Check commit messages
        if commits:
            for c in commits:
                msg = getattr(c, "message", "") or (c.get("message") if isinstance(c, dict) else "")
                if msg:
                    for match in issue_regex.finditer(msg):
                        num = int(match.group(1))
                        if num not in seen_numbers:
                            seen_numbers.add(num)
                            issues.append(
                                RelatedIssueItem(
                                    issue_number=num,
                                    title=f"Issue #{num}",
                                    reason=ContextReason.RELATED_ISSUE,
                                    relevance_explanation="Referenced in commit message",
                                    referenced_in="commit_message",
                                )
                            )

        return issues[:3]

    async def retrieve_and_rank_memories(
        self,
        repository_id: str,
        repository_full_name: str,
        pr_title: str,
        changed_files: List[str],
        symbols: List[str],
        domain: Optional[str],
        review_time: Optional[Any] = None,
        recalled_memories: Optional[List[Any]] = None,
        candidate_memories: Optional[List[Any]] = None,
    ) -> List[RankedMemoryItem]:
        """Constructs multi-signal memory queries, enforces Phase 3 applicability, and deterministically ranks relevant memories."""
        ranked_items: List[RankedMemoryItem] = []
        bank_id = repository_full_name.replace("/", "_").lower()

        if recalled_memories is None:
            # Multi-signal query construction
            query_terms: List[str] = [pr_title]
            if domain:
                query_terms.append(domain)
            if symbols:
                query_terms.extend(symbols[:3])

            hindsight_query = " | ".join(query_terms)

            recalled_memories = []
            try:
                recall_resp = self.memory.recall(
                    query=hindsight_query,
                    bank_id=bank_id,
                    max_tokens=2048,
                )
                if inspect.iscoroutine(recall_resp):
                    recall_resp = await recall_resp
                recalled_memories = getattr(recall_resp, "items", []) or []
            except Exception as e:
                logger.debug("Hindsight recall encountered issue during context engineering: %s", e)

        # Build candidate memories (reusing pre-fetched memories if available)
        candidates: List[CanonicalMemory] = []
        if candidate_memories is not None:
            for rm in candidate_memories:
                try:
                    candidates.append(rm if isinstance(rm, CanonicalMemory) else CanonicalMemory(**rm))
                except Exception:
                    pass
        elif hasattr(self.supabase, "list_engineering_memories"):
            repo_memories_raw = []
            try:
                repo_memories_raw = await self.supabase.list_engineering_memories(
                    repository_id=repository_id,
                    status="all",
                )
            except Exception as e:
                logger.debug("Failed fetching repository memories from Supabase: %s", e)
            for rm in repo_memories_raw:
                try:
                    candidates.append(rm if isinstance(rm, CanonicalMemory) else CanonicalMemory(**rm))
                except Exception:
                    pass

        # Convert recalled Hindsight items
        for mem in recalled_memories:
            raw_meta = getattr(mem, "metadata", {}) or {}
            mem_text = getattr(mem, "text", "")
            mem_type_str = raw_meta.get("category") or raw_meta.get("type") or "DOCUMENTED_DECISION"
            k_type = normalize_knowledge_type(mem_type_str)
            if "exception" in mem_text.lower():
                k_type = KnowledgeType.APPROVED_EXCEPTION
            elif "rejected pattern" in mem_text.lower() or "architecture drift" in mem_text.lower():
                k_type = KnowledgeType.REJECTED_PATTERN

            auth = get_default_authority(k_type)
            claim = raw_meta.get("claim") or mem_text
            pr_num_match = re.search(r"PR\s*#(\d+)", mem_text, re.IGNORECASE)
            pr_num = int(pr_num_match.group(1)) if pr_num_match else None

            try:
                cm = CanonicalMemory(
                    memory_id=getattr(mem, "id", None) or f"hindsight-{pr_num or len(candidates)}",
                    repository_id=raw_meta.get("repository_id") or repository_id,
                    repository_full_name=repository_full_name,
                    type=k_type,
                    authority=auth,
                    statement=claim,
                    rationale=raw_meta.get("rationale") or "Hindsight memory.",
                    scope=raw_meta.get("scope") or "Repository architecture",
                    source_ref=f"PR #{pr_num}" if pr_num else "PR precedent",
                    source_pr_number=pr_num,
                    status=MemoryStatus.ACTIVE,
                )
                candidates.append(cm)
            except Exception as e:
                logger.debug("Could not parse memory candidate: %s", e)

        # Run Phase 3 Deterministic Applicability Engine
        applicability = self.applicability_engine.evaluate(
            repository_id=repository_id,
            repository_full_name=repository_full_name,
            changed_files=changed_files,
            structural_symbols={f: symbols for f in changed_files},
            candidate_memories=candidates,
            review_time=review_time,
            hindsight_status="available" if recalled_memories else "empty",
        )

        # Rank Applicable Rules & Exceptions
        for r in applicability.applicable_rules:
            ranked_items.append(
                RankedMemoryItem(
                    memory_id=r.get("memory_id"),
                    claim=r.get("statement"),
                    rationale=r.get("rationale"),
                    scope=r.get("scope"),
                    category="ARCHITECTURAL_RULE",
                    authority=r.get("authority", "TEAM_DECISION"),
                    reason=ContextReason.ARCHITECTURAL_MEMORY,
                    relevance_score=1.0,
                    source_pr_ref=r.get("source_ref"),
                    provenance_status="verified" if r.get("source_pr_number") else "unverified",
                )
            )

        for exc in applicability.applicable_exceptions:
            ranked_items.append(
                RankedMemoryItem(
                    memory_id=exc.get("memory_id"),
                    claim=exc.get("statement"),
                    rationale=exc.get("rationale"),
                    scope=exc.get("scope"),
                    category="APPROVED_EXCEPTION",
                    authority="APPROVED_EXCEPTION",
                    reason=ContextReason.APPROVED_EXCEPTION,
                    relevance_score=0.95,
                    source_pr_ref=exc.get("source_ref"),
                    provenance_status="verified" if exc.get("source_pr_number") else "unverified",
                )
            )

        for obs in applicability.contextual_memories:
            ranked_items.append(
                RankedMemoryItem(
                    memory_id=obs.get("memory_id"),
                    claim=obs.get("statement"),
                    rationale=obs.get("rationale"),
                    scope=obs.get("scope"),
                    category="OBSERVATION",
                    authority="CONVERSATIONAL",
                    reason=ContextReason.HISTORICAL_PRECEDENT,
                    relevance_score=0.7,
                    source_pr_ref=obs.get("source_ref"),
                    provenance_status="none",
                )
            )

        # Sort by relevance score descending
        ranked_items.sort(key=lambda m: m.relevance_score, reverse=True)
        return ranked_items[:8]

    def enforce_context_budget(
        self,
        context: PRContext,
        max_tokens: int,
    ) -> None:
        """Enforces hard bounded token limits deterministically across context sections.
        Records truncation observability: truncated_sections, items_removed, limit_reached.
        Priority-aware truncation:
        P8 (Observations) -> P7 (Issues) -> P6 (Docs) -> P5 (PRs) -> Non-primary hunks.
        P0, P1, P2, P3, P4 are always preserved.
        """
        formatted_block = context.to_formatted_context_block()
        estimated_tokens = len(formatted_block) // 4

        context.context_budget.used_tokens = estimated_tokens
        if estimated_tokens <= max_tokens:
            context.context_budget.truncated = False
            context.context_budget.limit_reached = False
            return

        context.context_budget.truncated = True
        context.context_budget.limit_reached = True
        truncated_sections: List[str] = []
        items_removed: Dict[str, int] = {}

        # 1. Truncate P8 Contextual observations first
        obs = [m for m in context.relevant_memories if m.category == "OBSERVATION"]
        if obs:
            orig_len = len(context.relevant_memories)
            context.relevant_memories = [m for m in context.relevant_memories if m.category != "OBSERVATION"]
            removed = orig_len - len(context.relevant_memories)
            truncated_sections.append("contextual_observations")
            items_removed["contextual_observations"] = removed

        # Check if under budget
        if len(context.to_formatted_context_block()) // 4 <= max_tokens:
            context.context_budget.truncated_sections = truncated_sections
            context.context_budget.items_removed = items_removed
            context.context_budget.used_tokens = len(context.to_formatted_context_block()) // 4
            return

        # 2. Truncate P7 Linked issues
        if context.related_issues:
            removed = len(context.related_issues)
            context.related_issues = []
            truncated_sections.append("related_issues")
            items_removed["related_issues"] = removed

        # Check if under budget
        if len(context.to_formatted_context_block()) // 4 <= max_tokens:
            context.context_budget.truncated_sections = truncated_sections
            context.context_budget.items_removed = items_removed
            context.context_budget.used_tokens = len(context.to_formatted_context_block()) // 4
            return

        # 3. Truncate P6 Relevant docs
        if context.relevant_docs:
            removed = len(context.relevant_docs)
            context.relevant_docs = []
            truncated_sections.append("relevant_docs")
            items_removed["relevant_docs"] = removed

        # Check if under budget
        if len(context.to_formatted_context_block()) // 4 <= max_tokens:
            context.context_budget.truncated_sections = truncated_sections
            context.context_budget.items_removed = items_removed
            context.context_budget.used_tokens = len(context.to_formatted_context_block()) // 4
            return

        # 4. Truncate P5 Historical PRs
        if context.related_prs:
            removed = len(context.related_prs)
            context.related_prs = []
            truncated_sections.append("related_prs")
            items_removed["related_prs"] = removed

        # Check if under budget
        if len(context.to_formatted_context_block()) // 4 <= max_tokens:
            context.context_budget.truncated_sections = truncated_sections
            context.context_budget.items_removed = items_removed
            context.context_budget.used_tokens = len(context.to_formatted_context_block()) // 4
            return

        # 5. Truncate non-primary hunks (keep top 3 hunks)
        if len(context.changed_hunks) > 3:
            removed = len(context.changed_hunks) - 3
            context.changed_hunks = context.changed_hunks[:3]
            truncated_sections.append("changed_hunks")
            items_removed["changed_hunks"] = removed

        context.context_budget.truncated_sections = truncated_sections
        context.context_budget.items_removed = items_removed
        context.context_budget.used_tokens = len(context.to_formatted_context_block()) // 4


_context_engine: Optional[ContextEngine] = None


def get_context_engine() -> ContextEngine:
    """Singleton getter for ContextEngine."""
    global _context_engine
    if _context_engine is None:
        _context_engine = ContextEngine()
    return _context_engine
