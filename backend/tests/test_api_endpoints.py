from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_recent_reviews_endpoint():
    response = client.get("/api/v1/reviews/recent")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_memory_recall_endpoint():
    response = client.get("/api/v1/memory/recall?query=controller")
    assert response.status_code == 200
    data = response.json()
    assert "bank_id" in data
    assert "results_count" in data
    assert "items" in data
    assert isinstance(data["items"], list)


def test_memory_retain_endpoint():
    response = client.post(
        "/api/v1/memory/retain",
        json={
            "content": "Controllers must never invoke SQL queries directly.",
            "tags": ["architectural_decision"],
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert "bank_id" in data


def test_settings_status_endpoint():
    response = client.get("/api/v1/settings/status")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert "environment" in data
    assert "services" in data
    services = data["services"]
    assert "github" in services
    assert "supabase" in services
    assert "hindsight" in services
    assert "groq" in services
    assert "tree_sitter" in services
    assert services["tree_sitter"]["active"] is True
