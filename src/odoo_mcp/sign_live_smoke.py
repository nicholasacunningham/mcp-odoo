"""Temporary live Odoo Sign document smoke test.

The test uploads a harmless PDF, creates and reads a draft ``sign.send.request``,
then deletes both draft and attachment. It never calls ``create_request`` and
therefore never sends email or initiates a signer ceremony.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List

TEST_FILENAME = "OdooSign MCP Test 2026-08-14.pdf"
TEST_SUBJECT = "OdooSign MCP Test 2026-08-14 - Draft Only"
TEST_PDF_BASE64 = "JVBERi0xLjMKJZOMi54gUmVwb3J0TGFiIEdlbmVyYXRlZCBQREYgZG9jdW1lbnQgKG9wZW5zb3VyY2UpCjEgMCBvYmoKPDwKL0YxIDIgMCBSIC9GMiAzIDAgUiAvRjMgNCAwIFIKPj4KZW5kb2JqCjIgMCBvYmoKPDwKL0Jhc2VGb250IC9IZWx2ZXRpY2EgL0VuY29kaW5nIC9XaW5BbnNpRW5jb2RpbmcgL05hbWUgL0YxIC9TdWJ0eXBlIC9UeXBlMSAvVHlwZSAvRm9udAo+PgplbmRvYmoKMyAwIG9iago8PAovQmFzZUZvbnQgL0hlbHZldGljYS1Cb2xkIC9FbmNvZGluZyAvV2luQW5zaUVuY29kaW5nIC9OYW1lIC9GMiAvU3VidHlwZSAvVHlwZTEgL1R5cGUgL0ZvbnQKPj4KZW5kb2JqCjQgMCBvYmoKPDwKL0Jhc2VGb250IC9IZWx2ZXRpY2EtT2JsaXF1ZSAvRW5jb2RpbmcgL1dpbkFuc2lFbmNvZGluZyAvTmFtZSAvRjMgL1N1YnR5cGUgL1R5cGUxIC9UeXBlIC9Gb250Cj4+CmVuZG9iago1IDAgb2JqCjw8Ci9Db250ZW50cyA5IDAgUiAvTWVkaWFCb3ggWyAwIDAgNjEyIDc5MiBdIC9QYXJlbnQgOCAwIFIgL1Jlc291cmNlcyA8PAovRm9udCAxIDAgUiAvUHJvY1NldCBbIC9QREYgL1RleHQgL0ltYWdlQiAvSW1hZ2VDIC9JbWFnZUkgXQo+PiAvUm90YXRlIDAgL1RyYW5zIDw8Cgo+PiAKICAvVHlwZSAvUGFnZQo+PgplbmRvYmoKNiAwIG9iago8PAovUGFnZU1vZGUgL1VzZU5vbmUgL1BhZ2VzIDggMCBSIC9UeXBlIC9DYXRhbG9nCj4+CmVuZG9iago3IDAgb2JqCjw8Ci9BdXRob3IgKGFub255bW91cykgL0NyZWF0aW9uRGF0ZSAoRDoyMDI2MDgxNDExNTkzMSswMCcwMCcpIC9DcmVhdG9yIChhbm9ueW1vdXMpIC9LZXl3b3JkcyAoKSAvTW9kRGF0ZSAoRDoyMDI2MDgxNDExNTkzMSswMCcwMCcpIC9Qcm9kdWNlciAoUmVwb3J0TGFiIFBERiBMaWJyYXJ5IC0gXChvcGVuc291cmNlXCkpIAogIC9TdWJqZWN0ICh1bnNwZWNpZmllZCkgL1RpdGxlIChPZG9vU2lnbiBNQ1AgVGVzdCkgL1RyYXBwZWQgL0ZhbHNlCj4+CmVuZG9iago4IDAgb2JqCjw8Ci9Db3VudCAxIC9LaWRzIFsgNSAwIFIgXSAvVHlwZSAvUGFnZXMKPj4KZW5kb2JqCjkgMCBvYmoKPDwKL0ZpbHRlciBbIC9BU0NJSTg1RGVjb2RlIC9GbGF0ZURlY29kZSBdIC9MZW5ndGggNDYxCj4+CnN0cmVhbQpHYXNiVj8jUDxLJ1NjKVQoJTo3bWAoRE5xb1pnL2Y5RGxzIWMpcWZgPFM9JCY6cFJqTig2K2Y/MSpMQVIkJGksa1t0aGksMDVdOjkhMWpAPztbVVlpW1wsaFMrbStTS1ZsO0shZCsnIVtyM29Fc0xkPkZMIVY3RUA0S01HXzJxXnI3blptbjJRLCxqOUZXbSNxS19VNEYzXl4kLjJSNU1UMmtXOVRYMkQrK250N0tuX0VLVDE+Wk1UODkiUlE6bTA7N0dTMiFyM21gTzBtPi89O3NcTVJXVUdzSGJsNiZVLmwrWyVcc3ElXSxgVTZSMWZgNy9jOj5wPlVzcV1XRlFSQyRLJko/W0MvRHVJSFFpXEdbYChKXE9OLEZzKS83ZUpfWSpjbFFwUzl1ZGFTXWMrJjs5RDo9IzZWWW5FTXA7UGhhTUtKPSdPLitPSFssRSNGbEhWYmZbYjUjbys5TS5SKmlXZzY9Y0tJXUpaUGRWITMqRCFMUiRBJVUzZTg5cC1CJiElVChtJDZBZFxnJEs4cVxWNFltQz5jayVlW1VeVm5uOUwhaXRHXCpGMTN0IWI5SExmbSZQSWxuKykxZDhLbU5VW25UOlVcRyZ+PmVuZHN0cmVhbQplbmRvYmoKeHJlZgowIDEwCjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDA2MSAwMDAwMCBuIAowMDAwMDAwMTEyIDAwMDAwIG4gCjAwMDAwMDAyMTkgMDAwMDAgbiAKMDAwMDAwMDMzMSAwMDAwMCBuIAowMDAwMDAwNDQ2IDAwMDAwIG4gCjAwMDAwMDA2MzkgMDAwMDAgbiAKMDAwMDAwMDcwNyAwMDAwMCBuIAowMDAwMDAwOTc3IDAwMDAwIG4gCjAwMDAwMDEwMzYgMDAwMDAgbiAKdHJhaWxlcgo8PAovSUQgCls8MTBiNWY2NzcyYWMzMzQ0NjA4ZDkxYWJlODA1OTRlNmI+PDEwYjVmNjc3MmFjMzM0NDYwOGQ5MWFiZTgwNTk0ZTZiPl0KJSBSZXBvcnRMYWIgZ2VuZXJhdGVkIFBERiBkb2N1bWVudCAtLSBkaWdlc3QgKG9wZW5zb3VyY2UpCgovSW5mbyA3IDAgUgovUm9vdCA2IDAgUgovU2l6ZSAxMAo+PgpzdGFydHhyZWYKMTU4NwolJUVPRgo="

_STATUS: Dict[str, Any] = {"status": "not_run", "mode": "draft_only", "sent": False}


def get_document_smoke_status() -> Dict[str, Any]:
    return dict(_STATUS)


def _created_ids(result: Any) -> List[int]:
    if isinstance(result, int):
        return [result]
    if isinstance(result, list):
        return [int(value) for value in result if isinstance(value, int) or str(value).isdigit()]
    return []


def _create(odoo: Any, model: str, values: Dict[str, Any]) -> Any:
    if getattr(odoo, "transport", "") == "json2":
        return odoo.execute_method(model, "create", vals_list=[values])
    return odoo.execute_method(model, "create", [values])


def _unlink(odoo: Any, model: str, record_id: int) -> None:
    if getattr(odoo, "transport", "") == "json2":
        odoo.execute_method(model, "unlink", ids=[record_id])
    else:
        odoo.execute_method(model, "unlink", [record_id])


def run_document_smoke(odoo: Any) -> None:
    """Create, verify, then clean up a draft Sign document without sending it."""
    global _STATUS
    attachment_id: int | None = None
    wizard_id: int | None = None
    try:
        attachment_result = _create(
            odoo,
            "ir.attachment",
            {
                "name": TEST_FILENAME,
                "datas": TEST_PDF_BASE64,
                "mimetype": "application/pdf",
                "type": "binary",
            },
        )
        attachment_ids = _created_ids(attachment_result)
        if not attachment_ids:
            raise RuntimeError("Odoo did not return an attachment ID")
        attachment_id = attachment_ids[0]

        wizard_result = _create(
            odoo,
            "sign.send.request",
            {
                "attachment_ids": [[6, 0, [attachment_id]]],
                "subject": TEST_SUBJECT,
                "reminder_enabled": False,
                "reminder": 0,
                "certificate_reference": False,
                "set_sign_order": False,
            },
        )
        wizard_ids = _created_ids(wizard_result)
        if not wizard_ids:
            raise RuntimeError("Odoo did not return a Sign draft wizard ID")
        wizard_id = wizard_ids[0]

        verification = odoo.read_records(
            "sign.send.request",
            [wizard_id],
            ["subject", "attachment_ids"],
        )
        if not verification:
            raise RuntimeError("Sign draft wizard could not be read back")
        linked = [int(value) for value in verification[0].get("attachment_ids") or []]
        if attachment_id not in linked:
            raise RuntimeError("Sign draft wizard is not linked to the test PDF")

        _unlink(odoo, "sign.send.request", wizard_id)
        wizard_id = None
        _unlink(odoo, "ir.attachment", attachment_id)
        attachment_id = None

        _STATUS = {
            "status": "ok",
            "mode": "draft_only",
            "sent": False,
            "created": True,
            "read_back": True,
            "cleaned": True,
        }
        print(
            "Odoo Sign document smoke: ok (PDF uploaded; draft created/read/cleaned; sent=false)",
            file=sys.stderr,
            flush=True,
        )
    except Exception as exc:
        for model, record_id in (("sign.send.request", wizard_id), ("ir.attachment", attachment_id)):
            if record_id is not None:
                try:
                    _unlink(odoo, model, record_id)
                except Exception:
                    pass
        _STATUS = {
            "status": "failed",
            "mode": "draft_only",
            "sent": False,
            "error_type": type(exc).__name__,
        }
        print(
            f"Odoo Sign document smoke: failed ({type(exc).__name__})",
            file=sys.stderr,
            flush=True,
        )
