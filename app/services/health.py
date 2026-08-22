from __future__ import annotations

from app.config import settings
from app.db import ping
from app.schemas.referencias import Health


def status() -> Health:
    detalhe = None
    postgres = False
    try:
        postgres = ping()
    except Exception as exc:
        detalhe = str(exc)
    return Health(
        ok=postgres,
        postgres=postgres,
        anthropic_key=settings.anthropic_configurada(),
        detalhe=detalhe,
    )
