"""Start the shop: python -m kyo

Creates the database tables, seeds the catalog if needed, and serves the application.
"""

from __future__ import annotations

import os

import uvicorn

from .app import create_app
from .store.seed import seed_catalog


def main() -> None:
    app = create_app()
    if os.environ.get("KYO_SEED", "1") == "1":
        seed_catalog(app.state.sessions)
    uvicorn.run(
        app,
        host=os.environ.get("KYO_HOST", "127.0.0.1"),
        port=int(os.environ.get("KYO_PORT", "8000")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
