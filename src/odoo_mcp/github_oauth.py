"""GitHub-backed OAuth 2.1 authorization server for hosted MCP deployments.

The MCP client authenticates to this server using the SDK's OAuth endpoints.
GitHub is used only as the upstream identity provider. MCP access and refresh
tokens are signed, self-contained credentials scoped to this MCP resource so
client registrations and active sessions survive stateless host restarts.

This provider supports dynamic client registration, PKCE, refresh/offline
access, and an explicit GitHub-login allowlist.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
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
CLIENT_TOKEN_PREFIX = "mcpc1"
ACCESS_TOKEN_PREFIX = "mcpa1"
REFRESH_TOKEN_PREFIX = "mcpr1"


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

        # Derive a purpose-separated signing key from the existing GitHub OAuth
        # app secret. The secret itself is never placed in client IDs or tokens.
        self._signing_key = hmac.new(
            github_client_secret.encode("utf-8"),
            b"thrive-odoo-mcp-oauth-state-v1",
            hashlib.sha256,
        ).digest()

        # These dictionaries remain useful within one process for short-lived
        # authorization state and backwards compatibility. Long-lived client
        # registrations and bearer/refresh tokens are independently verifiable
        # from their signatures after a restart.
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.pending: dict[str, PendingGitHubAuthorization] = {}
        self.authorization_codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}
        self.revoked_token_digests: set[str] = set()

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

    @staticmethod
    def _b64encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    def _signed_value(self, prefix: str, payload: dict[str, Any]) -> str:
        raw = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        body = self._b64encode(raw)
        signature = hmac.new(
            self._signing_key,
            f"{prefix}.{body}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{prefix}.{body}.{self._b64encode(signature)}"

    def _verify_signed_value(
        self, value: str, prefix: str
    ) -> dict[str, Any] | None:
        try:
            actual_prefix, body, supplied_signature = value.split(".", 2)
        except ValueError:
            return None
        if actual_prefix != prefix:
            return None
        expected_signature = hmac.new(
            self._signing_key,
            f"{prefix}.{body}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        try:
            supplied = self._b64decode(supplied_signature)
        except Exception:
            return None
        if not hmac.compare_digest(expected_signature, supplied):
            return None
        try:
            payload = json.loads(self._b64decode(body).decode("utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _client_secret_for(self, client_id: str) -> str:
        digest = hmac.new(
            self._signing_key,
            b"client-secret\x00" + client_id.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return self._b64encode(digest)

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

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
        legacy = self.clients.get(client_id)
        if legacy is not None:
            return legacy

        payload = self._verify_signed_value(client_id, CLIENT_TOKEN_PREFIX)
        if payload is None:
            return None
        client_data = payload.get("client")
        if not isinstance(client_data, dict):
            return None

        data = dict(client_data)
        data["client_id"] = client_id
        auth_method = data.get("token_endpoint_auth_method")
        if auth_method != "none":
            data["client_secret"] = self._client_secret_for(client_id)
        else:
            data["client_secret"] = None

        expires_at = data.get("client_secret_expires_at")
        if expires_at not in (None, 0):
            try:
                if int(expires_at) <= int(time.time()):
                    return None
            except (TypeError, ValueError):
                return None

        try:
            return OAuthClientInformationFull.model_validate(data)
        except Exception:
            return None

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

        # The SDK creates random credentials before calling register_client().
        # Replace them in-place with signed/reconstructable credentials. The
        # registration handler returns this same object to the MCP client.
        client_data = client_info.model_dump(mode="json")
        client_data.pop("client_id", None)
        client_data.pop("client_secret", None)
        signed_client_id = self._signed_value(
            CLIENT_TOKEN_PREFIX,
            {"client": client_data},
        )
        client_info.client_id = signed_client_id
        if client_info.token_endpoint_auth_method != "none":
            client_info.client_secret = self._client_secret_for(signed_client_id)
        else:
            client_info.client_secret = None
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
        access_expires_at = now + ACCESS_TOKEN_TTL_SECONDS
        refresh_expires_at = now + REFRESH_TOKEN_TTL_SECONDS
        access_value = self._signed_value(
            ACCESS_TOKEN_PREFIX,
            {
                "client_id": client_id,
                "scopes": scopes,
                "subject": subject,
                "resource": resource,
                "issued_at": now,
                "expires_at": access_expires_at,
                "nonce": secrets.token_urlsafe(12),
            },
        )
        refresh_value = self._signed_value(
            REFRESH_TOKEN_PREFIX,
            {
                "client_id": client_id,
                "scopes": scopes,
                "subject": subject,
                "issued_at": now,
                "expires_at": refresh_expires_at,
                "nonce": secrets.token_urlsafe(16),
            },
        )
        access = AccessToken(
            token=access_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=access_expires_at,
            resource=resource,
            subject=subject,
            claims={"iss": self.public_url},
        )
        refresh = RefreshToken(
            token=refresh_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=refresh_expires_at,
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
        if self._token_digest(refresh_token) in self.revoked_token_digests:
            return None

        token = self.refresh_tokens.get(refresh_token)
        if token is not None:
            return token if token.client_id == client.client_id else None

        payload = self._verify_signed_value(refresh_token, REFRESH_TOKEN_PREFIX)
        if payload is None or payload.get("client_id") != client.client_id:
            return None
        try:
            expires_at = int(payload["expires_at"])
            scopes = [str(value) for value in payload.get("scopes", [])]
        except (KeyError, TypeError, ValueError):
            return None
        if expires_at <= int(time.time()):
            return None
        subject = payload.get("subject")
        return RefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=expires_at,
            subject=str(subject) if subject is not None else None,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        stored = await self.load_refresh_token(client, refresh_token.token)
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
        if self._token_digest(token) in self.revoked_token_digests:
            return None

        access = self.access_tokens.get(token)
        if access is not None:
            if (
                access.resource
                and access.resource.rstrip("/") != self.resource_url.rstrip("/")
            ):
                return None
            return access

        payload = self._verify_signed_value(token, ACCESS_TOKEN_PREFIX)
        if payload is None:
            return None
        try:
            expires_at = int(payload["expires_at"])
            client_id = str(payload["client_id"])
            scopes = [str(value) for value in payload.get("scopes", [])]
            resource = str(payload["resource"])
        except (KeyError, TypeError, ValueError):
            return None
        if expires_at <= int(time.time()):
            return None
        if resource.rstrip("/") != self.resource_url.rstrip("/"):
            return None
        if await self.get_client(client_id) is None:
            return None
        subject = payload.get("subject")
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=expires_at,
            resource=resource,
            subject=str(subject) if subject is not None else None,
            claims={"iss": self.public_url},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.revoked_token_digests.add(self._token_digest(token.token))
        self.access_tokens.pop(token.token, None)
        self.refresh_tokens.pop(token.token, None)
