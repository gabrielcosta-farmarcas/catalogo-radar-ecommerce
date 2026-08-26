from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

FaseProduto = Literal["pendente", "concluido", "nao_localizado"]
TipoProduto = Literal["medicamento", "nao_medicamento"]
Tarja = Literal["sem_tarja", "vermelha", "preta", "nao_aplicavel"]
OrigemCategorizacao = Literal["mapeamento_iqvia", "mapeamento_cmed", "ia"]
OrigemEnriquecimento = Literal["anvisa_cmed", "abcfarma", "iqvia", "crawler", "claude"]


class CadastroProduto(BaseModel):
    """Campos de conteúdo do cadastro - o que o frontend edita/exibe na ficha."""

    titulo: Optional[str] = None
    marca: Optional[str] = None
    fabricante: Optional[str] = None
    tipo_produto: Optional[TipoProduto] = None
    registro_ms: Optional[str] = None
    generico: Optional[bool] = None
    tarja: Optional[Tarja] = None
    precisa_retencao_receita: Optional[bool] = None
    principios_ativos: Optional[str] = None
    descricao_curta: Optional[str] = None
    frase_obrigatoria: Optional[str] = None
    categoria_id: Optional[int] = None
    departamento: Optional[str] = None
    categoria: Optional[str] = None
    subcategoria: Optional[str] = None
    origem_categorizacao: Optional[OrigemCategorizacao] = None
    imagem_url: Optional[str] = None
    pagina_produto_url: Optional[str] = None
    preco_pesquisado: Optional[str] = None
    data_pesquisa: Optional[date] = None
    origem_enriquecimento: Optional[OrigemEnriquecimento] = None
    origem_referencia: Optional[str] = None
    confirmado_anvisa_cmed: Optional[bool] = None
    precisa_validacao_humana: Optional[bool] = None
    mensagem_validacao_humana: Optional[str] = None
    modelo: Optional[str] = None


class ProdutoResumo(BaseModel):
    """Linha da listagem - só o que a tela de fila precisa."""

    id: int
    ean: str
    nome_produto: str
    titulo: Optional[str] = None
    marca: Optional[str] = None
    tipo_produto: Optional[TipoProduto] = None
    tarja: Optional[Tarja] = None
    fase_atual: FaseProduto
    origem_enriquecimento: Optional[OrigemEnriquecimento] = None
    origem_referencia: Optional[str] = None
    precisa_validacao_humana: Optional[bool] = None
    categoria_id: Optional[int] = None
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
