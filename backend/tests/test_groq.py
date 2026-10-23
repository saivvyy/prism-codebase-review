from unittest.mock import MagicMock, patch
import pytest
from groq import AuthenticationError, RateLimitError

from app.core.exceptions import (
    ConfigurationError,
    GroqAuthError,
    GroqRateLimitError,
    GroqResponseError,
)
from app.services.groq import GroqService


@pytest.fixture
def groq_service():
    return GroqService(
        api_key="mock-groq-key",
        reasoning_model="openai/gpt-oss-120b",
        lightweight_model="openai/gpt-oss-20b",
    )


def test_groq_unconfigured():
    service = GroqService(api_key=None)
    assert not service.is_configured
    with pytest.raises(ConfigurationError):
        _ = service.client


def test_chat_completion_success(groq_service):
    mock_client = MagicMock()
    mock_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Analysis completed without issues."
    mock_completion.choices = [mock_choice]
    mock_completion.usage.prompt_tokens = 50
    mock_completion.usage.completion_tokens = 20
    mock_completion.usage.total_tokens = 70

    mock_client.chat.completions.create.return_value = mock_completion
    groq_service._client = mock_client

    messages = [{"role": "user", "content": "Review this code"}]
    res = groq_service.chat_completion(messages=messages)

    assert res.content == "Analysis completed without issues."
    assert res.model == "openai/gpt-oss-120b"
    assert res.usage["total_tokens"] == 70
    mock_client.chat.completions.create.assert_called_once_with(
        messages=messages,
        model="openai/gpt-oss-120b",
        temperature=0.2,
        max_tokens=4096,
    )


def test_chat_completion_lightweight(groq_service):
    mock_client = MagicMock()
    mock_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Quick summary"
    mock_completion.choices = [mock_choice]
    mock_completion.usage = None

    mock_client.chat.completions.create.return_value = mock_completion
    groq_service._client = mock_client

    res = groq_service.chat_completion(
        messages=[{"role": "user", "content": "fast task"}],
        use_lightweight=True,
    )

    assert res.content == "Quick summary"
    assert res.model == "openai/gpt-oss-20b"


def test_generate_review_analysis_valid_json(groq_service):
    mock_client = MagicMock()
    mock_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = """
    {
        "summary": "Architecture drift detected in UserController.",
        "drift_detected": true,
        "exception_approved": false,
        "confidence_score": 0.95,
        "findings": [
            {
                "category": "architecture_drift",
                "severity": "error",
                "file_path": "app/controllers/user.py",
                "line_number": 42,
                "title": "Controller directly accessing database",
                "message": "This pattern was previously rejected in PR #142 because persistence queries belong in repository layer.",
                "historical_pr_ref": "PR #142",
                "suggested_fix": "Inject UserRepository and call repository method instead."
            }
        ]
    }
    """
    mock_completion.choices = [mock_choice]
    mock_completion.usage = None

    mock_client.chat.completions.create.return_value = mock_completion
    groq_service._client = mock_client

    result = groq_service.generate_review_analysis(
        pr_title="Direct DB Access in Controller",
        file_path="app/controllers/user.py",
        diff_patch="+ db.query('SELECT * FROM users')",
        ast_summary="UserController, method get_users, call db.query",
        historical_memory_context="PR #142 rejected controller querying database directly.",
    )

    assert result.drift_detected is True
    assert result.exception_approved is False
    assert result.confidence_score == 0.95
    assert len(result.findings) == 1
    assert result.findings[0].category == "architecture_drift"
    assert result.findings[0].historical_pr_ref == "PR #142"
    assert result.findings[0].line_number == 42


def test_generate_review_analysis_markdown_codeblock(groq_service):
    mock_client = MagicMock()
    mock_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = """```json
    {
        "summary": "Clean code changes.",
        "drift_detected": false,
        "exception_approved": false,
        "confidence_score": 1.0,
        "findings": []
    }
    ```"""
    mock_completion.choices = [mock_choice]
    mock_completion.usage = None

    mock_client.chat.completions.create.return_value = mock_completion
    groq_service._client = mock_client

    result = groq_service.generate_review_analysis(
        pr_title="Add logger",
        file_path="app/logger.py",
        diff_patch="+ import logging",
        ast_summary="logger init",
        historical_memory_context="",
    )

    assert result.drift_detected is False
    assert len(result.findings) == 0
    assert result.summary == "Clean code changes."


def test_generate_review_analysis_malformed_json_fallback(groq_service):
    mock_client = MagicMock()
    mock_completion = MagicMock()
    mock_choice = MagicMock()
    # Malformed non-JSON output discussing drift
    mock_choice.message.content = "Warning: this change violates architecture rules and has architecture drift from PR #142."
    mock_completion.choices = [mock_choice]
    mock_completion.usage = None

    mock_client.chat.completions.create.return_value = mock_completion
    groq_service._client = mock_client

    result = groq_service.generate_review_analysis(
        pr_title="Test title",
        file_path="app/controllers/user.py",
        diff_patch="+ db.query()",
        ast_summary="call db.query",
        historical_memory_context="PR #142",
    )

    # Must recover gracefully without raising exception
    assert result.drift_detected is True
    assert len(result.findings) == 1
    assert result.findings[0].category == "architecture_drift"
    assert "partial structured parse recovery" in result.summary


def test_generate_quick_summary(groq_service):
    mock_client = MagicMock()
    mock_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Added 3 helper functions for auth token generation."
    mock_completion.choices = [mock_choice]
    mock_completion.usage = None

    mock_client.chat.completions.create.return_value = mock_completion
    groq_service._client = mock_client

    summary = groq_service.generate_quick_summary("Diff content here")
    assert "Added 3 helper functions" in summary


def test_groq_auth_error(groq_service):
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = AuthenticationError(
        message="Invalid API Key",
        response=MagicMock(status_code=401),
        body={},
    )
    groq_service._client = mock_client

    with pytest.raises(GroqAuthError):
        groq_service.chat_completion(messages=[{"role": "user", "content": "hi"}])


def test_groq_rate_limit_error(groq_service):
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RateLimitError(
        message="Rate limit reached",
        response=MagicMock(status_code=429),
        body={},
    )
    groq_service._client = mock_client

    with pytest.raises(GroqRateLimitError):
        groq_service.chat_completion(messages=[{"role": "user", "content": "hi"}])


def test_groq_empty_response_error(groq_service):
    mock_client = MagicMock()
    mock_completion = MagicMock()
    mock_completion.choices = []
    mock_client.chat.completions.create.return_value = mock_completion
    groq_service._client = mock_client

    with pytest.raises(GroqResponseError):
        groq_service.chat_completion(messages=[{"role": "user", "content": "hi"}])
