"""
Shared pytest fixtures.

`app.py` calls `database.init_db()` — a real Postgres connection — at module
import time, and its `lifespan` spawns a background scheduler thread on
startup. Neither is appropriate in a test run without a live database, so
both are neutralized here before `app` is ever imported. Individual tests
then monkeypatch whichever `database.*` functions their endpoint touches;
nothing in this test suite hits a real database.
"""

import database
database.init_db = lambda *a, **kw: None

import app as app_module
app_module.start_hourly_scheduler = lambda *a, **kw: None

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    return TestClient(app_module.app)
