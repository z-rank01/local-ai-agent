"""Entry point for the frontend adapter API."""

from __future__ import annotations

import uvicorn

from core import config


def main() -> None:
    uvicorn.run(
        "bff.app:app",
        host=config.BFF_HOST,
        port=config.BFF_PORT,
        log_level=config.LOG_LEVEL.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()