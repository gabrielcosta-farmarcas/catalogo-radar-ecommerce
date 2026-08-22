"""Ponto de entrada compatível: `uvicorn api:app` ou `uvicorn app.main:app`."""

from app.main import app

__all__ = ["app"]
