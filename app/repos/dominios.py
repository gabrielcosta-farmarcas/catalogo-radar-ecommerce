from __future__ import annotations

from app.db import dict_cursor, get_conn


def listar() -> dict:
    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute("SELECT codigo, nome FROM tipos_produto ORDER BY codigo")
            tipos = [dict(row) for row in cur.fetchall()]
            cur.execute("SELECT codigo, nome FROM tarjas ORDER BY codigo")
            tarjas = [dict(row) for row in cur.fetchall()]
            cur.execute("SELECT codigo, nome FROM origens_enriquecimento ORDER BY codigo")
            origens = [dict(row) for row in cur.fetchall()]
    return {"tipos_produto": tipos, "tarjas": tarjas, "origens_enriquecimento": origens}
