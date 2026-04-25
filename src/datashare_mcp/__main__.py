from __future__ import annotations

import asyncio
import sys

from .config import Settings
from .server import build_server


async def _serve(settings: Settings) -> None:
    mcp, client = build_server(settings)
    try:
        await mcp.run_async()
    finally:
        await client.aclose()


def main() -> None:
    try:
        settings = Settings()
    except Exception as e:
        print(f"datashare-mcp: configuration error: {e}", file=sys.stderr)
        sys.exit(2)
    asyncio.run(_serve(settings))


if __name__ == "__main__":
    main()
