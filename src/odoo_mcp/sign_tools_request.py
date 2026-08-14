"""Request and workflow tools for the Odoo Sign-only MCP profile."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import Context

from .sign_policy import (
    ACTION_MODELS,
    BLOCKED_METHODS,
    attachment_with_data,
    audit,
    created_ids,
    model_fields,
    normalize_ids,
    orm_create,
    require_confirm,
    resolve,
)
from .tool_helpers import clamp_limit, validate_method_name


def sign_request(
    ctx: Context,
    operation: str,
    request_id: Optional[int] = None,
    wizard_id: Optional[int] = None,
    template_id: Optional[int] = None,
    attachment_ids: Optional[List[int]] = None,
    signer_id: Optional[int] = None,
    signers: Optional[List[Dict[str, Any]]] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    validity: Optional[str] = None,
    reminder_enabled: bool = False,
    reminder: int = 0,
    certificate_reference: bool = False,
    set_sign_order: bool = False,
    scheduled_date: Optional[str] = None,
    cc_partner_ids: Optional[List[int]] = None,
    include_data: bool = False,
    limit: int = 100,
    confirm: bool = False,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Prepare/send/status/completed/audit operations for Odoo Sign requests."""
    tool = "sign_request"
    try:
        operation = str(operation or "").strip().casefold()
        name, odoo = resolve(ctx, instance)
        if operation == "prepare":
            require_confirm(confirm)
            if template_id is None and not attachment_ids:
                raise ValueError("provide template_id or attachment_ids")
            values: Dict[str, Any] = {
                "reminder_enabled": bool(reminder_enabled),
                "reminder": max(0, int(reminder)),
                "certificate_reference": bool(certificate_reference),
                "set_sign_order": bool(set_sign_order),
            }
            if template_id is not None:
                values["template_id"] = int(template_id)
            if attachment_ids:
                values["attachment_ids"] = [[6, 0, normalize_ids(attachment_ids)]]
            if signer_id is not None:
                values["signer_id"] = int(signer_id)
            if subject is not None:
                values["subject"] = str(subject)
            if body is not None:
                values["body"] = str(body)
            if validity:
                values["validity"] = str(validity)
            if scheduled_date:
                values["scheduled_date"] = str(scheduled_date)
            if cc_partner_ids:
                values["cc_partner_ids"] = [[6, 0, normalize_ids(cc_partner_ids)]]
            if signers:
                commands = []
                for signer in signers:
                    if not isinstance(signer, dict) or not signer.get("partner_id"):
                        raise ValueError("each signer must include partner_id")
                    item = {"partner_id": int(signer["partner_id"])}
                    if signer.get("role_id") is not None:
                        item["role_id"] = int(signer["role_id"])
                    if signer.get("mail_sent_order") is not None:
                        item["mail_sent_order"] = int(signer["mail_sent_order"])
                    commands.append([0, 0, item])
                values["signer_ids"] = commands
            result = orm_create(odoo, "sign.send.request", values)
            ids = created_ids(result)
            audit(tool, "sign.send.request", "create", ids, name)
            return {"success": True, "tool": tool, "wizard_ids": ids, "sent": False, "result": result}
        if operation == "send":
            require_confirm(confirm)
            ids = normalize_ids([int(wizard_id or 0)])
            if getattr(odoo, "transport", "") == "json2":
                result = odoo.execute_method("sign.send.request", "create_request", ids=ids)
            else:
                result = odoo.execute_method("sign.send.request", "create_request", ids)
            audit(tool, "sign.send.request", "create_request", ids, name)
            return {"success": True, "tool": tool, "wizard_ids": ids, "result": result}

        rid = int(request_id or 0)
        if rid < 1:
            raise ValueError("request_id is required")
        requests = odoo.read_records(
            "sign.request",
            [rid],
            [
                "reference",
                "state",
                "template_id",
                "subject",
                "validity",
                "reminder_enabled",
                "reminder",
                "completion_date",
                "nb_closed",
                "nb_total",
                "nb_wait",
                "progress",
                "start_sign",
                "is_shared",
                "completed_document_attachment_ids",
            ],
        )
        if not requests:
            raise ValueError(f"sign.request {rid} not found")
        if operation == "status":
            signer_rows = odoo.search_read(
                "sign.request.item",
                [["sign_request_id", "=", rid]],
                fields=[
                    "sign_request_id",
                    "partner_id",
                    "role_id",
                    "state",
                    "mail_sent_order",
                    "signed_without_extra_auth",
                ],
                limit=200,
                order="mail_sent_order ASC, id ASC",
            )
            return {"success": True, "tool": tool, "request": requests[0], "signers": signer_rows}
        if operation == "completed":
            ids = [int(x) for x in requests[0].get("completed_document_attachment_ids") or []]
            attachments = [attachment_with_data(odoo, aid, include_data) for aid in ids]
            return {"success": True, "tool": tool, "request": requests[0], "attachments": attachments}
        if operation == "audit":
            metadata = model_fields(odoo, "sign.log")
            relation = next((x for x in ("sign_request_id", "request_id") if x in metadata), None)
            if relation is None:
                raise ValueError("could not identify sign.log request relation")
            candidates = [
                "create_date",
                "log_date",
                "action",
                "request_item_id",
                "partner_id",
                "user_id",
                "ip",
                "latitude",
                "longitude",
                "log_hash",
            ]
            fields = [x for x in candidates if x in metadata]
            rows = odoo.search_read(
                "sign.log",
                [[relation, "=", rid]],
                fields=fields,
                limit=clamp_limit(limit, maximum=500),
                order="id ASC",
            )
            return {"success": True, "tool": tool, "request": requests[0], "count": len(rows), "result": rows}
        raise ValueError("operation must be prepare, send, status, completed, or audit")
    except Exception as exc:
        return {"success": False, "tool": tool, "error": str(exc)}


def sign_action(
    ctx: Context,
    model: str,
    method: str,
    record_ids: Optional[List[int]] = None,
    params: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    confirm: bool = False,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Confirmed escape hatch for public Odoo Sign administrative workflow methods."""
    tool = "sign_action"
    try:
        require_confirm(confirm)
        model = str(model or "").strip()
        method = str(method or "").strip()
        if model not in ACTION_MODELS:
            raise ValueError(f"{model!r} is not allowed for Sign workflow methods")
        validate_method_name(method)
        if method.startswith("_") or method.casefold() in BLOCKED_METHODS:
            raise ValueError(f"{model}.{method} is blocked by the Sign safety policy")
        ids = normalize_ids(record_ids) if record_ids else []
        name, odoo = resolve(ctx, instance)
        kwargs = dict(params or {})
        if "ids" in kwargs or "context" in kwargs:
            raise ValueError("use the dedicated record_ids/context arguments")
        if context:
            kwargs["context"] = dict(context)
        if getattr(odoo, "transport", "") == "json2":
            if ids:
                kwargs["ids"] = ids
            result = odoo.execute_method(model, method, **kwargs)
        else:
            result = odoo.execute_method(model, method, *([ids] if ids else []), **kwargs)
        audit(tool, model, method, ids, name)
        return {
            "success": True,
            "tool": tool,
            "model": model,
            "method": method,
            "record_ids": ids,
            "result": result,
        }
    except Exception as exc:
        return {"success": False, "tool": tool, "error": str(exc)}
