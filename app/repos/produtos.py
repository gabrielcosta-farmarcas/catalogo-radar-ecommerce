"""Acesso a dados da tabela produtos. Sem regra de HTTP nem de pipeline."""

from __future__ import annotations

from app.db import dict_cursor, get_conn
from app.schemas.campos import COLUNAS_RESUMO
from dominios import normalizar_cadastro


def upsert(conn, ean: str, nome_produto: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO produtos (ean, nome_produto)
            VALUES (%s, %s)
            ON CONFLICT (ean) DO UPDATE
              SET nome_produto = EXCLUDED.nome_produto,
                  atualizado_em = now()
            """,
            (ean, nome_produto),
        )


def inserir(ean: str, nome_produto: str) -> dict | None:
    """Insere se o EAN for novo. Devolve a linha, ou None se já existia."""
    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO produtos (ean, nome_produto)
                VALUES (%s, %s)
                ON CONFLICT (ean) DO NOTHING
                RETURNING *
                """,
                (ean, nome_produto),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None


def obter_por_ean(ean: str) -> dict | None:
    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT p.*, c.departamento, c.categoria, c.subcategoria
                FROM produtos p
                LEFT JOIN categorias c ON c.id = p.categoria_id
                WHERE p.ean = %s
                """,
                (ean,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return normalizar_cadastro(dict(row))


def listar_historico(ean: str) -> list[dict]:
    """
    O snapshot de cada versão vive em `dados` (JSONB) - desempacota aqui pra
    devolver o mesmo formato achatado de antes (fase_resultado, titulo,
    marca, ... soltos), sem precisar mudar o schema Pydantic ProdutoHistorico.
    """
    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT id, produto_id, ean, dados, versionado_em
                FROM produtos_historico
                WHERE ean = %s
                ORDER BY versionado_em DESC, id DESC
                """,
                (ean,),
            )
            linhas = []
            for row in cur.fetchall():
                row = dict(row)
                dados = normalizar_cadastro(dict(row.pop("dados") or {}))
                linhas.append({**dados, **row})
            return linhas


def listar(
    *,
    fase: str | None,
    validacao_humana: bool | None,
    q: str | None,
    limit: int,
    offset: int,
) -> tuple[int, list[dict]]:
    filtros = []
    params: list = []

    if fase:
        filtros.append("p.fase_atual = %s")
        params.append(fase)
    if validacao_humana is True:
        filtros.append("p.precisa_validacao_humana = true")
    elif validacao_humana is False:
        filtros.append("(p.precisa_validacao_humana IS NULL OR p.precisa_validacao_humana = false)")
    if q:
        filtros.append(
            "(p.ean ILIKE %s OR p.nome_produto ILIKE %s OR COALESCE(p.titulo, '') ILIKE %s)"
        )
        like = f"%{q}%"
        params.extend([like, like, like])

    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    colunas = ", ".join(COLUNAS_RESUMO)

    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(f"SELECT count(*) AS total FROM produtos p {where}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"""
                SELECT {colunas}
                FROM produtos p
                LEFT JOIN categorias c ON c.id = p.categoria_id
                {where}
                ORDER BY p.atualizado_em DESC, p.ean
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            itens = [normalizar_cadastro(dict(row)) for row in cur.fetchall()]
    return total, itens


def estatisticas() -> dict:
    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                "SELECT fase_atual, count(*) AS qtd FROM produtos GROUP BY fase_atual"
            )
            por_fase = {row["fase_atual"]: row["qtd"] for row in cur.fetchall()}
            cur.execute(
                "SELECT count(*) AS qtd FROM produtos WHERE precisa_validacao_humana = true"
            )
            validacao = cur.fetchone()["qtd"]
            cur.execute("SELECT count(*) AS qtd FROM produtos")
            total = cur.fetchone()["qtd"]
    return {"por_fase": por_fase, "validacao_humana": validacao, "total": total}
