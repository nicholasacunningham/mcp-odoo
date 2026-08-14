"""Data, contact, and file tools for the Odoo Sign-only MCP profile."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import Context

from .server_core import mcp_surface_counts
from .sign_policy import (
    SIGN_MODELS,
    SIGN_WRITE_ENV,
    UNLINK_MODELS,
    attachment_metadata,
    attachment_with_data,
    audit,
    created_ids,
    is_sign_attachment,
    model_fields,
    normalize_ids,
    normalize_pdf,
    orm_create,
    orm_unlink,
    orm_write,
    require_confirm,
    resolve,
    safe_fields,
    validate_model,
    validate_write_values,
)
from .tool_helpers import clamp_limit, truthy_env


def sign_health(ctx: Context, instance: Optional[str] = None) -> Dict[str, Any]:
    """Verify Odoo Sign availability and the Sign-only MCP posture."""
    try:
        name, odoo = resolve(ctx, instance)
        models = odoo.get_models()
        missing = sorted(SIGN_MODELS - set(models.get("model_names", [])))
        return {
            "success": not missing,
            "tool": "sign_health",
            "instance": name,
            "server_version": odoo.get_server_version(),
            "user_context": odoo.get_user_context(),
            "missing_models": missing,
            "sign_writes_enabled": truthy_env(SIGN_WRITE_ENV),
            "surface": mcp_surface_counts(),
        }
    except Exception as exc:
        return {"success": False, "tool": "sign_health", "error": str(exc)}


def sign_model(
    ctx: Context,
    operation: str,
    model: str,
    domain: Optional[List[Any]] = None,
    fields: Optional[List[str]] = None,
    record_ids: Optional[List[int]] = None,
    values: Optional[Dict[str, Any]] = None,
    limit: int = 20,
    offset: int = 0,
    order: Optional[str] = None,
    confirm: bool = False,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Schema/read/search/create/write/unlink across the complete Sign model allowlist."""
    tool = "sign_model"
    try:
        operation = str(operation or "").strip().casefold()
        model = validate_model(model, mutable=operation in {"create", "write", "unlink"})
        name, odoo = resolve(ctx, instance)
        if operation == "schema":
            return {"success": True, "tool": tool, "model": model, "fields": model_fields(odoo, model)}
        if operation == "search":
            rows = odoo.search_read(
                model,
                domain or [],
                fields=safe_fields(odoo, model, fields),
                limit=clamp_limit(limit, maximum=200),
                offset=max(0, int(offset)),
                order=order,
            )
            return {"success": True, "tool": tool, "model": model, "count": len(rows), "result": rows}
        if operation == "read":
            ids = normalize_ids(record_ids or [])
            rows = odoo.read_records(model, ids, safe_fields(odoo, model, fields))
            return {"success": True, "tool": tool, "model": model, "count": len(rows), "result": rows}
        require_confirm(confirm)
        if operation == "create":
            result = orm_create(odoo, model, validate_write_values(model, values or {}))
            ids = created_ids(result)
        elif operation == "write":
            ids = normalize_ids(record_ids or [])
            result = orm_write(odoo, model, ids, validate_write_values(model, values or {}))
        elif operation == "unlink":
            if model not in UNLINK_MODELS:
                raise ValueError("unlink is blocked for durable Sign request/audit records")
            ids = normalize_ids(record_ids or [])
            result = orm_unlink(odoo, model, ids)
        else:
            raise ValueError("operation must be schema, search, read, create, write, or unlink")
        audit(tool, model, operation, ids, name)
        return {
            "success": True,
            "tool": tool,
            "instance": name,
            "model": model,
            "operation": operation,
            "record_ids": ids,
            "result": result,
        }
    except Exception as exc:
        return {"success": False, "tool": tool, "error": str(exc)}


def sign_contacts(
    ctx: Context,
    operation: str,
    query: Optional[str] = None,
    name: Optional[str] = None,
    email: Optional[str] = None,
    limit: int = 20,
    confirm: bool = False,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Search signer contacts or create the minimum contact needed for signing."""
    tool = "sign_contacts"
    try:
        operation = str(operation or "").strip().casefold()
        instance_name, odoo = resolve(ctx, instance)
        if operation == "search":
            text = str(query or "").strip()
            if not text:
                raise ValueError("query is required")
            rows = odoo.search_read(
                "res.partner",
                ["|", ["name", "ilike", text], ["email", "ilike", text]],
                fields=["name", "email", "company_id", "active"],
                limit=clamp_limit(limit, maximum=100),
                order="name ASC",
            )
            return {"success": True, "tool": tool, "count": len(rows), "result": rows}
        if operation == "create":
            require_confirm(confirm)
            contact_name = str(name or "").strip()
            contact_email = str(email or "").strip()
            if not contact_name or "@" not in contact_email:
                raise ValueError("name and a valid email are required")
            result = orm_create(
                odoo,
                "res.partner",
                {"name": contact_name, "email": contact_email, "type": "contact"},
            )
            ids = created_ids(result)
            audit(tool, "res.partner", "create", ids, instance_name)
            return {"success": True, "tool": tool, "partner_ids": ids, "result": result}
        raise ValueError("operation must be search or create")
    except Exception as exc:
        return {"success": False, "tool": tool, "error": str(exc)}


def sign_files(
    ctx: Context,
    operation: str,
    filename: Optional[str] = None,
    content_base64: Optional[str] = None,
    attachment_id: Optional[int] = None,
    include_data: bool = True,
    confirm: bool = False,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload a PDF for Sign or download a file already linked to Odoo Sign."""
    tool = "sign_files"
    try:
        operation = str(operation or "").strip().casefold()
        name, odoo = resolve(ctx, instance)
        if operation == "upload":
            require_confirm(confirm)
            file_name = str(filename or "").strip()
            if not file_name:
                raise ValueError("filename is required")
            if not file_name.lower().endswith(".pdf"):
                file_name += ".pdf"
            encoded, raw = normalize_pdf(content_base64 or "")
            result = orm_create(
                odoo,
                "ir.attachment",
                {"name": file_name, "datas": encoded, "mimetype": "application/pdf", "type": "binary"},
            )
            ids = created_ids(result)
            audit(tool, "ir.attachment", "create", ids, name)
            return {"success": True, "tool": tool, "attachment_ids": ids, "bytes": len(raw)}
        if operation == "download":
            aid = int(attachment_id or 0)
            if aid < 1:
                raise ValueError("attachment_id is required")
            meta = attachment_metadata(odoo, aid)
            if not is_sign_attachment(odoo, aid, meta):
                raise ValueError("attachment is not linked to Odoo Sign")
            return {"success": True, "tool": tool, "attachment": attachment_with_data(odoo, aid, include_data)}
        raise ValueError("operation must be upload or download")
    except Exception as exc:
        return {"success": False, "tool": tool, "error": str(exc)}
