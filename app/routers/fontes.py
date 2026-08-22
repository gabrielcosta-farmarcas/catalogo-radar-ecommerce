from __future__ import annotations

from fastapi import APIRouter

from app.ean import EanPath
from app.schemas.referencias import FontesEan
from app.services import fontes as fontes_service

router = APIRouter(prefix="/fontes", tags=["fontes"])


@router.get("/{ean}", response_model=FontesEan)
def consultar(ean: EanPath) -> FontesEan:
    """CMED, ABCFarma e IQVIA. Sem crawler e sem Claude."""
    return fontes_service.consultar(ean)
