"""
Script pra carregar a tabela CMED (cmed_carga.xlsx) na tabela
`anvisa_medicamentos` do Postgres.

Uso:
    python carregar_cmed.py criar-tabela
    python carregar_cmed.py carregar cmed_carga.xlsx

Requer Postgres rodando (docker-compose up -d) e psycopg2-binary instalado.
"""

import argparse

import pandas as pd
import psycopg2.extras

from cmed import normalizar_ean
from db import conectar

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS anvisa_medicamentos (
    codigo_ggrem        TEXT PRIMARY KEY,
    substancia          TEXT NOT NULL,
    cnpj                TEXT NOT NULL,
    laboratorio         TEXT NOT NULL,
    registro            TEXT NOT NULL,
    ean_1               TEXT,
    ean_2               TEXT,
    ean_3               TEXT,
    produto             TEXT NOT NULL,
    apresentacao        TEXT,
    classe_terapeutica  TEXT,
    tipo_produto        TEXT,
    tarja               TEXT,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_anvisa_medicamentos_ean_1 ON anvisa_medicamentos (ean_1);
"""


def criar_tabela():
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
    print("Tabela criada/confirmada: anvisa_medicamentos.")


def _limpar_texto(valor):
    """Normaliza célula do xlsx: NaN e placeholders tipo '-' viram None."""
    if pd.isna(valor):
        return None
    texto = str(valor).strip()
    if texto in ("", "-"):
        return None
    return texto


def _limpar_ean(valor):
    """
    Como _limpar_texto, mas também normaliza o EAN removendo zero(s) à
    esquerda (algumas linhas da CMED trazem EAN 2/3 como texto com zero à
    esquerda, ex: "0313533008178") e trata "0"/"000...0" como placeholder de
    "sem código de barras informado" (visto em KYMRIAH, ADACNE PEROX, NUCALA)
    - mesma normalização usada nas buscas (ver cmed.normalizar_ean), pra a
    tabela já nascer no formato certo em vez de depender só da consulta.
    """
    texto = _limpar_texto(valor)
    if texto is None:
        return None
    return normalizar_ean(texto) or None


def _linha_para_tupla(row):
    return (
        _limpar_texto(row["CÓDIGO GGREM"]),
        _limpar_texto(row["SUBSTÂNCIA"]),
        _limpar_texto(row["CNPJ"]),
        _limpar_texto(row["LABORATÓRIO"]),
        _limpar_texto(row["REGISTRO"]),
        _limpar_ean(row["EAN 1"]),
        _limpar_ean(row["EAN 2"]),
        _limpar_ean(row["EAN 3"]),
        _limpar_texto(row["PRODUTO"]),
        _limpar_texto(row["APRESENTAÇÃO"]),
        _limpar_texto(row["CLASSE TERAPÊUTICA"]),
        _limpar_texto(row["TIPO DE PRODUTO (STATUS DO PRODUTO)"]),
        _limpar_texto(row["TARJA"]),
    )


def carregar_medicamentos(caminho_xlsx):
    """
    Le o xlsx da CMED e insere na tabela anvisa_medicamentos, ignorando
    codigos GGREM ja existentes (idempotente - pode rodar de novo sem
    duplicar).
    """
    df = pd.read_excel(caminho_xlsx)
    tuplas = [_linha_para_tupla(row) for _, row in df.iterrows()]

    with conectar() as conn:
        with conn.cursor() as cur:
            resultado = psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO anvisa_medicamentos (
                    codigo_ggrem, substancia, cnpj, laboratorio, registro,
                    ean_1, ean_2, ean_3, produto, apresentacao,
                    classe_terapeutica, tipo_produto, tarja
                )
                VALUES %s
                ON CONFLICT (codigo_ggrem) DO NOTHING
                RETURNING codigo_ggrem
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

    sub.add_parser("criar-tabela", help="Cria a tabela anvisa_medicamentos se nao existir")

    p_carregar = sub.add_parser("carregar", help="Carrega dados de um xlsx da CMED pra tabela anvisa_medicamentos")
    p_carregar.add_argument("arquivo", help="Caminho do xlsx (cmed_carga.xlsx)")

    args = parser.parse_args()

    if args.comando == "criar-tabela":
        criar_tabela()
    elif args.comando == "carregar":
        carregar_medicamentos(args.arquivo)


if __name__ == "__main__":
    main()
