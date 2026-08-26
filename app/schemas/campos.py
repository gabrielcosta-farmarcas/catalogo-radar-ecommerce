from app.schemas.produto import CadastroProduto

CAMPOS_CADASTRO = tuple(CadastroProduto.model_fields.keys())

COLUNAS_RESUMO = (
    "p.id",
    "p.ean",
    "p.nome_produto",
    "p.titulo",
    "p.marca",
    "p.tipo_produto",
    "p.tarja",
    "p.fase_atual",
    "p.origem_enriquecimento",
    "p.precisa_validacao_humana",
    "p.categoria_id",
    "c.departamento",
    "c.categoria",
    "c.subcategoria",
    "p.atualizado_em",
)
