"""
Consulta a tabela `medicamentos_tarjados` (carregada por carregar_tarjados.py
a partir da base curada pelo time de negócio, alimentada diariamente) por
EAN, e seu mapeamento de categoria em `mapeamento_categoria_tarjado` (ver
carregar_mapeamento_tarjado.py).

Quarta camada de referência, entre ABCFarma e IQVIA (ver worker() em
enrich_com_crawler.py). Ao contrário de CMED/ABCFarma, não confirma tarja -
a base só diz TIPO DE PRODUTO (RX/CONTROLADO/NÃO INFORMADO/NÃO MEDICAMENTO),
sem distinguir Tarja Vermelha de Preta - mesma régua de validação humana já
usada pra IQVIA como RX.
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
    "produto", "laboratorio", "tipo_produto", "secao", "subsecao",
    "setor_nec_aberto", "nec1", "nec2", "nec3", "molecula",
)


def conectar():
    return psycopg2.connect(**DB_CONFIG)


def buscar_produto_tarjado(ean):
    """
    Busca o EAN (normalizado) na tabela medicamentos_tarjados. Retorna um
    dict com os campos da base, ou None se o EAN não está lá - nesse caso o
    chamador deve seguir o fluxo normal (IQVIA/crawler/Claude).
    """
    ean_normalizado = normalizar_ean(ean)
    if not ean_normalizado:
        return None

    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(CAMPOS)} FROM medicamentos_tarjados WHERE ean = %s LIMIT 1",
                (ean_normalizado,),
            )
            row = cur.fetchone()

    if row is None:
        return None
    return dict(zip(CAMPOS, row))


def buscar_categoria_mapeada(secao, nec1, nec2, nec3):
    """
    Consulta mapeamento_categoria_tarjado (ver carregar_mapeamento_tarjado.py)
    por uma combinação de taxonomia já revisada por humano. Retorna
    {"categoria_id", "tipo_produto", "departamento", "categoria",
    "subcategoria"} - tipo_produto vem da própria árvore oficial em
    `categorias`, não do TIPO DE PRODUTO bruto da origem: a combinação pode
    resolver pro ramo Não Medicamento mesmo vindo de um produto RX/CONTROLADO
    (caso confirmado visto na validação do time - ver
    mapeamento_categoria_tarjado). Retorna None se a combinação não existir
    ou ainda não tiver sido revisada - nesse caso o chamador cai no fluxo de
    IA.
    """
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.categoria_id, c.tipo_produto, c.departamento, c.categoria, c.subcategoria
                FROM mapeamento_categoria_tarjado m
                LEFT JOIN categorias c ON c.id = m.categoria_id
                WHERE coalesce(m.secao, '') = coalesce(%s, '')
                  AND coalesce(m.nec1, '') = coalesce(%s, '')
                  AND coalesce(m.nec2, '') = coalesce(%s, '')
                  AND coalesce(m.nec3, '') = coalesce(%s, '')
                  AND m.revisado = true
                """,
                (secao, nec1, nec2, nec3),
            )
            row = cur.fetchone()

    if row is None:
        return None
    return {
        "categoria_id": row[0],
        "tipo_produto": row[1],
        "departamento": row[2],
        "categoria": row[3],
        "subcategoria": row[4],
    }
