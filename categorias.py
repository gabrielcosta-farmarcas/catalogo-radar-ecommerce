"""
Consulta a tabela `categorias` (carregada por carregar_categorias.py a partir
da árvore oficial de categorização em xlsx) por tipo de produto.

Módulo separado de propósito - conexão própria, não importa db.py (que é
isolado pro fluxo de batch experimental), mesmo padrão de cmed.py. Usado por
enrich_produtos.py para montar o texto da árvore (por ramo) mandado ao modelo
e para validar se a categorização devolvida existe de fato na árvore oficial.

A folha da árvore é identificada por `categorias.id`. Textos
(departamento/categoria/subcategoria) são atributos dessa linha; o produto
e os de-paras apontam para o id, não copiam o trio.
"""

import os

import psycopg2
import psycopg2.extras

from dominios import nome_tipo_produto

DB_CONFIG = {
    "host": os.environ.get("PG_HOST", "localhost"),
    "port": os.environ.get("PG_PORT", "5433"),
    "user": os.environ.get("PG_USER", "cadastro"),
    "password": os.environ.get("PG_PASSWORD", "cadastro"),
    "dbname": os.environ.get("PG_DB", "cadastro_produtos"),
}

_INDICE = None


def conectar():
    return psycopg2.connect(**DB_CONFIG)


def _linha_ativa_sql():
    """ativo pode ainda não existir em banco antigo, antes da migração."""
    return "COALESCE(ativo, true) = true"


def carregar_indice(forcar=False):
    """
    Lê as folhas ativas de `categorias` e devolve um dict:
    - combinacoes: set (tipo_produto, departamento, categoria, subcategoria)
    - arvores_por_ramo: dict tipo_produto -> texto compacto do ramo (prompts)
    - ids_por_combinacao: a mesma tupla -> categorias.id
    - por_id: id -> dict da folha (incluindo tipo_produto e os três textos)
    Retorna estruturas vazias se a tabela não existir / estiver vazia.
    """
    global _INDICE
    if _INDICE is not None and not forcar:
        return _INDICE

    vazio = {
        "combinacoes": set(),
        "arvores_por_ramo": {},
        "ids_por_combinacao": {},
        "por_id": {},
    }
    try:
        with conectar() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, tipo_produto, departamento, categoria, subcategoria
                    FROM categorias
                    WHERE {_linha_ativa_sql()}
                    ORDER BY id
                    """
                )
                linhas = cur.fetchall()
    except psycopg2.errors.UndefinedTable:
        _INDICE = vazio
        return _INDICE
    except psycopg2.errors.UndefinedColumn:
        try:
            with conectar() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, tipo_produto, departamento, categoria, subcategoria "
                        "FROM categorias ORDER BY id"
                    )
                    linhas = cur.fetchall()
        except psycopg2.errors.UndefinedTable:
            _INDICE = vazio
            return _INDICE

    combinacoes = set()
    ids_por_combinacao = {}
    por_id = {}
    por_ramo = {}
    for cid, tipo, depto, cat, sub in linhas:
        chave = (tipo, depto, cat, sub)
        combinacoes.add(chave)
        ids_por_combinacao[chave] = cid
        por_id[cid] = {
            "id": cid,
            "tipo_produto": tipo,
            "departamento": depto,
            "categoria": cat,
            "subcategoria": sub,
        }
        por_ramo.setdefault(tipo, {}).setdefault(depto, {}).setdefault(cat, []).append(sub)

    arvores_por_ramo = {}
    for tipo, deptos in por_ramo.items():
        nome_ramo = nome_tipo_produto(tipo) or tipo
        lines = [f"[RAMO {nome_ramo} - não é nenhum campo de saída]"]
        for depto, categorias_ in deptos.items():
            lines.append(f'  departamento="{depto}"')
            for cat, subs in categorias_.items():
                lines.append(f'    categoria="{cat}" subcategorias: {"; ".join(subs)}')
        arvores_por_ramo[tipo] = "\n".join(lines)

    _INDICE = {
        "combinacoes": combinacoes,
        "arvores_por_ramo": arvores_por_ramo,
        "ids_por_combinacao": ids_por_combinacao,
        "por_id": por_id,
    }
    return _INDICE


def carregar_arvore():
    """
    Compatível com o contrato antigo: (combinacoes, arvores_por_ramo).
    Só folhas ativas entram na árvore mandada ao modelo e na validação.
    """
    indice = carregar_indice()
    return indice["combinacoes"], indice["arvores_por_ramo"]


def resolver_id(tipo_produto, departamento, categoria, subcategoria):
    """
    Traduz o trio textual (+ tipo) no id da folha oficial, ou None se a
    combinação não existir (ou algum campo vier vazio).
    """
    if not (tipo_produto and departamento and categoria and subcategoria):
        return None
    return carregar_indice()["ids_por_combinacao"].get(
        (tipo_produto, departamento, categoria, subcategoria)
    )


def por_id(categoria_id):
    """Folha oficial pelo id, ou None. Não filtra ativo: produto já gravado
    continua resolvendo o nome mesmo se a folha foi desativada na carga."""
    if not categoria_id:
        return None
    achou = carregar_indice()["por_id"].get(int(categoria_id))
    if achou:
        return achou
    try:
        with conectar() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, tipo_produto, departamento, categoria, subcategoria
                    FROM categorias WHERE id = %s
                    """,
                    (int(categoria_id),),
                )
                row = cur.fetchone()
    except (psycopg2.errors.UndefinedTable, TypeError, ValueError):
        return None
    return dict(row) if row else None


def aplicar_folha(data, categoria_id=None):
    """
    Grava categoria_id em data e copia os textos oficiais da folha
    (departamento/categoria/subcategoria) pra o restante do pipeline
    (frase obrigatória, prompts, histórico) continuar lendo nomes.
    categoria_id None zera os três textos.
    """
    if not categoria_id:
        data["categoria_id"] = None
        data["departamento"] = None
        data["categoria"] = None
        data["subcategoria"] = None
        return data
    folha = por_id(categoria_id)
    if not folha:
        data["categoria_id"] = None
        data["departamento"] = None
        data["categoria"] = None
        data["subcategoria"] = None
        return data
    data["categoria_id"] = folha["id"]
    data["departamento"] = folha["departamento"]
    data["categoria"] = folha["categoria"]
    data["subcategoria"] = folha["subcategoria"]
    return data
