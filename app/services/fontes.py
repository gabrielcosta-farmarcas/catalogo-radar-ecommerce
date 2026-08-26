from __future__ import annotations

import abcfarma
import cmed
import iqvia
import tarjados

from app.jsonutil import json_limpo
from app.schemas.referencias import FontesEan


def consultar(ean: str) -> FontesEan:
    return FontesEan(
        ean=ean,
        cmed=json_limpo(cmed.buscar_medicamento_anvisa(ean)),
        abcfarma=json_limpo(abcfarma.buscar_medicamento_abcfarma(ean)),
        tarjados=json_limpo(tarjados.buscar_produto_tarjado(ean)),
        iqvia=json_limpo(iqvia.buscar_produto_iqvia(ean)),
    )
