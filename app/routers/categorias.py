from __future__ import annotations

from fastapi import APIRouter

from app.schemas.referencias import ArvoreCategorias
from app.services import categorias as categorias_service

router = APIRouter(prefix="/categorias", tags=["categorias"])


@router.get("", response_model=ArvoreCategorias)
def arvore() -> ArvoreCategorias:
    return categorias_service.arvore()
