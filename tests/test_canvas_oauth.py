import json
import os
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from canvas_oauth import CanvasOAuthClient


def _configure_canvas_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CANVAS_BASE_URL", "https://school.instructure.com/")
    monkeypatch.setenv("CANVAS_CLIENT_ID", "client-id")
    monkeypatch.setenv("CANVAS_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("CANVAS_REDIRECT_URI", "http://localhost:8080/canvas/oauth/callback")
    monkeypatch.setenv("CANVAS_TOKEN_STORE", str(tmp_path / "canvas_token.json"))


def test_authorization_url_uses_canvas_oauth_endpoint(monkeypatch, tmp_path):
    _configure_canvas_env(monkeypatch, tmp_path)

    client = CanvasOAuthClient.from_env()
    url = client.authorization_url("state-123")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "school.instructure.com"
    assert parsed.path == "/login/oauth2/auth"
    assert params["client_id"] == ["client-id"]
    assert params["response_type"] == ["code"]
    assert params["state"] == ["state-123"]
    assert params["redirect_uri"] == ["http://localhost:8080/canvas/oauth/callback"]


def test_exchange_code_saves_token_without_client_secret(monkeypatch, tmp_path):
    _configure_canvas_env(monkeypatch, tmp_path)

    class FakeResponse:
        ok = True
        status_code = 200
        text = ""

        def json(self):
            return {
                "access_token": "access-123",
                "refresh_token": "refresh-123",
                "expires_in": 3600,
                "token_type": "Bearer",
            }

    posted = {}

    def fake_post(url, data, timeout):
        posted["url"] = url
        posted["data"] = data
        posted["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("canvas_oauth.requests.post", fake_post)

    client = CanvasOAuthClient.from_env()
    token = client.exchange_code("code-123")

    assert posted["url"] == "https://school.instructure.com/login/oauth2/token"
    assert posted["data"]["grant_type"] == "authorization_code"
    assert posted["data"]["code"] == "code-123"
    assert token["access_token"] == "access-123"

    saved = json.loads((tmp_path / "canvas_token.json").read_text())
    assert saved["refresh_token"] == "refresh-123"
    assert "client-secret" not in json.dumps(saved)


def test_refresh_preserves_existing_refresh_token(monkeypatch, tmp_path):
    _configure_canvas_env(monkeypatch, tmp_path)

    class FakeResponse:
        ok = True
        status_code = 200
        text = ""

        def json(self):
            return {
                "access_token": "new-access",
                "expires_in": 3600,
                "token_type": "Bearer",
            }

    monkeypatch.setattr("canvas_oauth.requests.post", lambda url, data, timeout: FakeResponse())

    client = CanvasOAuthClient.from_env()
    refreshed = client.refresh_access_token("old-refresh")

    assert refreshed["access_token"] == "new-access"
    assert refreshed["refresh_token"] == "old-refresh"
