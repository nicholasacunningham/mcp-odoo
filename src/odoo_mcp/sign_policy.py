"""Shared policy and transport helpers for the Odoo Sign-only MCP profile."""

from __future__ import annotations

import base64
import binascii
from typing import Any, Dict, Iterable, List, Optional

from mcp.server.mcpserver import Context

from .audit import record_write_event
from .tool_helpers import (
    max_attachment_bytes,
    max_attachment_upload_bytes,
    truthy_env,
)

SIGN_WRITE_ENV = "ODOO_MCP_SIGN_ENABLE_WRITES"
SIGN_PROFILE_ENV = "ODOO_MCP_PROFILE"

SIGN_MODELS = frozenset(
    {
        "sign.completed.document",
        "sign.document",
        "sign.item",
        "sign.item.option",
        "sign.item.radio.set",
        "sign.item.role",
        "sign.item.type",
        "sign.log",
        "sign.request",
        "sign.request.item",
        "sign.request.item.value",
        "sign.request.share",
        "sign.send.request",
        "sign.send.request.signer",
        "sign.template",
        "sign.template.preview",
        "sign.template.tag",
    }
)
MUTABLE_MODELS = SIGN_MODELS - {"sign.completed.document", "sign.log"}
UNLINK_MODELS = frozenset(
    {
        "sign.document",
        "sign.item",
        "sign.item.option",
        "sign.item.radio.set",
        "sign.request.share",
        "sign.send.request",
        "sign.send.request.signer",
        "sign.template.preview",
        "sign.template.tag",
    }
)
ACTION_MODELS = frozenset(
    {
        "sign.document",
        "sign.item",
        "sign.item.option",
        "sign.item.radio.set",
        "sign.item.role",
        "sign.item.type",
        "sign.request",
        "sign.request.share",
        "sign.send.request",
        "sign.send.request.signer",
        "sign.template",
        "sign.template.preview",
        "sign.template.tag",
    }
)
BLOCKED_READ_FIELDS = frozenset({"access_token", "signature", "datas", "share_link"})
BLOCKED_WRITE_FIELDS = frozenset({"access_token", "signature", "share_link"})
MODEL_BLOCKED_WRITE_FIELDS = {
    "sign.request": frozenset(
        {
            "state",
            "completion_date",
            "completed_document_attachment_ids",
            "completed_document_ids",
            "sign_log_ids",
            "integrity",
            "nb_closed",
            "nb_total",
            "nb_wait",
            "progress",
            "start_sign",
        }
    ),
    "sign.request.item": frozenset({"state", "signed_without_extra_auth"}),
}
BLOCKED_METHODS = frozenset(
    {
        "create",
        "write",
        "unlink",
        "search",
        "search_count",
        "search_read",
        "read",
        "fields_get",
        "browse",
        "sudo",
        "with_context",
        "with_user",
        "with_company",
        "sign",
        "action_sign",
        "action_signed",
        "complete",
        "action_complete",
        "mark_as_signed",
        "set_signed",
        "validate_signature",
        "update_signature",
    }
)
DEFAULT_FIELDS = (
    "id",
    "name",
    "display_name",
    "active",
    "reference",
    "state",
    "template_id",
    "document_ids",
    "attachment_id",
    "subject",
    "message",
    "body",
    "partner_id",
    "role_id",
    "responsible_id",
    "type_id",
    "request_item_ids",
    "sign_item_ids",
    "completed_document_attachment_ids",
    "validity",
    "reminder_enabled",
    "reminder",
    "completion_date",
    "scheduled_date",
    "certificate_reference",
    "set_sign_order",
    "mail_sent_order",
    "required",
    "page",
    "posX",
    "posY",
    "width",
    "height",
    "alignment",
    "create_date",
    "write_date",
)

_API: Any = None


def set_plugin_api(api: Any) -> None:
    global _API
    _API = api


def resolve(ctx: Context, instance: Optional[str]) -> tuple[str, Any]:
    if _API is None:
        raise RuntimeError("Odoo Sign plugin is not registered")
    return _API.resolve_odoo(ctx, instance)


def require_confirm(confirm: bool) -> None:
    if not truthy_env(SIGN_WRITE_ENV):
        raise ValueError(f"Sign writes are disabled; set {SIGN_WRITE_ENV}=1")
    if not confirm:
        raise ValueError("confirm=true is required for Odoo Sign mutations")


def validate_model(model: str, mutable: bool = False) -> str:
    value = str(model or "").strip()
    if value not in (MUTABLE_MODELS if mutable else SIGN_MODELS):
        raise ValueError(f"{value!r} is outside the Odoo Sign model allowlist")
    return value


def normalize_ids(values: Iterable[int]) -> List[int]:
    result = [int(value) for value in values]
    if not result or any(value < 1 for value in result):
        raise ValueError("record IDs must be positive integers")
    return result


def model_fields(odoo: Any, model: str) -> Dict[str, Any]:
    result = odoo.get_model_fields(model)
    if not isinstance(result, dict) or result.get("error"):
        raise ValueError(str((result or {}).get("error") or "fields_get failed"))
    return result


def safe_fields(odoo: Any, model: str, requested: Optional[List[str]]) -> List[str]:
    metadata = model_fields(odoo, model)
    if requested is not None:
        unknown = [name for name in requested if name != "id" and name not in metadata]
        if unknown:
            raise ValueError(f"unknown fields for {model}: {unknown}")
        for name in requested:
            field = metadata.get(name, {})
            if name in BLOCKED_READ_FIELDS or (
                isinstance(field, dict) and field.get("type") == "binary"
            ):
                raise ValueError("binary/security fields require sign_files")
        return list(dict.fromkeys(requested))
    result: List[str] = []
    for name in DEFAULT_FIELDS:
        field = metadata.get(name, {})
        if name == "id" or (
            name in metadata
            and name not in BLOCKED_READ_FIELDS
            and not (isinstance(field, dict) and field.get("type") == "binary")
        ):
            result.append(name)
    return result[:40]


def validate_write_values(model: str, values: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(values, dict) or not values:
        raise ValueError("values must be a non-empty object")
    blocked = set(BLOCKED_WRITE_FIELDS)
    blocked.update(MODEL_BLOCKED_WRITE_FIELDS.get(model, frozenset()))
    attempted = sorted(set(values).intersection(blocked))
    if attempted:
        raise ValueError(
            "Direct writes to signer/security/audit fields are blocked: "
            + ", ".join(attempted)
        )
    return dict(values)


def created_ids(result: Any) -> List[int]:
    if isinstance(result, int):
        return [result]
    if isinstance(result, list):
        return [int(x) for x in result if isinstance(x, int) or str(x).isdigit()]
    return []


def orm_create(odoo: Any, model: str, values: Dict[str, Any]) -> Any:
    if getattr(odoo, "transport", "") == "json2":
        return odoo.execute_method(model, "create", vals_list=[values])
    return odoo.execute_method(model, "create", [values])


def orm_write(odoo: Any, model: str, record_ids: List[int], values: Dict[str, Any]) -> Any:
    if getattr(odoo, "transport", "") == "json2":
        return odoo.execute_method(model, "write", ids=record_ids, vals=values)
    return odoo.execute_method(model, "write", record_ids, values)


def orm_unlink(odoo: Any, model: str, record_ids: List[int]) -> Any:
    if getattr(odoo, "transport", "") == "json2":
        return odoo.execute_method(model, "unlink", ids=record_ids)
    return odoo.execute_method(model, "unlink", record_ids)


def audit(tool: str, model: str, operation: str, ids: List[int], instance: str) -> None:
    record_write_event(
        tool,
        outcome="success",
        model=model,
        operation=operation,
        record_ids=ids,
        instance=instance,
    )


def normalize_pdf(value: str) -> tuple[str, bytes]:
    text = str(value or "").strip()
    if text.startswith("data:"):
        if ";base64," not in text:
            raise ValueError("invalid data URI")
        text = text.split(";base64,", 1)[1]
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("content_base64 is not valid base64") from exc
    if not raw.startswith(b"%PDF-"):
        raise ValueError("only PDF documents may be uploaded to Odoo Sign")
    if len(raw) > max_attachment_upload_bytes():
        raise ValueError("PDF exceeds the configured upload cap")
    return base64.b64encode(raw).decode("ascii"), raw


def attachment_metadata(odoo: Any, attachment_id: int) -> Dict[str, Any]:
    rows = odoo.read_records(
        "ir.attachment",
        [attachment_id],
        ["name", "mimetype", "res_model", "res_id", "file_size"],
    )
    if not rows:
        raise ValueError(f"attachment {attachment_id} not found")
    return dict(rows[0])


def is_sign_attachment(odoo: Any, attachment_id: int, meta: Dict[str, Any]) -> bool:
    if str(meta.get("res_model") or "").startswith("sign."):
        return True
    if odoo.search_read(
        "sign.document",
        [["attachment_id", "=", attachment_id]],
        fields=["id"],
        limit=1,
    ):
        return True
    return bool(
        odoo.search_read(
            "sign.request",
            [
                "|",
                ["attachment_ids", "in", [attachment_id]],
                ["completed_document_attachment_ids", "in", [attachment_id]],
            ],
            fields=["id"],
            limit=1,
        )
    )


def attachment_with_data(odoo: Any, attachment_id: int, include_data: bool) -> Dict[str, Any]:
    meta = attachment_metadata(odoo, attachment_id)
    meta["id"] = attachment_id
    if include_data:
        if int(meta.get("file_size") or 0) > max_attachment_bytes():
            raise ValueError("attachment exceeds the configured download cap")
        rows = odoo.read_records("ir.attachment", [attachment_id], ["datas"])
        meta["datas"] = rows[0].get("datas") if rows else None
    return meta
