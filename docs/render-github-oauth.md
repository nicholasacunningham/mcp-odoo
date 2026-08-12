# Render + GitHub OAuth for Odoo MCP

The hosted Render deployment is intentionally **fail-closed**. Do not add an Odoo API key until OAuth is active and unauthenticated `/mcp` requests are rejected.

## GitHub OAuth App

Create a GitHub OAuth App with:

- Application name: `Thrive Odoo MCP`
- Homepage URL: `https://mcp-odoo-65yn.onrender.com`
- Authorization callback URL: `https://mcp-odoo-65yn.onrender.com/oauth/github/callback`

The server requests only GitHub `read:user` identity scope. MCP access is additionally restricted by `MCP_GITHUB_ALLOWED_USERS`.

## Render environment

Set these on the `mcp-odoo` web service:

- `MCP_GITHUB_CLIENT_ID` — the OAuth App client ID
- `MCP_GITHUB_CLIENT_SECRET` — the OAuth App client secret; store only in Render's environment/secret UI
- `MCP_GITHUB_ALLOWED_USERS=nicholasacunningham`

`MCP_PUBLIC_URL` is optional on Render because the server automatically uses Render's `RENDER_EXTERNAL_URL`.

Existing HTTP settings:

- `MCP_TRANSPORT=streamable-http`
- `MCP_HTTP_HOST=0.0.0.0`
- `MCP_HTTP_PORT=10000`
- `MCP_HTTP_PATH=/mcp`
- `MCP_ALLOW_REMOTE_HTTP=1`
- `MCP_ALLOWED_HOSTS=mcp-odoo-65yn.onrender.com`

## OAuth behavior

When GitHub OAuth is configured, the MCP Python SDK exposes OAuth 2.1 endpoints including authorization-server metadata, dynamic client registration, authorization, token, revocation, and RFC 9728 protected-resource metadata. PKCE is required by the SDK.

The GitHub identity flow uses its own state value and PKCE challenge. After GitHub confirms the user, the server issues short-lived MCP access tokens and rotating refresh tokens. `/health` is public; `/mcp` requires a valid OAuth bearer token.

## Fail-closed behavior

If OAuth is not configured, `/mcp` does **not** fall back to anonymous access. It returns HTTP 503 unless an explicit static bearer-token digest is configured through `MCP_HTTP_AUTH_TOKEN_SHA256`.

This is deliberate. Never set Odoo credentials on a remote deployment that has not passed the authentication checks below.

## Verification before adding Odoo credentials

1. `GET /health` returns HTTP 200.
2. An unauthenticated request to `/mcp` is rejected.
3. With OAuth enabled, the rejection is HTTP 401 and includes `WWW-Authenticate` with protected-resource metadata.
4. OAuth metadata is reachable and dynamic client registration succeeds.
5. GitHub sign-in succeeds only for an allowlisted GitHub login.
6. The client completes PKCE and can call MCP only with the issued bearer token.

Only after all six checks pass should `ODOO_API_KEY` be added to Render.
