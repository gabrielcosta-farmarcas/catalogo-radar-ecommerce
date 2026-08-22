from __future__ import annotations

from app.errors import ConflictError, NotFoundError
from app.ean import validar_ean
from app.jsonutil import json_limpo
from app.repos import produtos as repo
from app.schemas.common import Pagina
from app.schemas.produto import (
    HistoricoLista,
    ProdutoCriar,
    ProdutoDetalhe,
    ProdutoHistorico,
    ProdutoResumo,
)
from app.schemas.referencias import Estatisticas


def _detalhe(row: dict) -> ProdutoDetalhe:
    return ProdutoDetalhe.model_validate(json_limpo(row))


def obter(ean: str) -> ProdutoDetalhe:
    row = repo.obter_por_ean(ean)
    if not row:
        raise NotFoundError(f"EAN {ean} não está cadastrado.")
    return _detalhe(row)


def criar(pedido: ProdutoCriar) -> ProdutoDetalhe:
    ean = validar_ean(pedido.ean)
    row = repo.inserir(ean, pedido.nome_produto.strip())
    if not row:
        raise ConflictError(
            f"EAN {ean} já está cadastrado.",
            code="ean_ja_cadastrado",
        )
    return _detalhe(row)


def listar(
    *,
    fase: str | None,
    validacao_humana: bool | None,
    q: str | None,
    limit: int,
    offset: int,
) -> Pagina[ProdutoResumo]:
    total, itens = repo.listar(
        fase=fase,
        validacao_humana=validacao_humana,
        q=q.strip() if q else None,
        limit=limit,
        offset=offset,
    )
    return Pagina[ProdutoResumo](
        total=total,
        limit=limit,
        offset=offset,
        itens=[ProdutoResumo.model_validate(json_limpo(item)) for item in itens],
    )


def historico(ean: str) -> HistoricoLista:
    if not repo.obter_por_ean(ean):
        raise NotFoundError(f"EAN {ean} não está cadastrado.")
    versoes = [
        ProdutoHistorico.model_validate(json_limpo(row))
        for row in repo.listar_historico(ean)
    ]
    return HistoricoLista(ean=ean, versoes=versoes)


def estatisticas() -> Estatisticas:
    dados = repo.estatisticas()
    return Estatisticas.model_validate(dados)
