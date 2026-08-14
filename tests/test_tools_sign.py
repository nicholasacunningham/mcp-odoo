"""Focused policy tests for the first-party Odoo Sign profile."""

import asyncio
import base64

import pytest

from odoo_mcp import plugin_api, server, sign_policy, tools_sign


def _tool_names():
    return {tool.name for tool in asyncio.run(server.mcp.list_tools())}


def test_sign_scope_rejects_general_erp_models():
    with pytest.raises(ValueError, match="outside the Odoo Sign model allowlist"):
        sign_policy.validate_model("account.move")


def test_sign_write_protects_signer_and_completion_fields():
    with pytest.raises(ValueError, match="signer/security/audit"):
        sign_policy.validate_write_values("sign.request.item", {"signature": "forged"})
    with pytest.raises(ValueError, match="signer/security/audit"):
        sign_policy.validate_write_values("sign.request", {"state": "signed"})
    assert sign_policy.validate_write_values("sign.request.item", {"partner_id": 42}) == {
        "partner_id": 42
    }


def test_pdf_validation_requires_pdf_header():
    valid = base64.b64encode(b"%PDF-1.7\nhello").decode("ascii")
    encoded, raw = sign_policy.normalize_pdf(valid)
    assert raw.startswith(b"%PDF-")
    assert base64.b64decode(encoded).startswith(b"%PDF-")

    invalid = base64.b64encode(b"not a pdf").decode("ascii")
    with pytest.raises(ValueError, match="only PDF"):
        sign_policy.normalize_pdf(invalid)


def test_json2_helpers_use_named_arguments():
    calls = []

    class FakeClient:
        transport = "json2"

        def execute_method(self, model, method, *args, **kwargs):
            calls.append((model, method, args, kwargs))
            return True

    client = FakeClient()
    sign_policy.orm_create(client, "sign.template", {"name": "Test"})
    sign_policy.orm_write(client, "sign.template", [7], {"name": "Renamed"})
    sign_policy.orm_unlink(client, "sign.item", [9])

    assert calls[0][3] == {"vals_list": [{"name": "Test"}]}
    assert calls[1][3] == {"ids": [7], "vals": {"name": "Renamed"}}
    assert calls[2][3] == {"ids": [9]}


def test_sign_profile_prunes_generic_surface(monkeypatch):
    mcp = server.mcp
    tools = mcp._tool_manager._tools
    resources = mcp._resource_manager._resources
    templates = mcp._resource_manager._templates
    prompts = mcp._prompt_manager._prompts
    snapshots = (dict(tools), dict(resources), dict(templates), dict(prompts))
    old_name = mcp._lowlevel_server.name
    old_instructions = mcp._lowlevel_server.instructions

    monkeypatch.setenv("ODOO_MCP_PROFILE", "sign")
    try:
        tools_sign.register(plugin_api)
        assert _tool_names() == {
            "sign_health",
            "sign_model",
            "sign_contacts",
            "sign_files",
            "sign_request",
            "sign_action",
        }
        assert resources == {}
        assert templates == {}
        assert prompts == {}
        assert mcp.name == "OdooSign MCP"
        assert "never forge" in (mcp.instructions or "")
    finally:
        for registry, snapshot in zip(
            (tools, resources, templates, prompts), snapshots, strict=True
        ):
            registry.clear()
            registry.update(snapshot)
        mcp._lowlevel_server.name = old_name
        mcp._lowlevel_server.instructions = old_instructions
