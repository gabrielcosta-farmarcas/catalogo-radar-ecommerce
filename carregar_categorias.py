"""
Script pra carregar a árvore oficial de categorização (xlsx com colunas Tipo
de Produto/Departamento/Categoria/Subcategoria, em qualquer combinação de
maiúsculas/minúsculas) na tabela `categorias` do Postgres.

A tabela é sempre substituída por completo a cada carga (TRUNCATE + insert) -
categorias não é uma tabela de acúmulo histórico como produtos/medicamentos,
é a árvore oficial vigente; carregar um xlsx novo deve refletir exatamente
esse arquivo, sem misturar categoria antiga que foi removida/renomeada.

Uso:
    python carregar_categorias.py criar-tabela
    python carregar_categorias.py carregar "Arvore de Categorização Att.xlsx"

Requer Postgres rodando (docker-compose up -d) e psycopg2-binary instalado.
"""

import argparse

import pandas as pd
import psycopg2.extras

from db import conectar

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS categorias (
    id            SERIAL PRIMARY KEY,
    tipo_produto  TEXT NOT NULL,
    departamento  TEXT NOT NULL,
    categoria     TEXT NOT NULL,
    subcategoria  TEXT NOT NULL
);
"""

# aceita cabeçalho em qualquer combinação de maiúsculas/minúsculas (o arquivo
# mais recente do time veio em CAIXA ALTA, versões anteriores vieram em Title
# Case) - normaliza pelo nome da coluna em minúsculas, não pela grafia exata
COLUNAS_ESPERADAS = {
    "tipo de produto": "tipo_produto",
    "departamento": "departamento",
    "categoria": "categoria",
    "subcategoria": "subcategoria",
}


def criar_tabela():
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
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
    Lê o xlsx da árvore oficial e substitui o conteúdo inteiro da tabela
    categorias (TRUNCATE + insert) - ver docstring do módulo.
    """
    df = _normalizar_colunas(pd.read_excel(caminho_xlsx))
    tuplas = [
        (
            str(row["tipo_produto"]).strip(),
            str(row["departamento"]).strip(),
            str(row["categoria"]).strip(),
            str(row["subcategoria"]).strip(),
        )
        for _, row in df.iterrows()
    ]

    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE categorias RESTART IDENTITY")
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO categorias (tipo_produto, departamento, categoria, subcategoria) VALUES %s",
                tuplas,
            )
        conn.commit()
    print(f"{len(tuplas)} categoria(s) carregada(s) de {caminho_xlsx!r} (tabela substituída por completo).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("criar-tabela", help="Cria a tabela categorias se nao existir")

    p_carregar = sub.add_parser(
        "carregar", help="Substitui o conteudo da tabela categorias pelo xlsx informado"
    )
    p_carregar.add_argument("arquivo", help="Caminho do xlsx da arvore de categorizacao")

    args = parser.parse_args()

    if args.comando == "criar-tabela":
        criar_tabela()
    elif args.comando == "carregar":
        carregar_categorias(args.arquivo)


if __name__ == "__main__":
    main()
