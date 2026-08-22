from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Query, status

from app.config import settings
from app.ean import EanPath
from app.schemas.common import Pagina
from app.schemas.enriquecimento import EnriquecerResposta
from app.schemas.produto import HistoricoLista, ProdutoCriar, ProdutoDetalhe, ProdutoResumo
from app.schemas.referencias import Estatisticas
from app.services import enriquecimento as enriquecimento_service
from app.services import produtos as produtos_service

router = APIRouter(prefix="/produtos", tags=["produtos"])

FaseFiltro = Literal["pendente", "concluido", "nao_localizado"]


@router.get("", response_model=Pagina[ProdutoResumo])
def listar(
    fase: Optional[FaseFiltro] = None,
    validacao_humana: Optional[bool] = None,
    q: Optional[str] = Query(None, description="Busca em EAN, nome e título"),
    limit: int = Query(settings.pagina_padrao, ge=1, le=settings.pagina_maxima),
    offset: int = Query(0, ge=0),
) -> Pagina[ProdutoResumo]:
    return produtos_service.listar(
        fase=fase,
        validacao_humana=validacao_humana,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/estatisticas", response_model=Estatisticas)
def estatisticas() -> Estatisticas:
    return produtos_service.estatisticas()


@router.post("", response_model=ProdutoDetalhe, status_code=status.HTTP_201_CREATED)
def criar(pedido: ProdutoCriar) -> ProdutoDetalhe:
    return produtos_service.criar(pedido)


@router.get("/{ean}", response_model=ProdutoDetalhe)
def obter(ean: EanPath) -> ProdutoDetalhe:
    return produtos_service.obter(ean)


@router.get("/{ean}/historico", response_model=HistoricoLista)
def historico(ean: EanPath) -> HistoricoLista:
    return produtos_service.historico(ean)


@router.post("/{ean}/enriquecer", response_model=EnriquecerResposta)
def enriquecer_cadastrado(
    ean: EanPath,
    sem_verificar_tarja: bool = False,
    verify_images: bool = False,
) -> EnriquecerResposta:
    return enriquecimento_service.enriquecer_cadastrado(
        ean,
        sem_verificar_tarja=sem_verificar_tarja,
        verify_images=verify_images,
    )
