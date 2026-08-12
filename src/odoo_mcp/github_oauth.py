"""GitHub-backed OAuth 2.1 authorization server for hosted MCP deployments.

The MCP client authenticates to this server using the SDK's OAuth endpoints.
GitHub is used only as the upstream identity provider. MCP access and refresh
tokens are opaque, high-entropy tokens issued by this process and scoped to
this MCP resource.

This provider intentionally supports dynamic client registration, PKCE, and
refresh/offline access. Access is additionally restricted by
MCP_GITHUB_ALLOWED_USERS so possessing a GitHub account alone is not sufficient
to reach the Odoo MCP server.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


ACCESS_TOKEN_TTL_SECONDS = 60 * 60
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
AUTH_CODE_TTL_SECONDS = 5 * 60
GITHUB_STATE_TTL_SECONDS = 10 * 60
OFFLINE_ACCESS_SCOPE = "offline_access"


@dataclass
class PendingGitHubAuthorization:
    client_id: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    code_challenge: str
    scopes: list[str]
    client_state: str | None
    resource: str
    expires_at: float
    github_code_verifier: str


class GitHubOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth authorization-server provider using GitHub for user sign-in."""

    def __init__(
        self,
        *,
        public_url: str,
        resource_url: str,
        github_client_id: str,
        github_client_secret: str,
        allowed_users: set[str],
        scope: str = "odoo",
    ) -> None:
        self.public_url = public_url.rstrip("/")
        self.resource_url = resource_url
        self.github_client_id = github_client_id
        self.github_client_secret = github_client_secret
        self.allowed_users = {user.casefold() for user in allowed_users}
        self.scope = scope

        if not self.allowed_users:
            raise ValueError(
                "MCP_GITHUB_ALLOWED_USERS must list at least one GitHub login; "
                "GitHub OAuth is fail-closed without an explicit user allowlist."
            )

        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.pending: dict[str, PendingGitHubAuthorization] = {}
        self.authorization_codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}

    @property
    def github_callback_url(self) -> str:
        return f"{self.public_url}/oauth/github/callback"

    @staticmethod
    def _pkce_pair() -> tuple[str, str]:
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return verifier, challenge

    @staticmethod
    def _is_safe_redirect_uri(uri: str) -> bool:
        lowered = uri.casefold()
        return lowered.startswith("https://") or lowered.startswith(
            ("http://127.0.0.1", "http://localhost", "http://[::1]")
        )

    def _cleanup_expired(self) -> None:
        now = time.time()
        self.pending = {
            key: value for key, value in self.pending.items() if value.expires_at > now
        }
        self.authorization_codes = {
            key: value
            for key, value in self.authorization_codes.items()
            if value.expires_at > now
        }
        self.access_tokens = {
            key: value
            for key, value in self.access_tokens.items()
            if value.expires_at is None or value.expires_at > now
        }
        self.refresh_tokens = {
            key: value
            for key, value in self.refresh_tokens.items()
            if value.expires_at is None or value.expires_at > now
        }

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.redirect_uris:
            raise RegistrationError(
                error="invalid_redirect_uri",
                error_description="At least one redirect URI is required.",
            )
        unsafe = [
            str(uri)
            for uri in client_info.redirect_uris
            if not self._is_safe_redirect_uri(str(uri))
        ]
        if unsafe:
            raise RegistrationError(
                error="invalid_redirect_uri",
                error_description="Redirect URIs must use HTTPS or an HTTP loopback address.",
            )
        self.clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        self._cleanup_expired()
        resource = params.resource or self.resource_url
        if resource.rstrip("/") != self.resource_url.rstrip("/"):
            raise AuthorizeError(
                error="invalid_target",
                error_description="The requested OAuth resource does not match this MCP server.",
            )

        scopes = params.scopes or [self.scope, OFFLINE_ACCESS_SCOPE]
        supported_scopes = {self.scope, OFFLINE_ACCESS_SCOPE}
        if not set(scopes).issubset(supported_scopes) or self.scope not in scopes:
            raise AuthorizeError(
                error="invalid_scope",
                error_description="Unsupported OAuth scope.",
            )

        github_state = secrets.token_urlsafe(32)
        github_verifier, github_challenge = self._pkce_pair()
        self.pending[github_state] = PendingGitHubAuthorization(
            client_id=client.client_id,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            code_challenge=params.code_challenge,
            scopes=scopes,
            client_state=params.state,
            resource=resource,
            expires_at=time.time() + GITHUB_STATE_TTL_SECONDS,
            github_code_verifier=github_verifier,
        )

        query = urlencode(
            {
                "client_id": self.github_client_id,
                "redirect_uri": self.github_callback_url,
                "scope": "read:user",
                "state": github_state,
                "code_challenge": github_challenge,
                "code_challenge_method": "S256",
                "allow_signup": "false",
            }
        )
        return f"https://github.com/login/oauth/authorize?{query}"

    def complete_github_callback(self, *, github_code: str, github_state: str) -> str:
        """Exchange GitHub's code, validate the user, and redirect to the MCP client."""
        self._cleanup_expired()
        pending = self.pending.pop(github_state, None)
        if pending is None or pending.expires_at <= time.time():
            raise ValueError("Invalid or expired GitHub OAuth state.")

        token_response = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={
                "Accept": "application/json",
                "User-Agent": "thrive-odoo-mcp",
            },
            data={
                "client_id": self.github_client_id,
                "client_secret": self.github_client_secret,
                "code": github_code,
                "redirect_uri": self.github_callback_url,
                "code_verifier": pending.github_code_verifier,
            },
            timeout=15,
        )
        token_response.raise_for_status()
        token_payload = token_response.json()
        github_access_token = token_payload.get("access_token")
        if not github_access_token:
            raise ValueError(
                "GitHub OAuth token exchange failed: "
                + str(
                    token_payload.get("error_description")
                    or token_payload.get("error")
                    or "unknown error"
                )
            )

        user_response = requests.get(
            "https://api.github.com/user",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {github_access_token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "thrive-odoo-mcp",
            },
            timeout=15,
        )
        user_response.raise_for_status()
        user = user_response.json()
        login = str(user.get("login") or "")
        user_id = user.get("id")
        if not login or user_id is None:
            raise ValueError("GitHub did not return a valid user identity.")
        if login.casefold() not in self.allowed_users:
            raise PermissionError(
                "This GitHub account is not authorized for the Thrive Odoo MCP server."
            )

        code = secrets.token_urlsafe(32)
        self.authorization_codes[code] = AuthorizationCode(
            code=code,
            client_id=pending.client_id,
            redirect_uri=pending.redirect_uri,
            redirect_uri_provided_explicitly=pending.redirect_uri_provided_explicitly,
            expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
            scopes=pending.scopes,
            code_challenge=pending.code_challenge,
            resource=pending.resource,
            subject=f"github:{user_id}:{login}",
        )
        return construct_redirect_uri(
            pending.redirect_uri,
            code=code,
            state=pending.client_state,
            iss=self.public_url,
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        self._cleanup_expired()
        code = self.authorization_codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    def _issue_token_pair(
        self,
        *,
        client_id: str,
        scopes: list[str],
        subject: str | None,
        resource: str,
    ) -> OAuthToken:
        now = int(time.time())
        access_value = secrets.token_urlsafe(48)
        refresh_value = secrets.token_urlsafe(64)
        access = AccessToken(
            token=access_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + ACCESS_TOKEN_TTL_SECONDS,
            resource=resource,
            subject=subject,
            claims={"iss": self.public_url},
        )
        refresh = RefreshToken(
            token=refresh_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + REFRESH_TOKEN_TTL_SECONDS,
            subject=subject,
        )
        self.access_tokens[access_value] = access
        self.refresh_tokens[refresh_value] = refresh
        return OAuthToken(
            access_token=access_value,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=refresh_value,
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        stored = self.authorization_codes.pop(authorization_code.code, None)
        if (
            stored is None
            or stored.client_id != client.client_id
            or stored.expires_at <= time.time()
        ):
            raise TokenError(
                error="invalid_grant",
                error_description="Invalid or expired authorization code.",
            )
        resource = stored.resource or self.resource_url
        return self._issue_token_pair(
            client_id=client.client_id,
            scopes=stored.scopes,
            subject=stored.subject,
            resource=resource,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        self._cleanup_expired()
        token = self.refresh_tokens.get(refresh_token)
        if token is None or token.client_id != client.client_id:
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        stored = self.refresh_tokens.pop(refresh_token.token, None)
        if stored is None or stored.client_id != client.client_id:
            raise TokenError(
                error="invalid_grant", error_description="Invalid refresh token."
            )
        requested = scopes or stored.scopes
        if not set(requested).issubset(set(stored.scopes)):
            raise TokenError(
                error="invalid_scope",
                error_description="Refresh requested an unauthorized scope.",
            )
        return self._issue_token_pair(
            client_id=client.client_id,
            scopes=requested,
            subject=stored.subject,
            resource=self.resource_url,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        self._cleanup_expired()
        access = self.access_tokens.get(token)
        if access is None:
            return None
        if (
            access.resource
            and access.resource.rstrip("/") != self.resource_url.rstrip("/")
        ):
            return None
        return access

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.access_tokens.pop(token.token, None)
        self.refresh_tokens.pop(token.token, None)
