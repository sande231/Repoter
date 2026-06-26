"""Canvas OAuth helper for the Canvas Tutor adapter.

Run this locally, open /canvas/oauth/start, approve the Canvas app once, and
the resulting token will be saved for the adapter to reuse and refresh.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from flask import Flask, jsonify, redirect, request, session


DEFAULT_REDIRECT_URI = "http://localhost:8080/canvas/oauth/callback"
DEFAULT_TOKEN_STORE = ".canvas_tokens/canvas_token.json"


class CanvasOAuthError(RuntimeError):
    """Base exception for Canvas OAuth failures."""


class CanvasOAuthConfigError(CanvasOAuthError):
    """Raised when the Canvas OAuth environment is incomplete."""


class CanvasTokenError(CanvasOAuthError):
    """Raised when a Canvas token is missing, expired, or rejected."""


def _clean_base_url(base_url: str | None) -> str:
    if not base_url:
        return ""
    return base_url.rstrip("/")


def _truthy(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CanvasOAuthConfig:
    base_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: str
    token_store: Path
    replace_tokens: bool

    @classmethod
    def from_env(cls, require_client_credentials: bool = True) -> "CanvasOAuthConfig":
        base_url = _clean_base_url(os.environ.get("CANVAS_BASE_URL"))
        client_id = os.environ.get("CANVAS_CLIENT_ID", "")
        client_secret = os.environ.get("CANVAS_CLIENT_SECRET", "")
        redirect_uri = os.environ.get("CANVAS_REDIRECT_URI", DEFAULT_REDIRECT_URI)
        scopes = os.environ.get("CANVAS_OAUTH_SCOPES", "")
        token_store = Path(os.environ.get("CANVAS_TOKEN_STORE", DEFAULT_TOKEN_STORE))
        replace_tokens = _truthy(os.environ.get("CANVAS_REPLACE_TOKENS"))

        missing = []
        if not base_url:
            missing.append("CANVAS_BASE_URL")
        if require_client_credentials and not client_id:
            missing.append("CANVAS_CLIENT_ID")
        if require_client_credentials and not client_secret:
            missing.append("CANVAS_CLIENT_SECRET")
        if missing:
            raise CanvasOAuthConfigError(f"Missing required Canvas OAuth env vars: {', '.join(missing)}")

        return cls(
            base_url=base_url,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scopes=scopes,
            token_store=token_store,
            replace_tokens=replace_tokens,
        )


class CanvasTokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def save_token_response(self, token_response: dict[str, Any], existing_refresh_token: str | None = None) -> dict[str, Any]:
        token = dict(token_response)
        if existing_refresh_token and not token.get("refresh_token"):
            token["refresh_token"] = existing_refresh_token
        if "access_token" not in token:
            raise CanvasTokenError("Canvas token response did not include access_token")
        if token.get("expires_in") is not None:
            token["expires_at"] = int(time.time()) + int(token["expires_in"])
        token["saved_at"] = int(time.time())

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(token, f, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(self.path)
        return token

    def delete(self) -> None:
        if self.path.exists():
            self.path.unlink()


class CanvasOAuthClient:
    def __init__(self, config: CanvasOAuthConfig) -> None:
        self.config = config
        self.store = CanvasTokenStore(config.token_store)

    @classmethod
    def from_env(cls, require_client_credentials: bool = True) -> "CanvasOAuthClient":
        return cls(CanvasOAuthConfig.from_env(require_client_credentials=require_client_credentials))

    def _require_client_credentials(self) -> None:
        missing = []
        if not self.config.client_id:
            missing.append("CANVAS_CLIENT_ID")
        if not self.config.client_secret:
            missing.append("CANVAS_CLIENT_SECRET")
        if missing:
            raise CanvasOAuthConfigError(f"Missing required Canvas OAuth env vars: {', '.join(missing)}")

    def authorization_url(self, state: str) -> str:
        if not self.config.client_id:
            raise CanvasOAuthConfigError("CANVAS_CLIENT_ID is required to build the Canvas authorization URL")
        params = {
            "client_id": self.config.client_id,
            "response_type": "code",
            "state": state,
            "redirect_uri": self.config.redirect_uri,
        }
        if self.config.scopes:
            params["scope"] = self.config.scopes
        return f"{self.config.base_url}/login/oauth2/auth?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        self._require_client_credentials()
        data = {
            "grant_type": "authorization_code",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "redirect_uri": self.config.redirect_uri,
            "code": code,
        }
        if self.config.replace_tokens:
            data["replace_tokens"] = "1"
        token = self._post_token(data)
        return self.store.save_token_response(token)

    def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        self._require_client_credentials()
        data = {
            "grant_type": "refresh_token",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "refresh_token": refresh_token,
        }
        token = self._post_token(data)
        return self.store.save_token_response(token, existing_refresh_token=refresh_token)

    def get_valid_access_token(self, min_ttl_seconds: int = 120) -> str:
        manual_token = os.environ.get("CANVAS_ACCESS_TOKEN")
        if manual_token:
            return manual_token

        token = self.store.load()
        if not token:
            raise CanvasTokenError(
                "No Canvas OAuth token found. Start the OAuth helper and open /canvas/oauth/start."
            )

        access_token = token.get("access_token")
        expires_at = int(token.get("expires_at") or 0)
        if access_token and (not expires_at or expires_at > int(time.time()) + min_ttl_seconds):
            return access_token

        refresh_token = token.get("refresh_token")
        if not refresh_token:
            raise CanvasTokenError("Stored Canvas token is expired and does not include a refresh_token")

        refreshed = self.refresh_access_token(refresh_token)
        return refreshed["access_token"]

    def api_get(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        access_token = self.get_valid_access_token()
        url = path if path.startswith("http://") or path.startswith("https://") else f"{self.config.base_url}/{path.lstrip('/')}"
        resp = requests.get(url, params=params or {}, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
        if resp.status_code == 401:
            raise CanvasTokenError("Canvas rejected the current token with HTTP 401; run OAuth again")
        resp.raise_for_status()
        return resp

    def token_status(self) -> dict[str, Any]:
        token = self.store.load()
        if not token:
            return {
                "configured": True,
                "base_url": self.config.base_url,
                "token_store": str(self.config.token_store),
                "has_token": bool(os.environ.get("CANVAS_ACCESS_TOKEN")),
                "using_env_access_token": bool(os.environ.get("CANVAS_ACCESS_TOKEN")),
            }

        expires_at = int(token.get("expires_at") or 0)
        return {
            "configured": True,
            "base_url": self.config.base_url,
            "token_store": str(self.config.token_store),
            "has_token": bool(token.get("access_token")),
            "has_refresh_token": bool(token.get("refresh_token")),
            "expires_at": expires_at or None,
            "expires_in_seconds": max(0, expires_at - int(time.time())) if expires_at else None,
            "using_env_access_token": False,
        }

    def _post_token(self, data: dict[str, str]) -> dict[str, Any]:
        url = f"{self.config.base_url}/login/oauth2/token"
        resp = requests.post(url, data=data, timeout=10)
        if not resp.ok:
            raise CanvasTokenError(f"Canvas token request failed: HTTP {resp.status_code} {resp.text[:300]}")
        return resp.json()


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("CANVAS_OAUTH_SESSION_SECRET") or secrets.token_hex(32)

    @app.get("/")
    def index():
        return (
            "<h1>Canvas OAuth Helper</h1>"
            "<p><a href='/canvas/oauth/start'>Start Canvas authorization</a></p>"
            "<p><a href='/canvas/oauth/status'>Check token status</a></p>"
        )

    @app.get("/canvas/oauth/start")
    def start():
        try:
            client = CanvasOAuthClient.from_env(require_client_credentials=True)
            state = secrets.token_urlsafe(32)
            session["canvas_oauth_state"] = state
            return redirect(client.authorization_url(state))
        except CanvasOAuthError as exc:
            return jsonify({"error": str(exc)}), 500

    @app.get("/canvas/oauth/callback")
    def callback():
        if request.args.get("error"):
            return jsonify({
                "error": request.args.get("error"),
                "error_description": request.args.get("error_description"),
            }), 400

        state = request.args.get("state")
        if not state or state != session.get("canvas_oauth_state"):
            return jsonify({"error": "Invalid OAuth state. Restart authorization from /canvas/oauth/start."}), 400

        code = request.args.get("code")
        if not code:
            return jsonify({"error": "Canvas callback did not include a code"}), 400

        try:
            client = CanvasOAuthClient.from_env(require_client_credentials=True)
            token = client.exchange_code(code)
            session.pop("canvas_oauth_state", None)
            return jsonify({
                "status": "ok",
                "message": "Canvas OAuth token saved. You can now run canvas_tutor_adapter.py.",
                "token_store": str(client.config.token_store),
                "expires_at": token.get("expires_at"),
                "has_refresh_token": bool(token.get("refresh_token")),
            })
        except CanvasOAuthError as exc:
            return jsonify({"error": str(exc)}), 500

    @app.get("/canvas/oauth/status")
    def status():
        try:
            client = CanvasOAuthClient.from_env(require_client_credentials=False)
            return jsonify(client.token_status())
        except CanvasOAuthError as exc:
            return jsonify({"configured": False, "error": str(exc)}), 500

    @app.post("/canvas/oauth/logout")
    def logout():
        try:
            client = CanvasOAuthClient.from_env(require_client_credentials=False)
            client.store.delete()
            return jsonify({"status": "ok", "message": "Stored Canvas token deleted"})
        except CanvasOAuthError as exc:
            return jsonify({"error": str(exc)}), 500

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Canvas OAuth helper")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Start the local OAuth callback server")
    subparsers.add_parser("status", help="Print token status")
    subparsers.add_parser("auth-url", help="Print an authorization URL")
    args = parser.parse_args()

    command = args.command or "serve"
    if command == "status":
        client = CanvasOAuthClient.from_env(require_client_credentials=False)
        print(json.dumps(client.token_status(), indent=2, sort_keys=True))
        return

    if command == "auth-url":
        client = CanvasOAuthClient.from_env(require_client_credentials=True)
        print(client.authorization_url(secrets.token_urlsafe(32)))
        return

    app = create_app()
    host = os.environ.get("CANVAS_OAUTH_HOST", "0.0.0.0")
    port = int(os.environ.get("CANVAS_OAUTH_PORT", "8080"))
    print(f"Canvas OAuth helper running. Open http://localhost:{port}/canvas/oauth/start")
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
