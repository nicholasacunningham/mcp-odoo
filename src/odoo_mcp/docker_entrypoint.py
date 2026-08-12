"""Docker entrypoint that preserves local stdio while securing remote HTTP."""

from __future__ import annotations

import os
import sys


def main() -> int:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport in {"streamable-http", "sse"}:
        from .remote_entrypoint import main as remote_main

        remote_main()
        return 0

    from .__main__ import main as cli_main

    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
