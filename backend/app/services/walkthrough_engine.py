"""
Phase 7 — Semantic PR Walkthrough Engine.

Transforms an existing PRContext (Phase 5 output) and ReviewAnalysisOutput (Phase 6)
into a structured, ordered, semantic walkthrough of what the PR changes, how the changes
relate structurally, and where the architectural implications are.

DESIGN CONSTRAINTS:
- Zero new AI/LLM calls. All grouping is deterministic from PRContext and Review data.
- No new memory retrieval. Uses already-ranked memories from PRContext.
- No hallucinated relationships. Every edge is grounded in AST, dependency graph,
  calls, imports, references, or test mapping from Phase 5.
- No authority escalation. Read-only; describes without issuing verdicts.
- Deterministic, bounded, and repository/SHA isolated.
"""
import logging
import re
from typing import Dict, List, Optional, Set, Tuple

from app.schemas.context import (
    ChangeType,
    ChangedFileDetail,
    HunkSymbolMap,
    PRContext,
    RelatedTestItem,
)
from app.schemas.review import ReviewAnalysisOutput, ReviewFindingSchema
from app.schemas.walkthrough import (
    ChangeGroup,
    ChangeGroupImpact,
    ChangeGroupKind,
    FileChangeEntry,
    GroupFindingEntry,
    GroupTestMapping,
    HunkSymbolDetail,
    PRWalkthrough,
    SemanticRelationship,
    SemanticRelationshipType,
    WalkthroughStep,
)

logger = logging.getLogger(__name__)

# Bounding limits
MAX_GROUPS = 15
MAX_FILES_PER_GROUP = 50
MAX_SYMBOLS_PER_GROUP = 30
MAX_RELATIONSHIPS_PER_GROUP = 30
MAX_TESTS_PER_GROUP = 20
MAX_FINDINGS_PER_GROUP = 20
MAX_STEPS = 15

# Secondary fallback path patterns
_KIND_PATTERNS: List[Tuple[ChangeGroupKind, List[str]]] = [
    # Tests — highest priority to avoid mis-labelling test helpers as services
    (ChangeGroupKind.TEST, [
        r"test", r"spec", r"__tests__", r"\.test\.", r"\.spec\.",
        r"fixtures?/", r"conftest\.py",
    ]),
    # TypeScript/Python type definitions — before SCHEMA to catch schemas?.py
    (ChangeGroupKind.TYPES, [
        r"types?/", r"interfaces?/", r"types?\.ts$", r"typing",
        r"schemas?\.py$", r"models?\.py$",
    ]),
    # Dependencies — before CONFIGURATION to catch package.json before *.json wildcard
    (ChangeGroupKind.DEPENDENCY, [
        r"requirements", r"package\.json", r"package-lock\.json",
        r"yarn\.lock", r"poetry\.lock", r"pipfile",
        r"pyproject\.toml", r"setup\.py", r"go\.sum", r"go\.mod",
    ]),
    # Schema / migrations / models
    (ChangeGroupKind.SCHEMA, [
        r"migration", r"schema", r"model", r"entity", r"\.sql$",
        r"alembic/", r"db/", r"database/", r"prisma/",
    ]),
    # API layer
    (ChangeGroupKind.API, [
        r"router", r"route", r"endpoint", r"controller", r"handler",
        r"api/", r"views?\.py$", r"urls?\.py$", r"resolvers?\.",
    ]),
    # Service / business logic
    (ChangeGroupKind.SERVICE, [
        r"service", r"usecase", r"use\.case", r"domain/", r"application/",
        r"business/", r"logic/",
    ]),
    # Configuration
    (ChangeGroupKind.CONFIGURATION, [
        r"\.yaml$", r"\.yml$", r"\.toml$", r"\.ini$", r"\.env",
        r"config/", r"settings", r"deploy", r"docker", r"\.json$",
        r"makefile", r"\.sh$", r"ci/", r"github/workflows",
    ]),
    # Documentation
    (ChangeGroupKind.DOCUMENTATION, [
        r"\.md$", r"\.rst$", r"\.txt$", r"docs?/", r"readme",
        r"changelog", r"license", r"adr/",
    ]),
    # UI / Frontend
    (ChangeGroupKind.UI, [
        r"\.tsx$", r"\.jsx$", r"\.vue$", r"\.svelte$",
        r"components?/", r"pages?/", r"views?/", r"styles?/",
        r"\.css$", r"\.scss$", r"\.sass$", r"\.less$",
    ]),
    # Utility / helpers
    (ChangeGroupKind.UTILITY, [
        r"util", r"helper", r"common/", r"shared/", r"lib/",
        r"utils?\.py$", r"utils?\.ts$", r"constants?",
    ]),
]

_RENDER_ORDER: Dict[ChangeGroupKind, int] = {
    ChangeGroupKind.DEPENDENCY: 0,
    ChangeGroupKind.CONFIGURATION: 1,
    ChangeGroupKind.SCHEMA: 2,
    ChangeGroupKind.FLOW: 3,
    ChangeGroupKind.SERVICE: 4,
    ChangeGroupKind.API: 5,
    ChangeGroupKind.UI: 6,
    ChangeGroupKind.TYPES: 7,
    ChangeGroupKind.UTILITY: 8,
    ChangeGroupKind.DOCUMENTATION: 9,
    ChangeGroupKind.TEST: 10,
    ChangeGroupKind.UNKNOWN: 11,
}

_KIND_LABELS: Dict[ChangeGroupKind, str] = {
    ChangeGroupKind.FLOW: "Feature Flow",
    ChangeGroupKind.SCHEMA: "Schema & Data Model",
    ChangeGroupKind.API: "API Layer",
    ChangeGroupKind.SERVICE: "Business Logic",
    ChangeGroupKind.TEST: "Tests",
    ChangeGroupKind.CONFIGURATION: "Configuration",
    ChangeGroupKind.DOCUMENTATION: "Documentation",
    ChangeGroupKind.DEPENDENCY: "Dependencies",
    ChangeGroupKind.UI: "UI Components",
    ChangeGroupKind.TYPES: "Type Definitions",
    ChangeGroupKind.UTILITY: "Utilities",
    ChangeGroupKind.UNKNOWN: "Other Changes",
}


def _classify_file(path: str) -> ChangeGroupKind:
    """Secondary fallback: classify a file into a ChangeGroupKind by path pattern."""
    path_lower = path.lower()
    for kind, patterns in _KIND_PATTERNS:
        if any(re.search(p, path_lower) for p in patterns):
            return kind
    return ChangeGroupKind.UNKNOWN




def _extract_stem(path: str) -> str:
    """Extracts a semantic domain stem from a file path or filename."""
    if _classify_file(path) == ChangeGroupKind.DEPENDENCY:
        parts = path.replace("\\", "/").split("/")
        if len(parts) > 1 and parts[0] not in (".", "..", "root"):
            return f"{parts[0]}_deps"
        return "dependencies"

    # Remove extension
    base = re.sub(r"\.[a-zA-Z0-9]+$", "", path)
    # Extract filename part
    filename = base.split("/")[-1].split("\\")[-1]
    # Remove common architectural suffixes/prefixes
    cleaned = re.sub(
        r"(controller|service|repository|repo|handler|test|spec|impl|model|schema|util|helper|dto|view|route)$",
        "",
        filename,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^(test_|spec_|mock_)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[_\-0-9]", "", cleaned).lower()
    return cleaned if len(cleaned) >= 3 else ""


class WalkthroughEngine:
    """
    Derives a genuinely semantic PR walkthrough from an existing PRContext.

    Stateless and dependency-free beyond PRContext and ReviewAnalysisOutput.
    Never calls GitHub, Hindsight, Groq, or external networks.
    """

    def build_walkthrough(
        self,
        context: PRContext,
        review: Optional[ReviewAnalysisOutput] = None,
    ) -> PRWalkthrough:
        """
        Builds a deterministic, structurally-grounded PRWalkthrough.
        """
        pr = context.pr
        intent = context.intent
        changed_files = context.changed_files or []

        if not changed_files:
            return self._build_empty_walkthrough(context)

        # 1. Build symbol index: symbol -> set of file paths defining or declaring it
        sym_to_files: Dict[str, Set[str]] = {}
        file_to_syms: Dict[str, Set[str]] = {}
        for f in changed_files:
            f_path = f.path
            file_to_syms[f_path] = set(f.affected_symbols)
            for s in f.affected_symbols:
                sym_to_files.setdefault(s, set()).add(f_path)
            for c in f.classes:
                sym_to_files.setdefault(c, set()).add(f_path)
            for fn in f.functions:
                sym_to_files.setdefault(fn, set()).add(f_path)

        for d in context.definitions:
            if d.file_path:
                sym_to_files.setdefault(d.symbol, set()).add(d.file_path)
                file_to_syms.setdefault(d.file_path, set()).add(d.symbol)

        changed_files_map: Dict[str, ChangedFileDetail] = {f.path: f for f in changed_files}
        changed_file_paths = sorted(changed_files_map.keys())

        # 2. Build Structural Connectivity Graph between changed files
        adjacency: Dict[str, Set[str]] = {p: set() for p in changed_file_paths}
        all_relationships: List[SemanticRelationship] = []

        # 2a. Evidence from context.dependencies.edges
        if context.dependencies and context.dependencies.edges:
            for edge in context.dependencies.edges:
                src_files = self._resolve_node_to_files(edge.source, changed_files_map, sym_to_files)
                tgt_files = self._resolve_node_to_files(edge.target, changed_files_map, sym_to_files)
                rel_type = self._map_relationship_type(edge.relationship)
                if src_files and tgt_files:
                    for sf in src_files:
                        for tf in tgt_files:
                            if sf != tf:
                                adjacency[sf].add(tf)
                                adjacency[tf].add(sf)
                                all_relationships.append(
                                    SemanticRelationship(
                                        source=edge.source if edge.source in sym_to_files else sf,
                                        target=edge.target if edge.target in sym_to_files else tf,
                                        relationship=rel_type,
                                        detail=f"{edge.source} {edge.relationship} {edge.target}",
                                        evidence_source="dependency_graph",
                                    )
                                )
                elif src_files and not tgt_files:
                    # Target is outside PR (external dependency)
                    for sf in src_files:
                        all_relationships.append(
                            SemanticRelationship(
                                source=edge.source if edge.source in sym_to_files else sf,
                                target=edge.target,
                                relationship=rel_type,
                                detail=f"Calls external {edge.target}",
                                evidence_source="dependency_graph",
                            )
                        )
                elif tgt_files and not src_files:
                    # Source is outside PR (external caller)
                    for tf in tgt_files:
                        all_relationships.append(
                            SemanticRelationship(
                                source=edge.source,
                                target=edge.target if edge.target in sym_to_files else tf,
                                relationship=rel_type,
                                detail=f"External caller {edge.source} calls {edge.target}",
                                evidence_source="dependency_graph",
                            )
                        )

        # 2b. Evidence from context.changed_hunks
        for hunk in (context.changed_hunks or []):
            sf = hunk.file_path
            if sf not in changed_files_map:
                continue
            for call in hunk.changed_calls:
                target_files = sym_to_files.get(call, set())
                for tf in target_files:
                    if tf in changed_files_map and tf != sf:
                        adjacency[sf].add(tf)
                        adjacency[tf].add(sf)
                        all_relationships.append(
                            SemanticRelationship(
                                source=sf,
                                target=call,
                                relationship=SemanticRelationshipType.CALLS,
                                detail=f"Calls {call} defined in {tf}",
                                evidence_source="hunk_call",
                            )
                        )
            for imp in hunk.changed_imports:
                target_files = sym_to_files.get(imp, set())
                for tf in target_files:
                    if tf in changed_files_map and tf != sf:
                        adjacency[sf].add(tf)
                        adjacency[tf].add(sf)
                        all_relationships.append(
                            SemanticRelationship(
                                source=sf,
                                target=imp,
                                relationship=SemanticRelationshipType.IMPORTS,
                                detail=f"Imports {imp} from {tf}",
                                evidence_source="hunk_import",
                            )
                        )

        # 2c. Evidence from context.definitions and references
        for d in (context.definitions or []):
            def_file = d.file_path
            if def_file not in changed_files_map:
                continue
            for caller in d.callers:
                caller_files = sym_to_files.get(caller, set())
                if caller_files:
                    for cf in caller_files:
                        if cf in changed_files_map and cf != def_file:
                            adjacency[cf].add(def_file)
                            adjacency[def_file].add(cf)
                            all_relationships.append(
                                SemanticRelationship(
                                    source=caller,
                                    target=d.symbol,
                                    relationship=SemanticRelationshipType.CALLS,
                                    detail=f"{caller} calls {d.symbol}",
                                    evidence_source="ast",
                                )
                            )
                else:
                    # External caller outside changed files
                    all_relationships.append(
                        SemanticRelationship(
                            source=caller,
                            target=d.symbol,
                            relationship=SemanticRelationshipType.CALLS,
                            detail=f"External caller {caller} calls {d.symbol}",
                            evidence_source="ast",
                        )
                    )
            for callee in d.callees:
                callee_files = sym_to_files.get(callee, set())
                if callee_files:
                    for cf in callee_files:
                        if cf in changed_files_map and cf != def_file:
                            adjacency[def_file].add(cf)
                            adjacency[cf].add(def_file)
                            all_relationships.append(
                                SemanticRelationship(
                                    source=d.symbol,
                                    target=callee,
                                    relationship=SemanticRelationshipType.CALLS,
                                    detail=f"{d.symbol} calls {callee}",
                                    evidence_source="ast",
                                )
                            )
                else:
                    # External callee outside changed files
                    all_relationships.append(
                        SemanticRelationship(
                            source=d.symbol,
                            target=callee,
                            relationship=SemanticRelationshipType.CALLS,
                            detail=f"{d.symbol} calls external {callee}",
                            evidence_source="ast",
                        )
                    )
            for ref in d.references:
                ref_files = sym_to_files.get(ref, set())
                if ref_files:
                    for rf in ref_files:
                        if rf in changed_files_map and rf != def_file:
                            adjacency[rf].add(def_file)
                            adjacency[def_file].add(rf)
                            all_relationships.append(
                                SemanticRelationship(
                                    source=ref,
                                    target=d.symbol,
                                    relationship=SemanticRelationshipType.REFERENCES,
                                    detail=f"{ref} references {d.symbol}",
                                    evidence_source="ast",
                                )
                            )
                else:
                    all_relationships.append(
                        SemanticRelationship(
                            source=ref,
                            target=d.symbol,
                            relationship=SemanticRelationshipType.REFERENCES,
                            detail=f"External {ref} references {d.symbol}",
                            evidence_source="ast",
                        )
                    )

        # 2d. Evidence from context.related_tests
        for t in (context.related_tests or []):
            tf = t.test_file_path
            if tf in changed_files_map:
                target_files = set()
                if t.target_symbol_or_file in changed_files_map:
                    target_files.add(t.target_symbol_or_file)
                if t.target_symbol_or_file in sym_to_files:
                    target_files.update(sym_to_files[t.target_symbol_or_file].intersection(changed_files_map.keys()))
                for target_file in target_files:
                    if target_file != tf:
                        adjacency[tf].add(target_file)
                        adjacency[target_file].add(tf)
                        all_relationships.append(
                            SemanticRelationship(
                                source=tf,
                                target=t.target_symbol_or_file,
                                relationship=SemanticRelationshipType.TESTS,
                                detail=f"Tests {t.target_symbol_or_file}",
                                evidence_source="related_test",
                            )
                        )

        # 2e. Secondary evidence from context.related_files (external dependencies / unedited related files)
        # NOTE ON PHASE 5 CONTEXT FIELDS:
        # - context.calls: Canonical call evidence is consumed with line numbers, enclosing symbols,
        #   and AST grounding via context.changed_hunks and context.definitions (callers/callees).
        # - context.imports: Canonical import evidence is consumed with line numbers and hunk positions
        #   via context.changed_hunks.changed_imports.
        # - context.references: Canonical reference evidence is consumed via context.definitions
        #   (SymbolDefinitionReference.references).
        # - context.related_files: Consumed here as secondary external dependency evidence (evidence_source="related_file")
        #   without overriding stronger AST/dependency graph edges.
        for rf in (context.related_files or []):
            if rf.path:
                src_match = None
                for cf_path in changed_file_paths:
                    if cf_path in (rf.reason_detail or "") or cf_path in str(rf.reason or ""):
                        src_match = cf_path
                        break
                if not src_match and changed_file_paths:
                    for cf_path in changed_file_paths:
                        if cf_path != rf.path:
                            src_match = cf_path
                            break
                if src_match and src_match != rf.path:
                    all_relationships.append(
                        SemanticRelationship(
                            source=src_match,
                            target=rf.path,
                            relationship=SemanticRelationshipType.DEPENDS_ON,
                            detail=rf.reason_detail or f"Related to {rf.path}",
                            evidence_source="related_file",
                        )
                    )

        # 2f. Deduplicate relationships deterministically
        unique_relationships: List[SemanticRelationship] = self._deduplicate_relationships(all_relationships)

        # 3. Find connected components (Structural clusters)
        raw_components = self._find_connected_components(changed_file_paths, adjacency)

        # 4. Handle isolated files (no AST/graph edges)
        # Check if isolated files share a distinct domain stem or fallback to architectural kind
        file_clusters: List[List[str]] = []
        isolated_files: List[str] = []
        for comp in raw_components:
            if len(comp) > 1:
                file_clusters.append(sorted(comp))
            else:
                isolated_files.append(comp[0])

        if isolated_files:
            # Group isolated files by domain stem first
            stem_groups: Dict[str, List[str]] = {}
            unstemmed: List[str] = []
            for path in isolated_files:
                stem = _extract_stem(path)
                if stem:
                    stem_groups.setdefault(stem, []).append(path)
                else:
                    unstemmed.append(path)

            for stem, stem_files in stem_groups.items():
                file_clusters.append(sorted(stem_files))

            # Secondary fallback: group remaining unstemmed isolated by path kind
            kind_groups: Dict[ChangeGroupKind, List[str]] = {}
            for path in unstemmed:
                kind = _classify_file(path)
                kind_groups.setdefault(kind, []).append(path)

            for kind, k_files in sorted(kind_groups.items(), key=lambda x: _render_order(x[0])):
                file_clusters.append(sorted(k_files))

        # 5. Extract and Index Phase 6 review findings
        # True count of findings, not matching files!
        findings_by_file: Dict[str, List[ReviewFindingSchema]] = {}
        if review and review.findings:
            for f in review.findings:
                if f.file_path:
                    findings_by_file.setdefault(f.file_path, []).append(f)

        # 6. Build ChangeGroups from clusters
        groups: List[ChangeGroup] = []
        truncation_notes: List[str] = []
        is_truncated = False

        if len(file_clusters) > MAX_GROUPS:
            is_truncated = True
            truncation_notes.append(f"Groups truncated from {len(file_clusters)} to {MAX_GROUPS}")
            file_clusters = file_clusters[:MAX_GROUPS]

        for idx, cluster_paths in enumerate(file_clusters):
            group_files_detail = [changed_files_map[p] for p in cluster_paths]
            cluster_paths_set = set(cluster_paths)

            # Gather symbols
            cluster_symbols_set: Set[str] = set()
            for fd in group_files_detail:
                cluster_symbols_set.update(fd.affected_symbols)
                cluster_symbols_set.update(fd.classes)
                cluster_symbols_set.update(fd.functions)

            all_cluster_symbols = sorted(list(cluster_symbols_set))
            total_symbols_count = len(all_cluster_symbols)
            truncated_symbols = max(0, total_symbols_count - MAX_SYMBOLS_PER_GROUP)
            bounded_symbols = all_cluster_symbols[:MAX_SYMBOLS_PER_GROUP]

            # Gather relationships touching this cluster
            cluster_rel_set: List[SemanticRelationship] = []
            for r in unique_relationships:
                # Relationship touches this group if source or target resolves to a file or symbol in group
                src_in = r.source in cluster_paths_set or r.source in cluster_symbols_set
                tgt_in = r.target in cluster_paths_set or r.target in cluster_symbols_set
                if src_in or tgt_in:
                    cluster_rel_set.append(r)

            total_rel_count = len(cluster_rel_set)
            truncated_rel = max(0, total_rel_count - MAX_RELATIONSHIPS_PER_GROUP)
            bounded_rel = cluster_rel_set[:MAX_RELATIONSHIPS_PER_GROUP]

            # Determine kind, label, purpose
            kind, label, purpose = self._determine_group_identity(
                group_files_detail, bounded_rel, idx
            )

            # Map tests
            group_tests = self._map_tests_to_group(
                cluster_paths_set, cluster_symbols_set, context.related_tests or []
            )
            has_test_coverage = len(group_tests) > 0 or kind == ChangeGroupKind.TEST

            # Map findings
            group_findings: List[GroupFindingEntry] = []
            for p in cluster_paths:
                for rf in findings_by_file.get(p, []):
                    entry = GroupFindingEntry(
                        id=f"finding-{len(group_findings)}-{p.replace('/', '_')}",
                        category=rf.category,
                        severity=rf.severity,
                        title=rf.title,
                        message=rf.message,
                        file_path=rf.file_path,
                        line_number=rf.line_number,
                        historical_pr_ref=rf.historical_pr_ref,
                        rationale=rf.rationale,
                    )
                    group_findings.append(entry)

            # Sort findings deterministically
            group_findings.sort(key=lambda x: (x.file_path, x.line_number or 0, x.title))
            actual_findings_count = len(group_findings)
            bounded_findings = group_findings[:MAX_FINDINGS_PER_GROUP]

            # Structural impact evaluation
            impact, impact_justification = self._evaluate_impact(
                kind, group_files_detail, bounded_rel, cluster_paths_set, changed_files_map
            )

            # Build file change entries
            total_files_count = len(group_files_detail)
            truncated_files = max(0, total_files_count - MAX_FILES_PER_GROUP)
            bounded_file_details = group_files_detail[:MAX_FILES_PER_GROUP]

            file_entries = [
                self._build_file_entry(fd, context.changed_hunks or [])
                for fd in bounded_file_details
            ]

            observed_impl = self._build_observed_implementation(
                group_files_detail, bounded_symbols, bounded_rel
            )

            evidence_items = [
                f"{r.source} {r.relationship.value} {r.target} ({r.evidence_source})"
                for r in bounded_rel[:5]
            ]
            if has_test_coverage and group_tests:
                evidence_items.append(f"Verified tests: {group_tests[0].test_file_path}")

            group = ChangeGroup(
                id=f"group-{idx}-{self._slugify(label)}",
                kind=kind,
                label=label,
                description=f"{label} comprising {total_files_count} file(s).",
                purpose=purpose,
                observed_implementation=observed_impl,
                files=file_entries,
                impact=impact,
                impact_justification=impact_justification,
                symbols_changed=bounded_symbols,
                relationships=bounded_rel,
                tests=group_tests[:MAX_TESTS_PER_GROUP],
                findings=bounded_findings,
                evidence=evidence_items,
                related_findings_count=actual_findings_count,
                has_test_coverage=has_test_coverage,
                ordering=_render_order(kind),
                total_files_count=total_files_count,
                total_symbols_count=total_symbols_count,
                total_relationships_count=total_rel_count,
                truncated_files=truncated_files,
                truncated_symbols=truncated_symbols,
                truncated_relationships=truncated_rel,
            )
            groups.append(group)

        # Sort groups deterministically by rendering order, then id
        groups.sort(key=lambda g: (g.ordering, g.id))

        # 7. Build narrative WalkthroughSteps
        steps: List[WalkthroughStep] = []
        for step_num, g in enumerate(groups[:MAX_STEPS], start=1):
            key_rels = [
                f"{r.source} -> {r.target}" for r in g.relationships[:3]
            ]
            step = WalkthroughStep(
                step_number=step_num,
                group_id=g.id,
                headline=f"{g.label}: {g.purpose.split('.')[0]}",
                detail=f"{g.observed_implementation} {g.impact_justification}",
                files_count=g.total_files_count,
                impact=g.impact,
                primary_symbols=g.symbols_changed[:4],
                key_relationships=key_rels,
            )
            steps.append(step)

        # 8. Global walkthrough summary flags
        kinds_present = {g.kind for g in groups}
        has_schema = any(g.kind == ChangeGroupKind.SCHEMA or any(_classify_file(f.path) == ChangeGroupKind.SCHEMA for f in g.files) for g in groups)
        has_api = any(g.kind == ChangeGroupKind.API or any(_classify_file(f.path) == ChangeGroupKind.API for f in g.files) for g in groups)
        has_test = any(g.kind == ChangeGroupKind.TEST or any(_classify_file(f.path) == ChangeGroupKind.TEST for f in g.files) for g in groups) or bool(context.related_tests)
        has_config = any(g.kind == ChangeGroupKind.CONFIGURATION or any(_classify_file(f.path) == ChangeGroupKind.CONFIGURATION for f in g.files) for g in groups)

        total_surfaced_findings = sum(g.related_findings_count for g in groups)

        total_add = sum(f.additions for f in changed_files)
        total_del = sum(f.deletions for f in changed_files)

        declared_intent = intent.summary or pr.title
        observed_summary = (
            f"Observed {len(groups)} logical change group(s) across {len(changed_files)} files. "
            + (" ".join(f"[{g.label}: {len(g.files)} files]" for g in groups[:4]))
        )

        return PRWalkthrough(
            pr_number=pr.pr_number,
            pr_title=pr.title,
            repository_id=pr.repository_id,
            head_sha=pr.head_sha,
            intent_summary=intent.summary,
            declared_intent=declared_intent,
            observed_implementation=observed_summary,
            change_type=intent.likely_change_type.value if hasattr(intent.likely_change_type, "value") else str(intent.likely_change_type),
            groups=groups,
            steps=steps,
            total_files_changed=len(changed_files),
            total_additions=total_add,
            total_deletions=total_del,
            groups_count=len(groups),
            has_schema_changes=has_schema,
            has_api_changes=has_api,
            has_test_changes=has_test,
            has_config_changes=has_config,
            surfaced_finding_count=total_surfaced_findings,
            is_truncated=is_truncated,
            truncation_notes=truncation_notes,
            derived_from="context_engine_v5",
        )

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _resolve_node_to_files(
        self,
        node_id: str,
        changed_files_map: Dict[str, ChangedFileDetail],
        sym_to_files: Dict[str, Set[str]],
    ) -> Set[str]:
        """Resolves a dependency graph node (path or symbol) to matching changed files."""
        res: Set[str] = set()
        if node_id in changed_files_map:
            res.add(node_id)
        if node_id in sym_to_files:
            res.update(sym_to_files[node_id].intersection(changed_files_map.keys()))
        return res

    def _map_relationship_type(self, raw: str) -> SemanticRelationshipType:
        raw_upper = raw.upper()
        if "CALL" in raw_upper:
            return SemanticRelationshipType.CALLS
        if "IMPORT" in raw_upper:
            return SemanticRelationshipType.IMPORTS
        if "INHERIT" in raw_upper:
            return SemanticRelationshipType.REFERENCES
        if "TEST" in raw_upper:
            return SemanticRelationshipType.TESTS
        return SemanticRelationshipType.DEPENDS_ON

    def _deduplicate_relationships(
        self, rels: List[SemanticRelationship]
    ) -> List[SemanticRelationship]:
        seen = set()
        deduped = []
        for r in rels:
            key = (r.source, r.target, r.relationship.value)
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        deduped.sort(key=lambda r: (r.source, r.relationship.value, r.target))
        return deduped

    def _find_connected_components(
        self, nodes: List[str], adj: Dict[str, Set[str]]
    ) -> List[List[str]]:
        visited: Set[str] = set()
        components: List[List[str]] = []

        for node in nodes:
            if node not in visited:
                comp: List[str] = []
                queue = [node]
                visited.add(node)
                while queue:
                    curr = queue.pop(0)
                    comp.append(curr)
                    for neighbor in sorted(adj.get(curr, set())):
                        if neighbor not in visited and neighbor in adj:
                            visited.add(neighbor)
                            queue.append(neighbor)
                components.append(sorted(comp))

        return components

    def _determine_group_identity(
        self,
        files: List[ChangedFileDetail],
        relationships: List[SemanticRelationship],
        index: int,
    ) -> Tuple[ChangeGroupKind, str, str]:
        """
        Determines semantic kind, human label, and architectural purpose for a cluster.
        """
        file_kinds = [_classify_file(f.path) for f in files]
        distinct_kinds = set(file_kinds)

        # Find dominant domain stem
        stems = [_extract_stem(f.path) for f in files]
        valid_stems = [s for s in stems if s]
        dominant_stem = max(set(valid_stems), key=valid_stems.count) if valid_stems else ""
        domain_name = dominant_stem.capitalize() if dominant_stem else ""

        # Production kinds (exclude pure tests/docs when determining primary architectural role)
        prod_kinds = [k for k in file_kinds if k not in (ChangeGroupKind.TEST, ChangeGroupKind.DOCUMENTATION)]
        distinct_prod = set(prod_kinds)

        if not prod_kinds:
            dominant_kind = ChangeGroupKind.TEST if ChangeGroupKind.TEST in distinct_kinds else file_kinds[0]
            label = f"{domain_name} Tests" if (domain_name and dominant_kind == ChangeGroupKind.TEST) else _KIND_LABELS[dominant_kind]
            purpose = "Verifies system behavior, regressions, and test suite coverage."
            return dominant_kind, label, purpose

        # Multi-layer flow: Requires actual structural evidence connecting the cluster
        has_structural_edges = any(
            r.evidence_source in ("ast", "dependency_graph", "hunk_call", "hunk_import")
            for r in relationships
        )
        if (
            len(files) > 1
            and len(distinct_prod) > 1
            and has_structural_edges
            and (
                ChangeGroupKind.SERVICE in distinct_prod
                or ChangeGroupKind.API in distinct_prod
                or ChangeGroupKind.SCHEMA in distinct_prod
            )
        ):
            dominant_kind = ChangeGroupKind.FLOW
            label = f"{domain_name} Flow" if domain_name else "End-to-End Flow"
            purpose = (
                f"Coordinates changes across multiple architectural layers "
                f"({', '.join(k.value for k in sorted(distinct_kinds, key=_render_order))})."
            )
            return dominant_kind, label, purpose

        # Single dominant production kind
        dominant_kind = max(set(prod_kinds), key=prod_kinds.count)
        label_base = _KIND_LABELS.get(dominant_kind, "Module Changes")
        if dominant_kind == ChangeGroupKind.DEPENDENCY:
            label = (
                f"{domain_name.replace('_deps', '').capitalize()} Dependencies"
                if domain_name and domain_name.lower() not in ("dependencies", "dependency", "package", "packagelock", "deps")
                else "Dependencies"
            )
        else:
            label = f"{domain_name} {label_base}" if domain_name else label_base

        purpose_map = {
            ChangeGroupKind.FLOW: "Coordinates changes across multiple architectural layers.",
            ChangeGroupKind.SCHEMA: "Defines persistence models, entities, and database migrations.",
            ChangeGroupKind.API: "Handles incoming API requests, routing, parameter validation, and response formatting.",
            ChangeGroupKind.SERVICE: "Executes business logic, domain rules, and orchestrates workflows.",
            ChangeGroupKind.TEST: "Verifies system behavior, regressions, and test suite coverage.",
            ChangeGroupKind.CONFIGURATION: "Controls operational settings, deployment rules, and system configuration.",
            ChangeGroupKind.DOCUMENTATION: "Documents architectural decisions, specifications, or guides.",
            ChangeGroupKind.DEPENDENCY: "Manages package dependencies, lockfiles, and third-party version constraints.",
            ChangeGroupKind.UI: "Renders user interfaces and visual client components.",
            ChangeGroupKind.TYPES: "Specifies typed interfaces, data contracts, and protocol definitions.",
            ChangeGroupKind.UTILITY: "Provides shared helper functions and cross-cutting utilities.",
            ChangeGroupKind.UNKNOWN: "Contains modifications to project files.",
        }
        purpose = purpose_map.get(dominant_kind, "Modifies project files.")
        return dominant_kind, label, purpose

    def _evaluate_impact(
        self,
        kind: ChangeGroupKind,
        files: List[ChangedFileDetail],
        relationships: List[SemanticRelationship],
        cluster_paths: Set[str],
        all_changed_files: Dict[str, ChangedFileDetail],
    ) -> Tuple[ChangeGroupImpact, str]:
        """
        Evaluates blast radius strictly from verified evidence.
        Does NOT assume SERVICE + API = CROSS_CUTTING without caller/callee links.
        """
        # Check for cross-boundary relationships (files or symbols)
        cluster_all = cluster_paths.union(
            s for f in files for s in (f.affected_symbols + f.classes + f.functions)
        )
        cross_boundary_calls = [
            r for r in relationships
            if (r.source not in cluster_all or r.target not in cluster_all)
        ]

        # Dependency changes: require structural evidence for FOUNDATIONAL / CROSS_CUTTING
        if kind == ChangeGroupKind.DEPENDENCY or any(_classify_file(f.path) == ChangeGroupKind.DEPENDENCY for f in files):
            has_schema_break = any(_classify_file(p) == ChangeGroupKind.SCHEMA for p in all_changed_files)
            has_infra = any(_classify_file(p) == ChangeGroupKind.CONFIGURATION for p in all_changed_files)
            has_downstream_persistence_or_infra = any(
                r.target and any(k in r.target.lower() for k in ("db", "database", "schema", "infra", "persistence", "config", "session"))
                for r in cross_boundary_calls
            )
            if has_schema_break or (has_infra and cross_boundary_calls) or has_downstream_persistence_or_infra:
                return (
                    ChangeGroupImpact.FOUNDATIONAL,
                    "Foundational: Dependency updates cross persistence or infrastructure boundaries affecting downstream consumers.",
                )
            if cross_boundary_calls:
                return (
                    ChangeGroupImpact.CROSS_CUTTING,
                    f"Cross-cutting: Dependency changes connect to modified application modules ({len(cross_boundary_calls)} external edge(s)).",
                )
            return (
                ChangeGroupImpact.CONTAINED,
                "Contained: Dependency updates manage package versions without detected cross-boundary architectural breakage.",
            )

        # Schema changes
        if kind == ChangeGroupKind.SCHEMA or any(_classify_file(f.path) == ChangeGroupKind.SCHEMA for f in files):
            if cross_boundary_calls or len(all_changed_files) > len(files):
                return (
                    ChangeGroupImpact.FOUNDATIONAL,
                    "Foundational: Schema modifications alter persistence boundaries affecting downstream consumers.",
                )
            return (
                ChangeGroupImpact.CROSS_CUTTING,
                "Cross-cutting: Schema definition changes modify structural models.",
            )

        # Cross-layer flow or external callers
        if kind == ChangeGroupKind.FLOW:
            return (
                ChangeGroupImpact.CROSS_CUTTING,
                f"Cross-cutting: Multi-layer flow with structural calls spanning architectural layers ({len(relationships)} verified edge(s)).",
            )

        if cross_boundary_calls:
            return (
                ChangeGroupImpact.CROSS_CUTTING,
                f"Cross-cutting: Verified structural dependencies connect to external modules ({len(cross_boundary_calls)} external edge(s)).",
            )

        # Types exported
        if kind == ChangeGroupKind.TYPES and any(f.classes for f in files):
            return (
                ChangeGroupImpact.CROSS_CUTTING,
                "Cross-cutting: Interface contract changes affect external callers.",
            )

        # Contained
        return (
            ChangeGroupImpact.CONTAINED,
            "Contained: Modifications and relationships remain bounded within this component with no external callers detected.",
        )

    def _map_tests_to_group(
        self,
        cluster_paths: Set[str],
        cluster_symbols: Set[str],
        related_tests: List[RelatedTestItem],
    ) -> List[GroupTestMapping]:
        """Maps related tests by file path OR symbol definitions."""
        mapped: List[GroupTestMapping] = []
        seen = set()

        for t in related_tests:
            target = t.target_symbol_or_file
            is_matched = (target in cluster_paths) or (target in cluster_symbols)
            if not is_matched:
                # Check basename match
                target_base = target.split("/")[-1]
                for p in cluster_paths:
                    if p.endswith(target_base):
                        is_matched = True
                        break

            if is_matched:
                key = (t.test_file_path, target)
                if key not in seen:
                    seen.add(key)
                    mapped.append(
                        GroupTestMapping(
                            test_file_path=t.test_file_path,
                            target_symbol_or_file=target,
                            test_name=getattr(t, "snippet", None),
                            relationship="TESTS",
                            is_in_pr_diff=t.is_in_pr_diff,
                        )
                    )

        mapped.sort(key=lambda m: (m.test_file_path, m.target_symbol_or_file))
        return mapped

    def _build_file_entry(
        self, f: ChangedFileDetail, hunks: List[HunkSymbolMap]
    ) -> FileChangeEntry:
        file_hunks = [
            HunkSymbolDetail(
                file_path=h.file_path,
                start_line=h.start_line,
                end_line=h.end_line,
                enclosing_symbol=h.enclosing_class or h.enclosing_function,
                changed_symbols=h.changed_symbols[:5],
                calls=h.changed_calls[:5],
                imports=h.changed_imports[:5],
            )
            for h in hunks
            if h.file_path == f.path
        ]
        return FileChangeEntry(
            path=f.path,
            status=f.status,
            additions=f.additions,
            deletions=f.deletions,
            language=f.language,
            affected_symbols=f.affected_symbols[:6],
            hunks=file_hunks[:5],
            note=self._build_file_note(f),
        )

    def _build_file_note(self, f: ChangedFileDetail) -> Optional[str]:
        if f.status == "added":
            return "New file added."
        if f.status == "removed":
            return "File deleted."
        if f.classes:
            return f"Modifies class {', '.join(f.classes[:2])}."
        if f.functions:
            return f"Modifies function {', '.join(f.functions[:2])}."
        return None

    def _build_observed_implementation(
        self,
        files: List[ChangedFileDetail],
        symbols: List[str],
        rels: List[SemanticRelationship],
    ) -> str:
        is_dep_only = all(_classify_file(f.path) == ChangeGroupKind.DEPENDENCY for f in files)
        if is_dep_only:
            manifests = [f.path for f in files if not any(lock in f.path.lower() for lock in ("lock", "sum"))]
            lockfiles = [f.path for f in files if any(lock in f.path.lower() for lock in ("lock", "sum"))]
            if manifests and lockfiles:
                return f"Synchronizes package manifest ({', '.join(manifests)}) with locked dependency resolutions ({', '.join(lockfiles)})."
            elif lockfiles:
                return f"Updates locked dependency resolutions ({', '.join(lockfiles)})."
            else:
                return f"Updates package dependency specifications ({', '.join(f.path for f in files)})."

        parts = [f"Alters {len(files)} file(s)"]
        if symbols:
            parts.append(f"affecting {len(symbols)} symbol(s) ({', '.join(symbols[:3])})")
        if rels:
            parts.append(f"with {len(rels)} structural relationship edge(s)")
        return "; ".join(parts) + "."

    def _slugify(self, text: str) -> str:
        s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
        return s.strip("-") or "group"

    def _build_empty_walkthrough(self, context: PRContext) -> PRWalkthrough:
        pr = context.pr
        return PRWalkthrough(
            pr_number=pr.pr_number,
            pr_title=pr.title,
            repository_id=pr.repository_id,
            head_sha=pr.head_sha,
            intent_summary=context.intent.summary or "No changes detected.",
            declared_intent=context.intent.summary or "No changes detected.",
            observed_implementation="Empty PR diff with 0 changed files.",
            change_type=context.intent.likely_change_type.value if hasattr(context.intent.likely_change_type, "value") else str(context.intent.likely_change_type),
            groups=[],
            steps=[],
            total_files_changed=0,
            total_additions=0,
            total_deletions=0,
            groups_count=0,
            has_schema_changes=False,
            has_api_changes=False,
            has_test_changes=False,
            has_config_changes=False,
            surfaced_finding_count=0,
            is_truncated=False,
            truncation_notes=[],
            derived_from="context_engine_v5_semantic",
        )


def _render_order(kind: ChangeGroupKind) -> int:
    return _RENDER_ORDER.get(kind, 99)


# Singleton instance
_walkthrough_engine: Optional[WalkthroughEngine] = None


def get_walkthrough_engine() -> WalkthroughEngine:
    global _walkthrough_engine
    if _walkthrough_engine is None:
        _walkthrough_engine = WalkthroughEngine()
    return _walkthrough_engine
