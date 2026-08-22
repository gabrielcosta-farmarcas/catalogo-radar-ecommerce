from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Health(BaseModel):
    ok: bool
    postgres: bool
    anthropic_key: bool
    detalhe: Optional[str] = None


class AnthropicCredito(BaseModel):
    ok: bool
    credito: Optional[bool] = None
    chave_configurada: bool
    codigo: Literal[
        "ok",
        "sem_credito",
        "chave_ausente",
        "chave_invalida",
        "indisponivel",
    ]
    mensagem: str
    http_status_anthropic: Optional[int] = None


class Estatisticas(BaseModel):
    por_fase: dict[str, int]
    validacao_humana: int
    total: int


class FontesEan(BaseModel):
    model_config = ConfigDict(extra="allow")

    ean: str
    cmed: Optional[dict[str, Any]] = None
    abcfarma: Optional[dict[str, Any]] = None
    iqvia: Optional[dict[str, Any]] = None


class CategoriaNo(BaseModel):
    nome: str
    subcategorias: list[str] = Field(default_factory=list)


class DepartamentoNo(BaseModel):
    nome: str
    categorias: list[CategoriaNo] = Field(default_factory=list)


class RamoCategorias(BaseModel):
    tipo_produto: str
    departamentos: list[DepartamentoNo] = Field(default_factory=list)


class ArvoreCategorias(BaseModel):
    ramos: list[RamoCategorias]
