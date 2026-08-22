"""Acesso a dados da tabela produtos. Sem regra de HTTP nem de pipeline."""

from __future__ import annotations

from app.db import dict_cursor, get_conn
from app.schemas.campos import COLUNAS_RESUMO


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
            cur.execute("SELECT * FROM produtos WHERE ean = %s", (ean,))
            row = cur.fetchone()
    return dict(row) if row else None


def listar_historico(ean: str) -> list[dict]:
    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT * FROM produtos_historico
                WHERE ean = %s
                ORDER BY versionado_em DESC, id DESC
                """,
                (ean,),
            )
            return [dict(row) for row in cur.fetchall()]


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
        filtros.append("fase_atual = %s")
        params.append(fase)
    if validacao_humana is True:
        filtros.append("precisa_validacao_humana = 'Sim'")
    elif validacao_humana is False:
        filtros.append("(precisa_validacao_humana IS NULL OR precisa_validacao_humana <> 'Sim')")
    if q:
        filtros.append(
            "(ean ILIKE %s OR nome_produto ILIKE %s OR COALESCE(titulo, '') ILIKE %s)"
        )
        like = f"%{q}%"
        params.extend([like, like, like])

    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    colunas = ", ".join(COLUNAS_RESUMO)

    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(f"SELECT count(*) AS total FROM produtos {where}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"""
                SELECT {colunas}
                FROM produtos
                {where}
                ORDER BY atualizado_em DESC, ean
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            itens = [dict(row) for row in cur.fetchall()]
    return total, itens


def estatisticas() -> dict:
    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                "SELECT fase_atual, count(*) AS qtd FROM produtos GROUP BY fase_atual"
            )
            por_fase = {row["fase_atual"]: row["qtd"] for row in cur.fetchall()}
            cur.execute(
                "SELECT count(*) AS qtd FROM produtos WHERE precisa_validacao_humana = 'Sim'"
            )
            validacao = cur.fetchone()["qtd"]
            cur.execute("SELECT count(*) AS qtd FROM produtos")
            total = cur.fetchone()["qtd"]
    return {"por_fase": por_fase, "validacao_humana": validacao, "total": total}
