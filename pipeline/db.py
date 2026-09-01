"""DSN único do Postgres — mesmo contrato de `app.config.settings.dsn`."""

from __future__ import annotations

import os


def dsn() -> dict:
    try:
        from app.config import settings

        return settings.dsn
    except Exception:
        return {
            "host": os.environ.get("PG_HOST", "localhost"),
            "port": os.environ.get("PG_PORT", "5433"),
            "user": os.environ.get("PG_USER", "cadastro"),
            "password": os.environ.get("PG_PASSWORD", "cadastro"),
            "dbname": os.environ.get("PG_DB", "cadastro_produtos"),
        }
