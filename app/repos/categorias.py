from __future__ import annotations

from app.db import get_conn
from app.errors import DependencyUnavailable
import psycopg2


def listar_linhas() -> list[tuple]:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, tipo_produto, departamento, categoria, subcategoria
                    FROM categorias
                    WHERE COALESCE(ativo, true) = true
                    ORDER BY tipo_produto, departamento, categoria, subcategoria
                    """
                )
                return cur.fetchall()
    except psycopg2.errors.UndefinedTable as exc:
        raise DependencyUnavailable(
            "Tabela categorias ainda não existe. Rode o carregamento da árvore."
        ) from exc
