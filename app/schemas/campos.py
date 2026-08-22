from app.schemas.produto import CadastroProduto

CAMPOS_CADASTRO = tuple(CadastroProduto.model_fields.keys())

COLUNAS_RESUMO = (
    "id",
    "ean",
    "nome_produto",
    "titulo",
    "marca",
    "tipo_cadastro",
    "tarja",
    "fase_atual",
    "origem_enriquecimento",
    "precisa_validacao_humana",
    "departamento",
    "categoria",
    "subcategoria",
    "atualizado_em",
)
