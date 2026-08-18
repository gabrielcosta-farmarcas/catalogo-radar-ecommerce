"""
Script pra carregar a tabela da ABCFarma (ex: "ABC FARMA AGOSTO.xlsx") na
tabela `abcfarma_medicamentos` do Postgres - segunda fonte oficial de
medicamento, ao lado da CMED (ver abcfarma.py e carregar_cmed.py).

Uso:
    python carregar_abcfarma.py criar-tabela
    python carregar_abcfarma.py carregar "ABC FARMA AGOSTO.xlsx"

Requer Postgres rodando (docker-compose up -d) e psycopg2-binary instalado.
"""

import argparse

import pandas as pd
import psycopg2.extras

from cmed import normalizar_ean
from db import conectar

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS abcfarma_medicamentos (
    codigo_produto      TEXT PRIMARY KEY,
    ean                 TEXT,
    descricao_produto   TEXT NOT NULL,
    apresentacao        TEXT,
    laboratorio         TEXT NOT NULL,
    registro_anvisa     TEXT,
    tipo_medicamento    TEXT,
    principio_ativo     TEXT,
    produto_referencia  TEXT,
    ggrem               TEXT,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_abcfarma_medicamentos_ean ON abcfarma_medicamentos (ean);
"""


def criar_tabela():
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
    print("Tabela criada/confirmada: abcfarma_medicamentos.")


def _limpar_texto(valor):
    """Normaliza célula do xlsx: NaN e placeholders tipo '-' viram None."""
    if pd.isna(valor):
        return None
    texto = str(valor).strip()
    if texto in ("", "-"):
        return None
    return texto


def _limpar_ean(valor):
    """Como _limpar_texto, mas também normaliza o EAN (ver cmed.normalizar_ean)."""
    texto = _limpar_texto(valor)
    if texto is None:
        return None
    return normalizar_ean(texto) or None


def _linha_para_tupla(row):
    return (
        _limpar_texto(row["CÓDIGO DO PRODUTO"]),
        _limpar_ean(row["CÓDIGO DE BARRAS (EAN)"]),
        _limpar_texto(row["DESCRIÇÃO DO PRODUTO"]),
        _limpar_texto(row["APRESENTAÇÃO DO PRODUTO"]),
        _limpar_texto(row["NOME DO LABORATÓRIO"]),
        _limpar_texto(row["REGISTRO ANVISA"]),
        _limpar_texto(row["TIPO DE MEDICAMENTO"]),
        _limpar_texto(row["PRINCÍPIO ATIVO"]),
        _limpar_texto(row["PRODUTO REFERÊNCIA"]),
        _limpar_texto(row["GGREM"]),
    )


def carregar_medicamentos(caminho_xlsx):
    """
    Lê o xlsx da ABCFarma e insere na tabela abcfarma_medicamentos, ignorando
    códigos de produto já existentes (idempotente - pode rodar de novo sem
    duplicar). codigo_produto (não o EAN) é a chave primária porque o mesmo
    EAN aparece em mais de uma linha na base bruta (~96 casos vistos na
    planilha de agosto/2026 - reformulação/atualização de cadastro mantendo
    o mesmo código de barras).
    """
    df = pd.read_excel(caminho_xlsx)
    tuplas = [_linha_para_tupla(row) for _, row in df.iterrows()]

    with conectar() as conn:
        with conn.cursor() as cur:
            resultado = psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO abcfarma_medicamentos (
                    codigo_produto, ean, descricao_produto, apresentacao,
                    laboratorio, registro_anvisa, tipo_medicamento,
                    principio_ativo, produto_referencia, ggrem
                )
                VALUES %s
                ON CONFLICT (codigo_produto) DO NOTHING
                RETURNING codigo_produto
                """,
                tuplas,
                fetch=True,
            )
            inseridos = len(resultado)
        conn.commit()
    print(f"{inseridos} medicamento(s) novo(s) inserido(s) de {len(df)} no arquivo.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("criar-tabela", help="Cria a tabela abcfarma_medicamentos se nao existir")

    p_carregar = sub.add_parser(
        "carregar", help="Carrega dados de um xlsx da ABCFarma pra tabela abcfarma_medicamentos"
    )
    p_carregar.add_argument("arquivo", help='Caminho do xlsx (ex: "ABC FARMA AGOSTO.xlsx")')

    args = parser.parse_args()

    if args.comando == "criar-tabela":
        criar_tabela()
    elif args.comando == "carregar":
        carregar_medicamentos(args.arquivo)


if __name__ == "__main__":
    main()
