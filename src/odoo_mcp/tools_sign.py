"""First-party Odoo Sign-only plugin registration and surface pruning."""

from __future__ import annotations

import os
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .server_core import DESTRUCTIVE_TOOL, PREVIEW_TOOL, mcp as _MCP
from .sign_live_smoke import get_document_smoke_status
from .sign_policy import SIGN_PROFILE_ENV, SIGN_MODELS, SIGN_WRITE_ENV, set_plugin_api
from .sign_tools_data import sign_contacts, sign_files, sign_health, sign_model
from .sign_tools_request import sign_action, sign_request

TOOLS = (
    (sign_health, "Verify Odoo Sign availability and Sign-only posture", PREVIEW_TOOL),
    (
        sign_model,
        "Full schema/read/search/CRUD access within the Odoo Sign model allowlist",
        DESTRUCTIVE_TOOL,
    ),
    (
        sign_contacts,
        "Search signer contacts or create a minimal signer contact",
        DESTRUCTIVE_TOOL,
    ),
    (
        sign_files,
        "Upload PDFs for Sign or download Sign-linked attachments",
        DESTRUCTIVE_TOOL,
    ),
    (
        sign_request,
        "Prepare, send, inspect, audit, and retrieve completed Sign requests",
        DESTRUCTIVE_TOOL,
    ),
    (
        sign_action,
        "Run a confirmed public Odoo Sign administrative workflow method",
        DESTRUCTIVE_TOOL,
    ),
)


@_MCP.custom_route("/sign-health", methods=["GET", "HEAD"])
async def sign_profile_health(_: Request) -> Response:
    """Public non-secret proof that the dedicated Sign plugin is loaded."""
    registry = getattr(getattr(_MCP, "_tool_manager", None), "_tools", {})
    tool_names = sorted(
        str(name) for name in registry if str(name).startswith("sign_")
    ) if isinstance(registry, dict) else []
    return JSONResponse(
        {
            "status": "ok",
            "profile": os.environ.get(SIGN_PROFILE_ENV, "").strip() or None,
            "sign_writes_enabled": os.environ.get(SIGN_WRITE_ENV, "").strip().casefold()
            in {"1", "true", "yes", "on"},
            "tool_count": len(tool_names),
            "tools": tool_names,
            "document_smoke": get_document_smoke_status(),
        }
    )


def _apply_profile() -> None:
    if os.environ.get(SIGN_PROFILE_ENV, "").strip().casefold() != "sign":
        return
    registry = getattr(getattr(_MCP, "_tool_manager", None), "_tools", None)
    if not isinstance(registry, dict):
        raise RuntimeError("could not access MCP tool registry")
    for name in list(registry):
        if not str(name).startswith("sign_"):
            del registry[name]
    for manager_name, attr in (
        ("_resource_manager", "_resources"),
        ("_resource_manager", "_templates"),
        ("_prompt_manager", "_prompts"),
    ):
        manager = getattr(_MCP, manager_name, None)
        values = getattr(manager, attr, None)
        if isinstance(values, dict):
            values.clear()
    lowlevel = getattr(_MCP, "_lowlevel_server", None)
    if lowlevel is not None:
        lowlevel.instructions = (
            "Odoo Sign-only MCP. Manage Sign data and workflows, but never forge "
            "or apply a signer signature."
        )


def register(api: Any) -> None:
    set_plugin_api(api)
    for function, description, annotations in TOOLS:
        api.tool(
            name=function.__name__,
            description=description,
            annotations=annotations,
            structured_output=True,
        )(function)
    _apply_profile()


__all__ = ["SIGN_MODELS", "register"] + [function.__name__ for function, _, _ in TOOLS]
