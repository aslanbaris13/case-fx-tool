"""Shared test setup.

The cache is process-global, so it is cleared before and after every test to
keep them independent. `client` is a FastAPI TestClient that drives the app
in-process; respx (used in the tests) intercepts the app's httpx calls to the
upstream, so no test ever touches the real network.
"""

import pytest
from fastapi.testclient import TestClient

from app import cache
from app.main import app


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def client():
    return TestClient(app)
