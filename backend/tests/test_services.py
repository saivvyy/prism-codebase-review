from unittest.mock import MagicMock, patch
import pytest

from app.core.exceptions import ConfigurationError, HindsightMemoryError, SupabaseStateError
from app.services.supabase import SupabaseService
from app.services.memory import HindsightMemoryService


def test_supabase_service_unconfigured():
    service = SupabaseService(supabase_url=None, supabase_key=None, persist_local=False)
    assert not service.is_configured
    with pytest.raises(ConfigurationError):
        _ = service.client


def test_supabase_service_configured():
    with patch("app.services.supabase.create_client") as mock_create:
        mock_client = MagicMock()
        mock_create.return_value = mock_client

        service = SupabaseService(
            supabase_url="https://test.supabase.co",
            supabase_key="test-key",
            persist_local=False,
        )
        assert service.is_configured
        client = service.client
        assert client == mock_client
        mock_create.assert_called_once_with("https://test.supabase.co", "test-key")


def test_hindsight_service_unconfigured():
    service = HindsightMemoryService(base_url="https://api.hindsight.vectorize.io", api_key=None)
    assert not service.is_configured
    with pytest.raises(ConfigurationError):
        _ = service.client


def test_hindsight_retain_wrapper():
    service = HindsightMemoryService(
        base_url="https://api.hindsight.vectorize.io",
        api_key="mock-key",
        default_bank_id="test_bank",
    )

    mock_client = MagicMock()
    mock_retain_res = MagicMock()
    mock_retain_res.success = True
    mock_retain_res.items_count = 1
    mock_retain_res.operation_id = "op_123"
    mock_client.retain.return_value = mock_retain_res
    service._client = mock_client

    result = service.retain(
        content="Controllers must not query database directly.",
        context="PR #142 rejection",
        metadata={"rule": "architecture_drift"},
        tags=["controller", "persistence"],
    )

    assert result.success is True
    assert result.bank_id == "test_bank"
    assert result.items_count == 1
    assert result.operation_id == "op_123"
    mock_client.retain.assert_called_once_with(
        bank_id="test_bank",
        content="Controllers must not query database directly.",
        context="PR #142 rejection",
        document_id=None,
        metadata={"rule": "architecture_drift"},
        tags=["controller", "persistence"],
    )


def test_hindsight_recall_wrapper():
    service = HindsightMemoryService(
        base_url="https://api.hindsight.vectorize.io",
        api_key="mock-key",
        default_bank_id="test_bank",
    )

    mock_client = MagicMock()
    mock_recall_res = MagicMock()
    mock_item = MagicMock()
    mock_item.id = "mem_1"
    mock_item.text = "Rejected pattern: Controller accessing repository directly."
    mock_item.score = 0.92
    mock_item.type = "observation"
    mock_item.metadata = {"pr": "142"}
    mock_item.tags = ["drift"]

    mock_recall_res.results = [mock_item]
    mock_recall_res.to_prompt_string.return_value = "Historical Context: Controller direct DB was rejected."
    mock_client.recall.return_value = mock_recall_res
    service._client = mock_client

    result = service.recall(
        query="UserController calls db.query",
        tags=["drift"],
    )

    assert result.bank_id == "test_bank"
    assert result.results_count == 1
    assert result.items[0].text == "Rejected pattern: Controller accessing repository directly."
    assert result.prompt_context == "Historical Context: Controller direct DB was rejected."
    mock_client.recall.assert_called_once_with(
        bank_id="test_bank",
        query="UserController calls db.query",
        max_tokens=4096,
        tags=["drift"],
    )


def test_hindsight_reflect_wrapper():
    service = HindsightMemoryService(
        base_url="https://api.hindsight.vectorize.io",
        api_key="mock-key",
        default_bank_id="test_bank",
    )

    mock_client = MagicMock()
    mock_reflect_res = MagicMock()
    mock_reflect_res.text = "Controllers are strictly required to invoke application service layer."
    mock_reflect_res.based_on = ["mem_1", "mem_2"]
    mock_client.reflect.return_value = mock_reflect_res
    service._client = mock_client

    result = service.reflect(
        query="What is the established persistence access policy?",
        budget="mid",
    )

    assert result.bank_id == "test_bank"
    assert "application service layer" in result.answer
    assert result.based_on == ["mem_1", "mem_2"]
    mock_client.reflect.assert_called_once_with(
        bank_id="test_bank",
        query="What is the established persistence access policy?",
        context=None,
        budget="mid",
        tags=None,
    )


def test_hindsight_error_handling():
    service = HindsightMemoryService(
        base_url="https://api.hindsight.vectorize.io",
        api_key="mock-key",
    )
    mock_client = MagicMock()
    mock_client.recall.side_effect = RuntimeError("Hindsight API unreachable")
    service._client = mock_client

    with pytest.raises(HindsightMemoryError) as exc_info:
        service.recall(query="test query")
    assert "Hindsight API unreachable" in str(exc_info.value)
