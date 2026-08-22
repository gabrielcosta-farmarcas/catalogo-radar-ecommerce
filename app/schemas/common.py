from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Tokens(BaseModel):
    utilizados: int = 0
    cache_gravados: int = 0
    cache_lidos: int = 0


class Pagina(BaseModel, Generic[T]):
    total: int
    limit: int
    offset: int
    itens: list[T]


class ErroDetalhe(BaseModel):
    code: str
    message: str
    details: Optional[list] = None


class ErroResposta(BaseModel):
    error: ErroDetalhe
