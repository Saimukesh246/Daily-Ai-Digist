import app


def test_homepage_falls_back_when_no_digest(client, monkeypatch):
    monkeypatch.setattr(app.database, "get_latest_digest", lambda db_path: None)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "AI Digest" in resp.text
    assert "{{OG_TITLE}}" not in resp.text


def test_homepage_uses_lead_story_for_og_tags(client, monkeypatch):
    monkeypatch.setattr(app.database, "get_latest_digest", lambda db_path: {
        "date": "2026-08-08",
        "content": {"biggest_news": [{"headline": "Big Thing Happens", "summary": "A summary.", "link": "https://example.com/a"}]},
    })
    monkeypatch.setattr(app, "_fetch_og_image", lambda url: {"image_url": "https://example.com/img.jpg", "title": ""})
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Big Thing Happens" in resp.text
    assert "https://example.com/img.jpg" in resp.text


def test_article_page_renders_og_tags_for_valid_params(client, monkeypatch):
    monkeypatch.setattr(app.database, "get_digest", lambda db_path, date: {
        "date": date,
        "content": {"biggest_news": [{"headline": "Headline A", "summary": "Dek A", "link": "https://example.com/x"}]},
    })
    monkeypatch.setattr(app, "_fetch_og_image", lambda url: {"image_url": "https://example.com/hero.jpg", "title": ""})
    resp = client.get("/article.html", params={"date": "2026-08-08", "section": "biggest_news", "index": 0})
    assert resp.status_code == 200
    assert "Headline A" in resp.text
    assert "https://example.com/hero.jpg" in resp.text


def test_article_page_falls_back_gracefully_on_missing_params(client):
    resp = client.get("/article.html")
    assert resp.status_code == 200
    assert "AI Digest" in resp.text


def test_article_page_falls_back_on_out_of_range_index(client, monkeypatch):
    monkeypatch.setattr(app.database, "get_digest", lambda db_path, date: {
        "date": date, "content": {"biggest_news": []},
    })
    resp = client.get("/article.html", params={"date": "2026-08-08", "section": "biggest_news", "index": 5})
    assert resp.status_code == 200
    assert "AI Digest" in resp.text


def test_sitemap_includes_article_urls(client, monkeypatch):
    monkeypatch.setattr(app.database, "get_latest_digest", lambda db_path: {
        "date": "2026-08-08",
        "content": {"biggest_news": [{"headline": "A", "link": "https://example.com/a"}]},
    })
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    assert "article.html?date=2026-08-08" in resp.text


def test_sitemap_still_valid_with_no_digest(client, monkeypatch):
    monkeypatch.setattr(app.database, "get_latest_digest", lambda db_path: None)
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert "<urlset" in resp.text


def test_robots_txt_points_at_sitemap(client):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert "Sitemap:" in resp.text
    assert "Disallow: /api/" in resp.text


def test_unmatched_route_returns_styled_404_html(client):
    resp = client.get("/this-does-not-exist")
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    assert "AI Digest" in resp.text


def test_unmatched_api_route_returns_json_404(client):
    resp = client.get("/api/this-does-not-exist")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"detail": "Not Found"}


def test_api_raised_404_is_still_json_not_html(client, monkeypatch):
    """An HTTPException(404, ...) raised inside an /api/ handler should stay
    JSON — only unmatched browser routes get the styled HTML page."""
    monkeypatch.setattr(app.database, "get_latest_digest", lambda db_path: None)
    resp = client.get("/api/public/digest")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
