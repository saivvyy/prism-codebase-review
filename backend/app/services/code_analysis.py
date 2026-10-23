import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from tree_sitter import Language, Node, Parser
import tree_sitter_cpp as tscpp
import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjs
import tree_sitter_python as tspy
import tree_sitter_typescript as tsts

from app.schemas.analysis import (
    ChangedRegion,
    ClassDefinition,
    FileAnalysisResult,
    FunctionCall,
    FunctionDefinition,
    ImportStatement,
)

logger = logging.getLogger(__name__)


class CodeAnalysisService:
    """Tree-sitter code analysis service extracting structural context from source code and PR diffs."""

    EXTENSION_MAP = {
        ".py": "python",
        ".pyi": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".mts": "typescript",
        ".cts": "typescript",
        ".tsx": "tsx",
        ".java": "java",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".hpp": "cpp",
        ".h": "cpp",
        ".c": "cpp",
    }

    def __init__(self):
        self._parsers: Dict[str, Parser] = {}
        self._init_languages()

    def _init_languages(self) -> None:
        """Pre-initializes Tree-sitter parsers for supported languages."""
        try:
            self._parsers["python"] = Parser(Language(tspy.language()))
            self._parsers["javascript"] = Parser(Language(tsjs.language()))
            self._parsers["typescript"] = Parser(Language(tsts.language_typescript()))
            self._parsers["tsx"] = Parser(Language(tsts.language_tsx()))
            self._parsers["java"] = Parser(Language(tsjava.language()))
            self._parsers["cpp"] = Parser(Language(tscpp.language()))
        except Exception as e:
            logger.error("Failed initializing tree-sitter languages: %s", e)

    def detect_language(self, file_path: str) -> Optional[str]:
        """Detects supported language from file extension."""
        _, ext = os.path.splitext(file_path.lower())
        return self.EXTENSION_MAP.get(ext)

    def is_supported(self, file_path: str) -> bool:
        """Checks if file extension is supported for syntax parsing."""
        return self.detect_language(file_path) is not None

    def analyze_file(
        self,
        file_path: str,
        content: str,
        patch: Optional[str] = None,
    ) -> FileAnalysisResult:
        """Parses source code into AST and extracts classes, functions, imports, calls, and diff changes."""
        lang = self.detect_language(file_path)
        if not lang or lang not in self._parsers:
            # Safely handle unsupported file formats (markdown, json, yaml, images, etc.)
            changed_regions = self._parse_patch_regions(patch) if patch else []
            return FileAnalysisResult(
                file_path=file_path,
                language=lang or "unsupported",
                is_supported=False,
                parse_success=True,
                changed_regions=changed_regions,
                summary=f"Unsupported format ({os.path.splitext(file_path)[1] or 'unknown'}). {len(changed_regions)} diff regions extracted without AST.",
            )

        parser = self._parsers[lang]
        code_bytes = content.encode("utf-8", errors="replace")

        try:
            tree = parser.parse(code_bytes)
        except Exception as e:
            logger.error("Tree-sitter parser crash on %s: %s", file_path, e)
            changed_regions = self._parse_patch_regions(patch) if patch else []
            return FileAnalysisResult(
                file_path=file_path,
                language=lang,
                is_supported=True,
                parse_success=False,
                error_message=str(e),
                changed_regions=changed_regions,
                summary="Failed parsing file due to syntax error.",
            )

        classes: List[ClassDefinition] = []
        functions: List[FunctionDefinition] = []
        imports: List[ImportStatement] = []
        calls: List[FunctionCall] = []

        # Traverse AST
        self._traverse_node(
            node=tree.root_node,
            code_bytes=code_bytes,
            lang=lang,
            classes=classes,
            functions=functions,
            imports=imports,
            calls=calls,
            current_class=None,
            current_function=None,
        )

        # Parse patch and map changed regions to enclosing classes and functions
        changed_regions = self._parse_patch_regions(patch, classes, functions) if patch else []

        summary = (
            f"{lang.title()}: {len(classes)} classes, {len(functions)} functions, "
            f"{len(imports)} imports, {len(calls)} calls, {len(changed_regions)} changed regions"
        )

        return FileAnalysisResult(
            file_path=file_path,
            language=lang,
            is_supported=True,
            parse_success=True,
            classes=classes,
            functions=functions,
            imports=imports,
            calls=calls,
            changed_regions=changed_regions,
            summary=summary,
        )

    def analyze_diff(self, file_path: str, patch: str) -> FileAnalysisResult:
        """Parses the patch diff content, including surrounding context lines, when full source is unavailable."""
        code_lines = []
        for line in patch.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                code_lines.append(line[1:])
            elif line.startswith(" "):
                code_lines.append(line[1:])
        pseudo_content = "\n".join(code_lines)

        return self.analyze_file(file_path=file_path, content=pseudo_content, patch=patch)

    def _traverse_node(
        self,
        node: Node,
        code_bytes: bytes,
        lang: str,
        classes: List[ClassDefinition],
        functions: List[FunctionDefinition],
        imports: List[ImportStatement],
        calls: List[FunctionCall],
        current_class: Optional[str],
        current_function: Optional[str],
    ) -> None:
        """Recursive node visitor extracting constructs according to language grammar."""
        ntype = node.type
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        # 1. Imports
        if ntype in ("import_statement", "import_from_statement", "import_declaration", "preproc_include"):
            raw_text = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()
            imports.append(
                ImportStatement(
                    source=raw_text,
                    line_number=start_line,
                )
            )

        # 2. Classes
        elif ntype in ("class_definition", "class_declaration", "class_specifier", "struct_specifier"):
            class_name = self._find_child_identifier(node, code_bytes) or "AnonymousClass"
            cls_def = ClassDefinition(
                name=class_name,
                start_line=start_line,
                end_line=end_line,
            )
            classes.append(cls_def)

            for child in node.children:
                self._traverse_node(
                    node=child,
                    code_bytes=code_bytes,
                    lang=lang,
                    classes=classes,
                    functions=functions,
                    imports=imports,
                    calls=calls,
                    current_class=class_name,
                    current_function=current_function,
                )
            return

        # 3. Functions / Methods
        elif ntype in (
            "function_definition",
            "function_declaration",
            "method_definition",
            "method_declaration",
            "constructor_declaration",
        ):
            func_name = self._find_child_identifier(node, code_bytes) or "anonymous_function"
            is_async = any(c.type == "async" for c in node.children)
            fn_def = FunctionDefinition(
                name=func_name,
                class_name=current_class,
                start_line=start_line,
                end_line=end_line,
                is_async=is_async,
            )
            functions.append(fn_def)
            if current_class:
                cls_match = next((c for c in classes if c.name == current_class), None)
                if cls_match:
                    cls_match.methods.append(func_name)

            for child in node.children:
                self._traverse_node(
                    node=child,
                    code_bytes=code_bytes,
                    lang=lang,
                    classes=classes,
                    functions=functions,
                    imports=imports,
                    calls=calls,
                    current_class=current_class,
                    current_function=func_name,
                )
            return

        # 4. Function / Method Calls
        elif ntype in ("call", "call_expression", "method_invocation"):
            target_name = self._extract_call_target(node, code_bytes)
            if target_name:
                calls.append(
                    FunctionCall(
                        target=target_name,
                        enclosing_function=current_function,
                        enclosing_class=current_class,
                        line_number=start_line,
                    )
                )

        # Continue traversing children
        for child in node.children:
            self._traverse_node(
                node=child,
                code_bytes=code_bytes,
                lang=lang,
                classes=classes,
                functions=functions,
                imports=imports,
                calls=calls,
                current_class=current_class,
                current_function=current_function,
            )

    def _find_child_identifier(self, node: Node, code_bytes: bytes) -> Optional[str]:
        """Finds immediate identifier child for function or class name."""
        for child in node.children:
            if child.type in ("identifier", "name", "type_identifier", "property_identifier"):
                return code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        return None

    def _extract_call_target(self, node: Node, code_bytes: bytes) -> Optional[str]:
        """Extracts invoked expression, e.g. 'db.query' or 'execute'."""
        if not node.children:
            return None
        target_node = node.children[0]
        return code_bytes[target_node.start_byte:target_node.end_byte].decode("utf-8", errors="replace").strip()

    def _parse_patch_regions(
        self,
        patch: str,
        classes: Optional[List[ClassDefinition]] = None,
        functions: Optional[List[FunctionDefinition]] = None,
    ) -> List[ChangedRegion]:
        """Parses unified diff hunks into ChangedRegion models with line numbers."""
        regions: List[ChangedRegion] = []
        if not patch:
            return regions

        hunk_regex = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
        current_region: Optional[ChangedRegion] = None
        new_line_cursor = 0

        for line in patch.splitlines():
            match = hunk_regex.match(line)
            if match:
                if current_region:
                    regions.append(current_region)
                start_line = int(match.group(2))
                count = int(match.group(3)) if match.group(3) else 1
                new_line_cursor = start_line
                current_region = ChangedRegion(
                    start_line=start_line,
                    end_line=start_line + max(count - 1, 0),
                    added_lines=[],
                    deleted_lines=[],
                )
                continue

            if current_region:
                if line.startswith("+") and not line.startswith("+++"):
                    current_region.added_lines.append(line[1:])
                    new_line_cursor += 1
                elif line.startswith("-") and not line.startswith("---"):
                    current_region.deleted_lines.append(line[1:])
                elif line.startswith(" "):
                    new_line_cursor += 1

        if current_region:
            regions.append(current_region)

        # Match enclosing scopes
        if classes or functions:
            for region in regions:
                mid_line = (region.start_line + region.end_line) // 2
                if functions:
                    enclosing_fn = next((f for f in functions if f.start_line <= mid_line <= f.end_line), None)
                    if enclosing_fn:
                        region.enclosing_function = enclosing_fn.name
                        region.enclosing_class = enclosing_fn.class_name
                if not region.enclosing_class and classes:
                    enclosing_cls = next((c for c in classes if c.start_line <= mid_line <= c.end_line), None)
                    if enclosing_cls:
                        region.enclosing_class = enclosing_cls.name

        return regions


_code_analysis_service: Optional[CodeAnalysisService] = None


def get_code_analysis_service() -> CodeAnalysisService:
    """Returns singleton instance of CodeAnalysisService."""
    global _code_analysis_service
    if _code_analysis_service is None:
        _code_analysis_service = CodeAnalysisService()
    return _code_analysis_service
