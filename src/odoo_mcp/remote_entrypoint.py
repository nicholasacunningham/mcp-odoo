"""Secure remote HTTP entrypoint for hosted Odoo MCP deployments.

This entrypoint is intentionally fail-closed. Streamable HTTP MCP traffic is
served only when one of these authentication modes is configured:

1. Native OAuth via the existing ODOO_MCP_AUTH_* settings, or
2. A static bearer token whose SHA-256 digest is stored in
   MCP_HTTP_AUTH_TOKEN_SHA256.

If neither is configured, /mcp remains unavailable (HTTP 503) while /health
stays public for hosting-platform health checks and uptime monitors.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .auth import build_auth
from .server import mcp

LOCAL_HTTP_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
DEFAULT_ALLOWED_ORIGINS = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
]


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@mcp.custom_route("/health", methods=["GET", "HEAD"])
async def health(_: Request) -> Response:
    """Public liveness endpoint. Never includes credentials or Odoo data."""
    return JSONResponse({"status": "ok"})


class MCPAuthGate:
    """ASGI gate protecting the MCP endpoint when native OAuth is not active."""

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


def _configure_native_oauth() -> bool:
    """Enable the repository's native OAuth resource-server implementation."""
    auth = build_auth()
    if auth is None:
        return False
    auth_settings, verifier = auth
    mcp.settings.auth = auth_settings
    mcp._token_verifier = verifier
    return True


def build_app() -> Any:
    host = os.environ.get("MCP_HTTP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    path = os.environ.get("MCP_HTTP_PATH", "/mcp").strip() or "/mcp"
    allowed_hosts = _parse_csv(os.environ.get("MCP_ALLOWED_HOSTS")) or DEFAULT_ALLOWED_HOSTS
    allowed_origins = (
        _parse_csv(os.environ.get("MCP_ALLOWED_ORIGINS")) or DEFAULT_ALLOWED_ORIGINS
    )

    if host not in LOCAL_HTTP_HOSTS and not os.environ.get("MCP_ALLOW_REMOTE_HTTP"):
        raise ValueError(
            "Remote HTTP binding requires MCP_ALLOW_REMOTE_HTTP=1 and an explicit "
            "authentication configuration."
        )

    security = TransportSecuritySettings(
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )

    oauth_enabled = _configure_native_oauth()
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
    port = int(os.environ.get("MCP_HTTP_PORT", "8000"))
    log_level = os.environ.get("MCP_LOG_LEVEL", "INFO").strip().lower() or "info"
    app = build_app()
    uvicorn.run(app, host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    main()
