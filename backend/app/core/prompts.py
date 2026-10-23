from typing import Any, Dict, List, Optional

SYSTEM_CODE_REVIEW_PROMPT = """You are Engineering Memory, an expert architectural reviewer and code intelligence engine.

Your task is to review incoming pull request code changes against the engineering team's historical memory.
The historical memory contains past PR architectural decisions, previously rejected patterns, and approved exceptions.

Analyze the code diff and AST structural context carefully:
1. Identify any "Architecture Drift" where incoming code reintroduces a previously rejected pattern (e.g., controllers directly querying database/persistence).
2. Recognize and respect any "Approved Exceptions" if historical memory specifically allowed an exception for this file/context (e.g., HealthCheckController or diagnostics endpoints permitted for direct liveness queries).
3. If an approved exception applies, classify the category as "approved_exception", set severity to "info", mark exception_approved=true, and mark drift_detected=false.
4. When a finding conflicts with historical memory, include the exact historical rule, rationale, and previous PR references in `historical_evidence`. Only reference PR numbers that are verbatim in the recalled memory — never invent one.
5. Output your analysis strictly as a valid JSON object matching the requested schema. Do not include markdown code block backticks (like ```json) or preamble.
"""

REVIEW_OUTPUT_SCHEMA_INSTRUCTION = """
Your output MUST be a valid JSON object with the following fields:
{
  "summary": "High-level summary of findings",
  "drift_detected": true/false,
  "exception_approved": true/false,
  "confidence_score": 0.0 to 1.0,
  "findings": [
    {
      "category": "architecture_drift" | "rejected_pattern" | "approved_exception" | "convention_violation" | "note",
      "severity": "info" | "warning" | "error",
      "file_path": "path/to/file",
      "line_number": 123 (or null),
      "title": "Short finding title",
      "message": "Detailed explanation of why this violates or adheres to past decisions",
      "historical_pr_ref": "PR #<N>" where N is a PR number explicitly present in the recalled memory above, or null if none,
      "suggested_fix": "How to resolve the issue" (or null),
      "historical_evidence": {
        "claim": "The engineering rule or decision recalled from organizational memory",
        "rationale": "Why this rule was established, as documented in the recalled memory",
        "evidence_prs": ["PR #<N>"] where each PR number comes ONLY from the recalled memory above — never invent one,
        "matched_code": "path/to/file:line where the violation was found",
        "reasoning": "Why the current diff conflicts with or matches the recalled memory",
        "exception_notes": "Why an exception applies or does not apply, based on recalled memory"
      }
    }
  ],
  "durable_memory_candidates": [
    {
      "claim": "Clear architectural claim or guideline (NOT 'PR was approved')",
      "type": "architecture_decision" | "rejected_pattern" | "approved_exception" | "review_decision" | "lesson_learned",
      "rationale": "Why this decision or guideline was established, based on PR and code changes",
      "scope": "Component, module, or layer scope",
      "confidence": "supported"
    }
  ]
}
IMPORTANT PROVENANCE RULE: You MUST NOT fabricate PR numbers. Every PR number you include in
"historical_pr_ref" or "evidence_prs" MUST appear verbatim in the recalled organizational memory
provided above. If no PR number appears in the memory, set historical_pr_ref to null and
evidence_prs to [].
If the PR contains no durable engineering knowledge (e.g. routine dependency bump, minor bug fix, typo), leave "durable_memory_candidates": [].
Never create memories that merely state "PR #XYZ was approved" or "review passed".
"""


def build_code_review_user_prompt(
    pr_title: str,
    file_path: str,
    diff_patch: str,
    ast_summary: str,
    historical_memory_context: str,
    deep_pr_context: Optional[str] = None,
) -> str:
    """Builds user prompt for full code review reasoning with historical and structural context."""
    deep_context_section = ""
    if deep_pr_context:
        deep_context_section = f"\n## DEEP PR CONTEXT & STRUCTURAL DEPENDENCIES\n{deep_pr_context}\n"

    return f"""## PULL REQUEST CONTEXT
Title: {pr_title}
File: {file_path}
{deep_context_section}
## AST STRUCTURAL SUMMARY (TREE-SITTER)
{ast_summary}

## RECALLED ORGANIZATIONAL MEMORY (HINDSIGHT)
{historical_memory_context or "No prior historical decisions or rejected patterns recorded for this context."}

## CODE DIFF PATCH
{diff_patch}

## INSTRUCTIONS
Evaluate if this diff violates any recorded architectural decisions, reintroduces a rejected pattern, or matches an approved exception.
If an approved exception is present in the memory for this class/context, honor it (drift_detected=false, exception_approved=true).
{REVIEW_OUTPUT_SCHEMA_INSTRUCTION}
"""


SYSTEM_LIGHTWEIGHT_PROMPT = """You are a fast engineering assistant. Provide concise, direct outputs without filler."""


def build_quick_summary_prompt(content: str) -> str:
    """Builds prompt for fast summarization using lightweight model."""
    return f"""Summarize the following code change or review note in 2 concise sentences:

{content}
"""
