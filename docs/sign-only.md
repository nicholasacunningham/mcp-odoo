# OdooSign MCP profile

This repository includes a first-party `sign` plugin for a dedicated Odoo Sign deployment. It reuses the existing Odoo JSON-2 client and remote OAuth transport, but removes the generic ERP MCP surface.

## Render flags

```text
ODOO_MCP_PLUGINS=sign
ODOO_MCP_PROFILE=sign
ODOO_MCP_TOOLS_INCLUDE=sign_*
ODOO_MCP_SIGN_ENABLE_WRITES=1
ODOO_MCP_MAX_ATTACHMENT_BYTES=16777216
ODOO_MCP_MAX_ATTACHMENT_UPLOAD_BYTES=16777216
```

Keep the existing Odoo URL/database/API-key and GitHub OAuth variables unchanged.

## Exposed tools

- `sign_health`: verifies the live Sign models and Sign-only surface.
- `sign_model`: schema, search, read, create, write, and narrowly scoped unlink across `sign.*` models.
- `sign_contacts`: signer lookup and minimal signer-contact creation.
- `sign_files`: PDF upload plus download of attachments linked to Odoo Sign.
- `sign_request`: prepare/send requests, including one-off PDFs or templates, multiple signers, order, reminders, expiration, scheduling, CCs, certificate reference, plus status/completed-document/audit retrieval.
- `sign_action`: confirmed escape hatch for public Odoo Sign administrative workflow methods.

## Security boundary

All mutations require both `ODOO_MCP_SIGN_ENABLE_WRITES=1` and `confirm=true`.

The profile blocks direct writes to signatures, access tokens, completion state, completed-document pointers, integrity/progress fields, and audit logs. It also blocks destructive deletion of durable signature requests and blocks direct signing/completion workflow method names.

The MCP administers the signature process. The actual signer still uses Odoo Sign to perform the signature and any identity/authentication step.
