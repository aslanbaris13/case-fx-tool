"""Smoke test: the app imports cleanly and the health endpoint responds.

This exists so test.sh is green from the first commit; the real conversion
tests arrive with the conversion logic.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
