import re

from typing import Annotated

from fastapi import Depends, Path

from app.errors import ValidationAppError

EAN_RE = re.compile(r"^\d{8,14}$")


def validar_ean(ean: str) -> str:
    valor = str(ean or "").strip()
    if not EAN_RE.fullmatch(valor):
        raise ValidationAppError("EAN deve ter entre 8 e 14 dígitos.")
    return valor


def _ean_path(ean: str = Path(..., min_length=8, max_length=14, pattern=r"^\d{8,14}$")) -> str:
    return validar_ean(ean)


EanPath = Annotated[str, Depends(_ean_path)]
