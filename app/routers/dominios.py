from __future__ import annotations

from fastapi import APIRouter

from app.repos import dominios as repo
from app.schemas.referencias import DominiosCadastro

router = APIRouter(prefix="/dominios", tags=["dominios"])


@router.get("", response_model=DominiosCadastro)
def listar() -> DominiosCadastro:
    return DominiosCadastro.model_validate(repo.listar())
