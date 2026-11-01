"""Lancement du service en processus (python -m app)."""

from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run("app.main:application", host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
