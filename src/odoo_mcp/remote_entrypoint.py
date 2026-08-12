"""Secure remote HTTP entrypoint for hosted Odoo MCP deployments.

Remote MCP is fail-closed. Authentication preference is:

1. The repository's existing external OAuth resource-server configuration
   (ODOO_MCP_AUTH_*), or
2. The built-in GitHub-backed OAuth 2.1 authorization server
   (MCP_GITHUB_*; public URL derives from Render automatically), or
3. An explicitly configured static bearer-token SHA-256 digest.

If none is configured, /mcp returns HTTP 503 and never executes MCP code.
/health remains public for Render health checks and uptime monitoring.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

import uvicorn
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from .auth import build_auth
from .github_oauth import GitHubOAuthProvider
from .server import mcp

LOCAL_HTTP_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
DEFAULT_ALLOWED_ORIGINS = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
]

_github_provider: GitHubOAuthProvider | None = None


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


@mcp.custom_route("/health", methods=["GET", "HEAD"])
async def health(_: Request) -> Response:
    """Public liveness endpoint. Never includes credentials or Odoo data."""
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/oauth/github/callback", methods=["GET"])
async def github_oauth_callback(request: Request) -> Response:
    """Public upstream-IdP callback used only during the OAuth authorization flow."""
    provider = _github_provider
    if provider is None:
        return JSONResponse({"error": "github_oauth_not_configured"}, status_code=404)

    github_error = request.query_params.get("error")
    if github_error:
        return JSONResponse({"error": "github_authorization_denied"}, status_code=403)

    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    if not code or not state:
        return JSONResponse({"error": "missing_oauth_callback_parameters"}, status_code=400)

    try:
        redirect_uri = provider.complete_github_callback(
            github_code=code,
            github_state=state,
        )
    except PermissionError:
        return JSONResponse({"error": "github_user_not_authorized"}, status_code=403)
    except Exception:
        # Do not reflect upstream OAuth details, secrets, or tokens to the browser.
        return JSONResponse({"error": "github_oauth_callback_failed"}, status_code=400)
    return RedirectResponse(url=redirect_uri, status_code=302)


class MCPAuthGate:
    """ASGI bearer gate used only when OAuth is not active."""

    def __init__(
        self,
        app: Any,
        *,
        mcp_path: str,
        token_sha256: str | None,
    ) -> None:
        self.app = app
        self.mcp_path = "/" + mcp_path.strip("/")
        self.token_sha256 = (token_sha256 or "").strip().lower() or None

        if self.token_sha256 is not None:
            if len(self.token_sha256) != 64 or any(
                ch not in "0123456789abcdef" for ch in self.token_sha256
            ):
                raise ValueError(
                    "MCP_HTTP_AUTH_TOKEN_SHA256 must be a 64-character "
                    "lowercase hexadecimal SHA-256 digest"
                )

    def _is_mcp_path(self, path: str) -> bool:
        return path == self.mcp_path or path.startswith(self.mcp_path + "/")

    @staticmethod
    def _authorization_header(scope: dict[str, Any]) -> str:
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                return value.decode("latin-1")
        return ""

    def _authorized(self, scope: dict[str, Any]) -> bool:
        if self.token_sha256 is None:
            return False
        header = self._authorization_header(scope)
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        token = header[len(prefix) :].strip()
        if not token:
            return False
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, self.token_sha256)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not self._is_mcp_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        if self.token_sha256 is None:
            response = JSONResponse(
                {
                    "error": "mcp_auth_not_configured",
                    "message": "Remote MCP access is disabled until authentication is configured.",
                },
                status_code=503,
            )
            await response(scope, receive, send)
            return

        if not self._authorized(scope):
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _configure_external_oauth() -> bool:
    """Enable the repository's existing external OAuth resource-server mode."""
    auth = build_auth()
    if auth is None:
        return False
    auth_settings, verifier = auth
    mcp.settings.auth = auth_settings
    mcp._token_verifier = verifier
    return True


def _configure_github_oauth(path: str) -> bool:
    """Enable same-process OAuth 2.1 with GitHub as the upstream IdP."""
    global _github_provider

    public_url = (
        os.environ.get("MCP_PUBLIC_URL", "").strip()
        or os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    ).rstrip("/")
    client_id = os.environ.get("MCP_GITHUB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("MCP_GITHUB_CLIENT_SECRET", "").strip()
    allowed_users = set(_parse_csv(os.environ.get("MCP_GITHUB_ALLOWED_USERS")))
    scope = os.environ.get("MCP_OAUTH_SCOPE", "odoo").strip() or "odoo"

    if bool(client_id) != bool(client_secret):
        raise ValueError(
            "Incomplete GitHub OAuth configuration: MCP_GITHUB_CLIENT_ID and "
            "MCP_GITHUB_CLIENT_SECRET must be set together."
        )
    if not client_id:
        return False
    if not public_url:
        raise ValueError(
            "GitHub OAuth requires MCP_PUBLIC_URL, or RENDER_EXTERNAL_URL when running on Render."
        )
    if not public_url.startswith("https://"):
        raise ValueError("The OAuth public URL must use HTTPS for a remote deployment.")

    resource_url = f"{public_url}/{path.strip('/')}"
    provider = GitHubOAuthProvider(
        public_url=public_url,
        resource_url=resource_url,
        github_client_id=client_id,
        github_client_secret=client_secret,
        allowed_users=allowed_users,
        scope=scope,
    )
    settings = AuthSettings(
        issuer_url=AnyHttpUrl(public_url),
        resource_server_url=AnyHttpUrl(resource_url),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[scope],
            default_scopes=[scope],
            client_secret_expiry_seconds=30 * 24 * 60 * 60,
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=[scope],
    )
    mcp.settings.auth = settings
    mcp._auth_server_provider = provider
    mcp._token_verifier = ProviderTokenVerifier(provider)
    _github_provider = provider
    return True


def _configure_oauth(path: str) -> bool:
    external = _configure_external_oauth()
    github_env_present = any(
        os.environ.get(name, "").strip()
        for name in ("MCP_GITHUB_CLIENT_ID", "MCP_GITHUB_CLIENT_SECRET")
    )
    if external and github_env_present:
        raise ValueError(
            "Configure either ODOO_MCP_AUTH_* external OAuth or MCP_GITHUB_* same-process OAuth, not both."
        )
    if external:
        return True
    return _configure_github_oauth(path)


def build_app() -> Any:
    host = os.environ.get("MCP_HTTP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    path = os.environ.get("MCP_HTTP_PATH", "/mcp").strip() or "/mcp"
    allowed_hosts = _parse_csv(os.environ.get("MCP_ALLOWED_HOSTS")) or DEFAULT_ALLOWED_HOSTS
    allowed_origins = (
        _parse_csv(os.environ.get("MCP_ALLOWED_ORIGINS")) or DEFAULT_ALLOWED_ORIGINS
    )

    if host not in LOCAL_HTTP_HOSTS and not _truthy(os.environ.get("MCP_ALLOW_REMOTE_HTTP")):
        raise ValueError(
            "Remote HTTP binding requires MCP_ALLOW_REMOTE_HTTP=1 and an explicit "
            "authentication configuration."
        )

    security = TransportSecuritySettings(
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )

    oauth_enabled = _configure_oauth(path)
    app = mcp.streamable_http_app(
        host=host,
        streamable_http_path=path,
        transport_security=security,
    )

    if oauth_enabled:
        return app

    token_sha256 = os.environ.get("MCP_HTTP_AUTH_TOKEN_SHA256")
    return MCPAuthGate(app, mcp_path=path, token_sha256=token_sha256)


def main() -> None:
    host = os.environ.get("MCP_HTTP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(
        os.environ.get("MCP_HTTP_PORT", "").strip()
        or os.environ.get("PORT", "").strip()
        or "8000"
    )
    log_level = os.environ.get("MCP_LOG_LEVEL", "INFO").strip().lower() or "info"
    app = build_app()
    uvicorn.run(app, host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    main()
