from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.config import settings
from app.schemas.common import Tokens
from app.schemas.produto import CadastroProduto, ProdutoDetalhe


class EnriquecerPedido(BaseModel):
    ean: str = Field(..., examples=["7891150097377"])
    nome_produto: str = Field(..., min_length=1)
    salvar: bool = Field(True, description="Persiste na tabela produtos e no histórico")
    sem_verificar_tarja: bool = False
    verify_images: bool = False
    model: str = Field(default_factory=lambda: settings.modelo_padrao)


class EnriquecerResposta(BaseModel):
    ean: str
    nome_produto: str
    status: Literal["OK", "Não localizado"]
    origem: Optional[str] = None
    cadastro: Optional[CadastroProduto] = None
    tokens: Tokens
    salvo: bool
    produto: Optional[ProdutoDetalhe] = None


class JobStatus(BaseModel):
    job_id: str
    ean: str
    nome_produto: str
    status: Literal["na_fila", "rodando", "concluido", "erro"]
    etapa: str
    mensagem: str
    progresso: int
    iniciado_em: datetime
    atualizado_em: datetime
    resultado: Optional[EnriquecerResposta] = None
    erro: Optional[str] = None
