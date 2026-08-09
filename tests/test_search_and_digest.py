import app


def test_public_digest_404_when_none(client, monkeypatch):
    monkeypatch.setattr(app.database, "get_latest_digest", lambda db_path: None)
    resp = client.get("/api/public/digest")
    assert resp.status_code == 404


def test_public_digest_returns_latest(client, monkeypatch):
    monkeypatch.setattr(app.database, "get_latest_digest", lambda db_path: {"date": "2026-08-08", "content": {}})
    resp = client.get("/api/public/digest")
    assert resp.status_code == 200
    assert resp.json()["date"] == "2026-08-08"


def test_public_article_unknown_section_is_400(client):
    resp = client.get("/api/public/article", params={"date": "2026-08-08", "section": "not_real", "index": 0})
    assert resp.status_code == 400


def test_public_article_missing_digest_is_404(client, monkeypatch):
    monkeypatch.setattr(app.database, "get_digest", lambda db_path, date: None)
    resp = client.get("/api/public/article", params={"date": "2026-08-08", "section": "biggest_news", "index": 0})
    assert resp.status_code == 404


def test_public_article_index_out_of_range_is_404(client, monkeypatch):
    monkeypatch.setattr(app.database, "get_digest", lambda db_path, date: {"content": {"biggest_news": []}})
    resp = client.get("/api/public/article", params={"date": "2026-08-08", "section": "biggest_news", "index": 0})
    assert resp.status_code == 404


def test_public_article_returns_item_and_related(client, monkeypatch):
    monkeypatch.setattr(app.database, "get_digest", lambda db_path, date: {
        "content": {
            "biggest_news": [{"headline": "A", "link": "https://example.com/a"}],
            "open_source_research": [{"title": "R", "link": "https://example.com/r"}],
        }
    })
    resp = client.get("/api/public/article", params={"date": "2026-08-08", "section": "biggest_news", "index": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["item"]["headline"] == "A"
    assert len(data["related"]) == 1
    assert data["related"][0]["title"] == "R"


def test_search_returns_results_and_sources(client, monkeypatch):
    monkeypatch.setattr(app.database, "search_articles", lambda db_path, q, source, limit: [{"title": "Match"}])
    monkeypatch.setattr(app.database, "get_distinct_sources", lambda db_path: ["Hacker News"])
    resp = client.get("/api/public/search", params={"q": "match"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["title"] == "Match"
    assert data["sources"] == ["Hacker News"]


def test_search_limit_is_capped(client, monkeypatch):
    captured = {}

    def fake_search(db_path, q, source, limit):
        captured["limit"] = limit
        return []

    monkeypatch.setattr(app.database, "search_articles", fake_search)
    monkeypatch.setattr(app.database, "get_distinct_sources", lambda db_path: [])
    client.get("/api/public/search", params={"q": "x", "limit": 9999})
    assert captured["limit"] == 100


def test_search_empty_query_still_returns_shape(client, monkeypatch):
    monkeypatch.setattr(app.database, "search_articles", lambda db_path, q, source, limit: [])
    monkeypatch.setattr(app.database, "get_distinct_sources", lambda db_path: [])
    resp = client.get("/api/public/search")
    assert resp.status_code == 200
    assert resp.json() == {"results": [], "sources": [], "total": 0}
