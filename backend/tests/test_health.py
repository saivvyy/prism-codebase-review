from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_root_health():
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"] == "0.1.0"
    assert payload["environment"] == "development"
    assert "timestamp" in payload
    print("test_root_health passed:", payload)


def test_v1_health():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    print("test_v1_health passed:", payload)


if __name__ == "__main__":
    test_root_health()
    test_v1_health()
    print("All backend checks passed.")
