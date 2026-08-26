"""
Script pra carregar a árvore oficial de categorização (xlsx com colunas Tipo
de Produto/Departamento/Categoria/Subcategoria, em qualquer combinação de
maiúsculas/minúsculas) na tabela `categorias` do Postgres.

A chave natural (tipo_produto, departamento, categoria, subcategoria) é
estável: carga nova faz upsert — insere folha nova, reativa a que voltou
no xlsx, desativa (ativo=false) a que saiu. Não usa TRUNCATE, pra não
quebrar FK de produtos/de-paras que apontam pra categorias.id.

Uso:
    python carregar_categorias.py criar-tabela
    python carregar_categorias.py carregar "Arvore de Categorização Att.xlsx"

Requer Postgres rodando (docker-compose up -d) e psycopg2-binary instalado.
"""

import argparse

import pandas as pd
import psycopg2.extras

from db import conectar
from dominios import parse_tipo_produto

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS categorias (
    id            SERIAL PRIMARY KEY,
    tipo_produto  TEXT NOT NULL REFERENCES tipos_produto(codigo),
    departamento  TEXT NOT NULL,
    categoria     TEXT NOT NULL,
    subcategoria  TEXT NOT NULL,
    ativo         BOOLEAN NOT NULL DEFAULT true
);
CREATE UNIQUE INDEX IF NOT EXISTS categorias_chave_natural ON categorias (
    tipo_produto, departamento, categoria, subcategoria
);
"""

COLUNAS_ESPERADAS = {
    "tipo de produto": "tipo_produto",
    "departamento": "departamento",
    "categoria": "categoria",
    "subcategoria": "subcategoria",
}


def garantir_schema(cur):
    cur.execute(SCHEMA_SQL)


def criar_tabela():
    with conectar() as conn:
        with conn.cursor() as cur:
            garantir_schema(cur)
        conn.commit()
    print("Tabela criada/confirmada: categorias.")


def _normalizar_colunas(df):
    renomeadas = {}
    for coluna in df.columns:
        chave = coluna.strip().lower()
        if chave not in COLUNAS_ESPERADAS:
            raise ValueError(
                f"coluna inesperada no xlsx: {coluna!r} - esperado uma de "
                f"{sorted(COLUNAS_ESPERADAS)}"
            )
        renomeadas[coluna] = COLUNAS_ESPERADAS[chave]
    return df.rename(columns=renomeadas)


def carregar_categorias(caminho_xlsx):
    """
    Lê o xlsx da árvore oficial e faz upsert pela chave natural. Linhas que
    saíram do arquivo ficam com ativo=false (id preservado).
    """
    df = _normalizar_colunas(pd.read_excel(caminho_xlsx))
    tuplas = [
        (
            parse_tipo_produto(str(row["tipo_produto"]).strip())
            or str(row["tipo_produto"]).strip(),
            str(row["departamento"]).strip(),
            str(row["categoria"]).strip(),
            str(row["subcategoria"]).strip(),
        )
        for _, row in df.iterrows()
    ]
    if any(t[0] not in ("medicamento", "nao_medicamento") for t in tuplas):
        raise ValueError(
            "tipo de produto no xlsx precisa ser Medicamento ou Não Medicamento"
        )

    with conectar() as conn:
        with conn.cursor() as cur:
            garantir_schema(cur)
            if tuplas:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO categorias
                        (tipo_produto, departamento, categoria, subcategoria, ativo)
                    VALUES %s
                    ON CONFLICT (tipo_produto, departamento, categoria, subcategoria)
                    DO UPDATE SET ativo = true
                    """,
                    [(t[0], t[1], t[2], t[3], True) for t in tuplas],
                )
                cur.execute(
                    """
                    UPDATE categorias SET ativo = false
                    WHERE ativo = true
                      AND NOT (tipo_produto, departamento, categoria, subcategoria) IN %s
                    """,
                    (tuple(tuplas),),
                )
            else:
                cur.execute("UPDATE categorias SET ativo = false WHERE ativo = true")
        conn.commit()
    print(
        f"{len(tuplas)} categoria(s) vigentes em {caminho_xlsx!r} "
        "(upsert; folhas fora do xlsx ficaram inativas)."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("criar-tabela", help="Cria a tabela categorias se nao existir")

    p_carregar = sub.add_parser(
        "carregar",
        help="Upsert da arvore oficial a partir do xlsx (preserva ids)",
    )
    p_carregar.add_argument("arquivo", help="Caminho do xlsx da arvore de categorizacao")

    args = parser.parse_args()

    if args.comando == "criar-tabela":
        criar_tabela()
    elif args.comando == "carregar":
        carregar_categorias(args.arquivo)


if __name__ == "__main__":
    main()
