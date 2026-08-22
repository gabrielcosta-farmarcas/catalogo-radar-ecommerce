"""
Consulta a tabela `iqvia_produtos` (carregada por carregar_iqvia.py a partir
do catálogo IQVIA enviado por um parceiro) por EAN.

Terceira fonte de referência, ao lado da CMED (cmed.py) e da ABCFarma
(abcfarma.py). Diferente das outras duas, cobre não-medicamento também
(cosmético, alimento, etc.) e classifica isso direto via SETOR_NEC_ABERTO -
ver eh_medicamento()/eh_generico(). Não tem, porém, tarja detalhada
(vermelha vs preta) nem registro_ms: só distingue "precisa receita" (RX) de
"não precisa" (MIP) de "não é medicamento" (NAO_MEDICAMENTO_*).
"""

import os

import psycopg2

from cmed import normalizar_ean

DB_CONFIG = {
    "host": os.environ.get("PG_HOST", "localhost"),
    "port": os.environ.get("PG_PORT", "5433"),
    "user": os.environ.get("PG_USER", "cadastro"),
    "password": os.environ.get("PG_PASSWORD", "cadastro"),
    "dbname": os.environ.get("PG_DB", "cadastro_produtos"),
}

CAMPOS = (
    "fcc", "brand", "descricao_longa", "descricao_fabricante",
    "descricao_corporacao", "setor_nec_aberto", "sub_cat1", "sub_cat2",
    "sub_cat3", "sub_cat4", "area_farmacia", "molecula",
)

# SETOR_NEC_ABERTO: RX_* = precisa receita (Rx), MIP_* = medicamento isento
# de prescrição (venda livre), NAO_MEDICAMENTO_* = não é medicamento (PEC =
# perfumaria/cosmético, PAC = cuidado ao paciente, NTR = alimento/nutrição,
# OTC aqui = "venda livre não-medicamento", ex: suplemento/fitoterápico sem
# registro de medicamento - não confundir com MIP, que É medicamento OTC)
SETORES_MEDICAMENTO = {
    "RX_PROMOVIDO", "RX_GENERICO", "RX_TRADE",
    "MIP_MARCA", "MIP_TRADE", "MIP_GENERICO",
}

ORIGEM_IQVIA = "iqvia"


def conectar():
    return psycopg2.connect(**DB_CONFIG)


def eh_medicamento(setor_nec_aberto):
    return setor_nec_aberto in SETORES_MEDICAMENTO


def eh_generico(setor_nec_aberto):
    return bool(setor_nec_aberto) and setor_nec_aberto.endswith("_GENERICO")


def precisa_receita(setor_nec_aberto):
    """True (RX) / False (MIP) / None (não é medicamento - pergunta não
    se aplica)."""
    if not eh_medicamento(setor_nec_aberto):
        return None
    return setor_nec_aberto.startswith("RX_")


def buscar_produto_iqvia(ean):
    """
    Busca o EAN (normalizado) na tabela iqvia_produtos. Retorna um dict com
    os campos do catálogo, ou None se o EAN não está na base - nesse caso o
    chamador deve seguir o fluxo normal (CMED/ABCFarma/crawler/Claude).

    EAN é único na base (confirmado na carga - ao contrário da ABCFarma, não
    precisa de critério de desempate).
    """
    ean_normalizado = normalizar_ean(ean)
    if not ean_normalizado:
        return None

    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(CAMPOS)} FROM iqvia_produtos WHERE ean = %s LIMIT 1",
                (ean_normalizado,),
            )
            row = cur.fetchone()

    if row is None:
        return None
    return dict(zip(CAMPOS, row))


def buscar_categoria_mapeada(tipo_cadastro, area_farmacia, sub_cat1, sub_cat2, sub_cat3, sub_cat4):
    """
    Consulta mapeamento_categoria_iqvia (ver mapear_categorias_iqvia.py) por
    uma combinação de taxonomia já revisada por humano. Retorna
    {"departamento", "categoria", "subcategoria"} (valores podem ser None,
    se a revisão confirmou que nenhuma categoria da árvore se aplica) ou
    None se a combinação não existir na tabela ou ainda não tiver sido
    revisada - nesse caso o chamador deve cair no fluxo normal (perguntar
    pro Claude a partir da dica bruta, como já faz hoje).
    """
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT departamento, categoria, subcategoria
                FROM mapeamento_categoria_iqvia
                WHERE tipo_cadastro = %s
                  AND coalesce(area_farmacia, '') = coalesce(%s, '')
                  AND coalesce(sub_cat1, '') = coalesce(%s, '')
                  AND coalesce(sub_cat2, '') = coalesce(%s, '')
                  AND coalesce(sub_cat3, '') = coalesce(%s, '')
                  AND coalesce(sub_cat4, '') = coalesce(%s, '')
                  AND revisado_humanamente = true
                """,
                (tipo_cadastro, area_farmacia, sub_cat1, sub_cat2, sub_cat3, sub_cat4),
            )
            row = cur.fetchone()

    if row is None:
        return None
    return {"departamento": row[0], "categoria": row[1], "subcategoria": row[2]}
