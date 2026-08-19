"""
Consulta a tabela `categorias` (carregada por carregar_categorias.py a partir
da árvore oficial de categorização em xlsx) por tipo de produto.

Módulo separado de propósito - conexão própria, não importa db.py (que é
isolado pro fluxo de batch experimental), mesmo padrão de cmed.py. Usado por
enrich_produtos.py para montar o texto da árvore (por ramo) mandado ao modelo
e para validar se a categorização devolvida existe de fato na árvore oficial.
"""

import os

import psycopg2

DB_CONFIG = {
    "host": os.environ.get("PG_HOST", "localhost"),
    "port": os.environ.get("PG_PORT", "5433"),
    "user": os.environ.get("PG_USER", "cadastro"),
    "password": os.environ.get("PG_PASSWORD", "cadastro"),
    "dbname": os.environ.get("PG_DB", "cadastro_produtos"),
}


def conectar():
    return psycopg2.connect(**DB_CONFIG)


def carregar_arvore():
    """
    Lê a tabela `categorias` inteira e retorna (combinacoes, arvores_por_ramo):
    - combinacoes: set de tuplas (tipo_produto, departamento, categoria,
      subcategoria) - usado para validar se a combinação que o modelo
      devolveu realmente existe na árvore oficial (ver
      enrich_produtos.validar_categorizacao).
    - arvores_por_ramo: dict tipo_produto -> texto compacto só daquele ramo,
      no mesmo formato usado nos prompts de categorização/formatação.
    Retorna (set(), {}) se a tabela estiver vazia ou não existir ainda (o
    script ainda funciona, mas sem a taxonomia oficial).
    """
    try:
        with conectar() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tipo_produto, departamento, categoria, subcategoria "
                    "FROM categorias ORDER BY id"
                )
                linhas = cur.fetchall()
    except psycopg2.errors.UndefinedTable:
        return set(), {}

    combinacoes = set()
    por_ramo = {}
    for tipo, depto, cat, sub in linhas:
        por_ramo.setdefault(tipo, {}).setdefault(depto, {}).setdefault(cat, []).append(sub)
        combinacoes.add((tipo, depto, cat, sub))

    arvores_por_ramo = {}
    for tipo, deptos in por_ramo.items():
        lines = [f"[RAMO {tipo} - não é nenhum campo de saída]"]
        for depto, categorias_ in deptos.items():
            lines.append(f'  departamento="{depto}"')
            for cat, subs in categorias_.items():
                lines.append(f'    categoria="{cat}" subcategorias: {"; ".join(subs)}')
        arvores_por_ramo[tipo] = "\n".join(lines)

    return combinacoes, arvores_por_ramo
