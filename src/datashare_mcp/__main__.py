from __future__ import annotations

import asyncio
import sys

from pydantic import ValidationError

from .config import Settings
from .server import build_server


async def _serve(settings: Settings) -> None:
    mcp, client = build_server(settings)
    try:
        # Pinned, not defaulted. With transport=None FastMCP resolves it from its own
        # pydantic-settings model, whose env_prefix is FASTMCP_ *and* whose env_file is
        # ".env" in the process working directory. Either source setting
        # FASTMCP_TRANSPORT=http would turn this stdio server into an unauthenticated
        # HTTP listener on FASTMCP_HOST/FASTMCP_PORT, handing the operator's Datashare
        # access to any local process. Note the asymmetry: this project's own Settings
        # declares no env_file, so a .env never supplies DATASHARE_* — but it does supply
        # FASTMCP_*.
        await mcp.run_async(transport="stdio")
    finally:
        await client.aclose()


def _render_config_error(e: Exception) -> str:
    """Describe a settings failure without echoing any resolved value.

    A pydantic ValidationError renders `input_value=<the whole input dict>`, which for
    this model carries DATASHARE_API_KEY. Only locations and error types are safe.
    """
    if isinstance(e, ValidationError):
        lines = []
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "<model>"
            lines.append(f"  DATASHARE_{loc.upper()}: {err['type']}")
        return "\n".join(["invalid settings:", *lines])
    return type(e).__name__


def main() -> None:
    try:
        settings = Settings()
    except Exception as e:
        print(f"datashare-mcp: configuration error: {_render_config_error(e)}", file=sys.stderr)
        sys.exit(2)
    asyncio.run(_serve(settings))


if __name__ == "__main__":
    main()
