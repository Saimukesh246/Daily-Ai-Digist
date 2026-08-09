import time

import app


def test_unsubscribe_token_is_deterministic_and_email_specific():
    t1 = app._unsubscribe_token("person@example.com")
    t2 = app._unsubscribe_token("PERSON@example.com ")  # case/whitespace-insensitive
    t3 = app._unsubscribe_token("someone-else@example.com")
    assert t1 == t2
    assert t1 != t3


def test_unsubscribe_valid_token_deactivates_subscriber(client, monkeypatch):
    calls = []
    monkeypatch.setattr(app.database, "deactivate_subscriber", lambda db_path, email: calls.append(email) or True)
    token = app._unsubscribe_token("person@example.com")
    resp = client.get("/api/public/unsubscribe", params={"email": "person@example.com", "token": token})
    assert resp.status_code == 200
    assert "unsubscribed" in resp.text
    assert calls == ["person@example.com"]


def test_unsubscribe_wrong_token_does_not_deactivate(client, monkeypatch):
    calls = []
    monkeypatch.setattr(app.database, "deactivate_subscriber", lambda db_path, email: calls.append(email) or True)
    resp = client.get("/api/public/unsubscribe", params={"email": "person@example.com", "token": "wrong"})
    assert resp.status_code == 200
    assert "invalid" in resp.text.lower()
    assert calls == []


def test_unsubscribe_token_cannot_be_reused_for_another_email(client, monkeypatch):
    monkeypatch.setattr(app.database, "deactivate_subscriber", lambda db_path, email: True)
    token_for_a = app._unsubscribe_token("a@example.com")
    resp = client.get("/api/public/unsubscribe", params={"email": "b@example.com", "token": token_for_a})
    assert "invalid" in resp.text.lower()


def test_subscribe_rejects_invalid_email(client):
    resp = client.post("/api/public/subscribe", json={"email": "not-an-email"})
    assert resp.status_code == 400


def test_subscribe_rejects_duplicate(client, monkeypatch):
    monkeypatch.setattr(app.database, "add_subscriber", lambda db_path, email, name: False)
    resp = client.post("/api/public/subscribe", json={"email": "person@example.com"})
    assert resp.status_code == 409


def test_subscribe_success_sends_confirmation_in_background(client, monkeypatch):
    monkeypatch.setattr(app.database, "add_subscriber", lambda db_path, email, name: True)
    monkeypatch.setattr(app.database, "get_setting", lambda db_path, key, default="": default)
    sent = []
    monkeypatch.setattr(app.emailer, "send_subscribe_confirmation_email", lambda *a, **kw: sent.append(a))
    resp = client.post("/api/public/subscribe", json={"email": "person@example.com", "name": "Person"})
    assert resp.status_code == 200
    time.sleep(0.2)  # confirmation email is fired via a real OS thread, not an ASGI background task
    assert sent
