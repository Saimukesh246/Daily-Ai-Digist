import app


def test_get_reading_list_rejects_overlong_code(client):
    resp = client.get("/api/public/reading-list/" + "x" * 100)
    assert resp.status_code == 400


def test_get_reading_list_returns_bookmarks(client, monkeypatch):
    monkeypatch.setattr(app.database, "get_synced_bookmarks", lambda db_path, code: [{"url": "https://x"}])
    resp = client.get("/api/public/reading-list/abc123")
    assert resp.status_code == 200
    assert resp.json()["bookmarks"] == [{"url": "https://x"}]


def test_put_reading_list_replaces_bookmarks(client, monkeypatch):
    captured = {}

    def fake_replace(db_path, code, items):
        captured["code"] = code
        captured["items"] = items

    monkeypatch.setattr(app.database, "replace_synced_bookmarks", fake_replace)
    resp = client.put("/api/public/reading-list/abc123", json={"bookmarks": [{"url": "https://x", "title": "X"}]})
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    assert captured["code"] == "abc123"
    assert captured["items"] == [{"url": "https://x", "title": "X"}]


def test_put_reading_list_rejects_overlong_code(client):
    resp = client.put("/api/public/reading-list/" + "x" * 100, json={"bookmarks": []})
    assert resp.status_code == 400


def test_put_reading_list_empty_list_clears(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(app.database, "replace_synced_bookmarks", lambda db_path, code, items: captured.update(items=items))
    resp = client.put("/api/public/reading-list/abc123", json={"bookmarks": []})
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    assert captured["items"] == []
