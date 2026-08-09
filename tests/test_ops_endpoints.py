import app


def test_cron_hourly_requires_secret_configured(client, monkeypatch):
    monkeypatch.setattr(app, "CRON_SECRET", "")
    resp = client.get("/api/cron/hourly")
    assert resp.status_code == 503


def test_cron_hourly_rejects_wrong_secret(client, monkeypatch):
    monkeypatch.setattr(app, "CRON_SECRET", "correct-secret")
    resp = client.get("/api/cron/hourly", headers={"X-Cron-Secret": "wrong"})
    assert resp.status_code == 401


def test_cron_hourly_accepts_correct_secret_header(client, monkeypatch):
    monkeypatch.setattr(app, "CRON_SECRET", "correct-secret")
    monkeypatch.setattr(app, "run_scheduled_tasks", lambda: None)
    resp = client.get("/api/cron/hourly", headers={"X-Cron-Secret": "correct-secret"})
    assert resp.status_code == 200


def test_cron_hourly_accepts_secret_query_param(client, monkeypatch):
    monkeypatch.setattr(app, "CRON_SECRET", "correct-secret")
    monkeypatch.setattr(app, "run_scheduled_tasks", lambda: None)
    resp = client.get("/api/cron/hourly", params={"secret": "correct-secret"})
    assert resp.status_code == 200


def test_ops_status_requires_secret(client, monkeypatch):
    monkeypatch.setattr(app, "CRON_SECRET", "correct-secret")
    resp = client.get("/api/ops/status")
    assert resp.status_code == 401


def test_ops_status_returns_summary(client, monkeypatch):
    monkeypatch.setattr(app, "CRON_SECRET", "correct-secret")
    monkeypatch.setattr(app.database, "get_latest_digest", lambda db_path: {"date": "2026-08-08"})
    monkeypatch.setattr(app.database, "get_all_digest_dates", lambda db_path: ["2026-08-08", "2026-08-07"])
    monkeypatch.setattr(app.database, "get_active_subscribers", lambda db_path: [])
    monkeypatch.setattr(app.database, "get_sources", lambda db_path: [1, 2, 3])
    resp = client.get("/api/ops/status", headers={"X-Ops-Secret": "correct-secret"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["latest_digest_date"] == "2026-08-08"
    assert data["digest_count"] == 2
    assert data["source_count"] == 3


def test_ops_status_reports_admin_alert_email(client, monkeypatch):
    monkeypatch.setattr(app, "CRON_SECRET", "correct-secret")
    monkeypatch.setattr(app, "ADMIN_ALERT_EMAIL", "admin@example.com")
    monkeypatch.setattr(app.database, "get_latest_digest", lambda db_path: None)
    monkeypatch.setattr(app.database, "get_all_digest_dates", lambda db_path: [])
    monkeypatch.setattr(app.database, "get_active_subscribers", lambda db_path: [])
    monkeypatch.setattr(app.database, "get_sources", lambda db_path: [])
    resp = client.get("/api/ops/status", headers={"X-Ops-Secret": "correct-secret"})
    assert resp.json()["admin_alert_email"] == "admin@example.com"


def test_ops_sync_requires_secret(client, monkeypatch):
    monkeypatch.setattr(app, "CRON_SECRET", "correct-secret")
    resp = client.post("/api/ops/sync")
    assert resp.status_code == 401


def test_ops_sync_rejects_when_already_running(client, monkeypatch):
    monkeypatch.setattr(app, "CRON_SECRET", "correct-secret")
    app.SYNC_STATUS["status"] = "fetching"
    try:
        resp = client.post("/api/ops/sync", headers={"X-Ops-Secret": "correct-secret"})
        assert resp.status_code == 400
    finally:
        app.SYNC_STATUS["status"] = "idle"


def test_ops_sync_triggers_background_task(client, monkeypatch):
    monkeypatch.setattr(app, "CRON_SECRET", "correct-secret")
    called = []
    monkeypatch.setattr(app, "run_sync_job", lambda date: called.append(date))
    resp = client.post("/api/ops/sync", headers={"X-Ops-Secret": "correct-secret"}, params={"date": "2026-01-01"})
    assert resp.status_code == 200
    assert resp.json()["date"] == "2026-01-01"
    assert called == ["2026-01-01"]


def test_ops_sync_defaults_to_today_when_no_date_given(client, monkeypatch):
    monkeypatch.setattr(app, "CRON_SECRET", "correct-secret")
    monkeypatch.setattr(app, "run_sync_job", lambda date: None)
    resp = client.post("/api/ops/sync", headers={"X-Ops-Secret": "correct-secret"})
    assert resp.status_code == 200
    assert resp.json()["date"]  # non-empty
