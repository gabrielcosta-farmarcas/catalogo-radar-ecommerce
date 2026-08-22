from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

FaseProduto = Literal["pendente", "concluido", "nao_localizado"]


class CadastroProduto(BaseModel):
    """Campos de conteúdo do cadastro - o que o frontend edita/exibe na ficha."""

    titulo: Optional[str] = None
    marca: Optional[str] = None
    fabricante: Optional[str] = None
    tipo_cadastro: Optional[str] = None
    registro_ms: Optional[str] = None
    generico: Optional[str] = None
    tarja: Optional[str] = None
    precisa_retencao_receita: Optional[str] = None
    principios_ativos: Optional[str] = None
    descricao_curta: Optional[str] = None
    frase_obrigatoria: Optional[str] = None
    departamento: Optional[str] = None
    categoria: Optional[str] = None
    subcategoria: Optional[str] = None
    origem_categorizacao: Optional[str] = None
    imagem_url: Optional[str] = None
    pagina_produto_url: Optional[str] = None
    preco_pesquisado: Optional[str] = None
    data_pesquisa: Optional[str] = None
    origem_enriquecimento: Optional[str] = None
    confirmado_anvisa_cmed: Optional[str] = None
    precisa_validacao_humana: Optional[str] = None
    mensagem_validacao_humana: Optional[str] = None
    model: Optional[str] = None


class ProdutoResumo(BaseModel):
    """Linha da listagem - só o que a tela de fila precisa."""

    id: int
    ean: str
    nome_produto: str
    titulo: Optional[str] = None
    marca: Optional[str] = None
    tipo_cadastro: Optional[str] = None
    tarja: Optional[str] = None
    fase_atual: FaseProduto
    origem_enriquecimento: Optional[str] = None
    precisa_validacao_humana: Optional[str] = None
    departamento: Optional[str] = None
    categoria: Optional[str] = None
    subcategoria: Optional[str] = None
    atualizado_em: datetime


class ProdutoDetalhe(CadastroProduto):
    id: int
    ean: str
    nome_produto: str
    fase_atual: FaseProduto
    tokens_utilizados: int = 0
    tokens_cache_gravados: int = 0
    tokens_cache_lidos: int = 0
    criado_em: datetime
    atualizado_em: datetime


class ProdutoHistorico(CadastroProduto):
    id: int
    produto_id: int
    ean: str
    fase_resultado: str
    tokens_utilizados: int = 0
    tokens_cache_gravados: int = 0
    tokens_cache_lidos: int = 0
    versionado_em: datetime


class ProdutoCriar(BaseModel):
    ean: str = Field(..., examples=["7891150097377"])
    nome_produto: str = Field(..., min_length=1, examples=["Kit Seda Ceramidas"])


class HistoricoLista(BaseModel):
    ean: str
    versoes: list[ProdutoHistorico]
