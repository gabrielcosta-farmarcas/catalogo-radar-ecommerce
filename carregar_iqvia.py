"""
Script pra carregar o catálogo IQVIA (CATALOGO_DE_PRODUTOS_<periodo>_AJUSTADO.xlsx,
aba "IQVIA") na tabela `iqvia_produtos` do Postgres - terceira fonte de
referência, ao lado da CMED e da ABCFarma (ver iqvia.py, cmed.py e
abcfarma.py). Base rica enviada por um parceiro/distribuidor: ~260 mil
produtos (medicamento e não-medicamento), com classificação regulatória
(SETOR_NEC_ABERTO) e taxonomia própria de categoria (SUB_CAT1..4,
AREA_FARMACIA) - cobre não-medicamento também, diferente de CMED/ABCFarma.
EAN é a chave primária (não FCC) - linha sem EAN é descartada na carga.

Uso:
    python carregar_iqvia.py criar-tabela
    python carregar_iqvia.py carregar CATALOGO_DE_PRODUTOS_202607_AJUSTADO.xlsx

Requer Postgres rodando (docker-compose up -d) e psycopg2-binary instalado.
"""

import argparse

import pandas as pd
import psycopg2.extras

from cmed import normalizar_ean
from db import conectar

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS iqvia_produtos (
    ean                     TEXT PRIMARY KEY,
    fcc                     TEXT NOT NULL,
    brand                   TEXT NOT NULL,
    descricao_longa         TEXT NOT NULL,
    descricao_fabricante    TEXT,
    descricao_corporacao    TEXT,
    setor_nec_aberto        TEXT NOT NULL,
    sub_cat1                TEXT,
    sub_cat2                TEXT,
    sub_cat3                TEXT,
    sub_cat4                TEXT,
    area_farmacia           TEXT,
    molecula                TEXT,
    criado_em               TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def criar_tabela():
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
    print("Tabela criada/confirmada: iqvia_produtos.")


def _limpar_texto(valor):
    """Normaliza célula do xlsx: NaN e placeholders tipo 'N/I' (não
    informado - convenção do IQVIA, visto em MOLECULA e AREA_FARMACIA)
    viram None."""
    if pd.isna(valor):
        return None
    texto = str(valor).strip()
    if texto in ("", "N/I", "-"):
        return None
    return texto


def _limpar_ean(valor):
    """Como _limpar_texto, mas também normaliza o EAN (ver cmed.normalizar_ean).
    Retorna None se não der pra normalizar - o chamador descarta a linha
    nesse caso (EAN é a chave de busca; sem EAN a linha não serve pra
    consulta e não entra na tabela)."""
    texto = _limpar_texto(valor)
    if texto is None:
        return None
    # vem como float do Excel (ex: 7898150000000.0) - int() antes de
    # normalizar_ean evita "7898150000000.0" virar lixo na normalização
    return normalizar_ean(int(float(texto))) or None


def _linha_para_tupla(row):
    return (
        _limpar_ean(row["EAN"]),
        str(int(row["FCC"])),
        _limpar_texto(row["BRAND"]),
        _limpar_texto(row["DESCRICAO_LONGA"]),
        _limpar_texto(row["DESCRICAO_FABRICANTE"]),
        _limpar_texto(row["DESCRICAO_CORPORACAO"]),
        _limpar_texto(row["SETOR_NEC_ABERTO"]),
        _limpar_texto(row["SUB_CAT1"]),
        _limpar_texto(row["SUB_CAT2"]),
        _limpar_texto(row["SUB_CAT3"]),
        _limpar_texto(row["SUB_CAT4"]),
        _limpar_texto(row["AREA_FARMACIA"]),
        _limpar_texto(row["MOLECULA"]),
    )


def carregar_produtos(caminho_xlsx):
    """
    Lê a aba "IQVIA" do xlsx e insere na tabela iqvia_produtos, ignorando EAN
    já existentes (idempotente - pode rodar de novo sem duplicar, inclusive
    pra carregar um catálogo de mês seguinte por cima do anterior). Linha sem
    EAN (produto sem código de barras cadastrado no parceiro) é descartada -
    EAN é a chave de busca deste projeto, sem ele a linha não serve.
    """
    df = pd.read_excel(caminho_xlsx, sheet_name="IQVIA")
    tuplas = [t for t in (_linha_para_tupla(row) for _, row in df.iterrows()) if t[0]]
    sem_ean = len(df) - len(tuplas)

    with conectar() as conn:
        with conn.cursor() as cur:
            resultado = psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO iqvia_produtos (
                    ean, fcc, brand, descricao_longa, descricao_fabricante,
                    descricao_corporacao, setor_nec_aberto, sub_cat1,
                    sub_cat2, sub_cat3, sub_cat4, area_farmacia, molecula
                )
                VALUES %s
                ON CONFLICT (ean) DO NOTHING
                RETURNING ean
                """,
                tuplas,
                fetch=True,
            )
            inseridos = len(resultado)
        conn.commit()
    print(f"{inseridos} produto(s) novo(s) inserido(s) de {len(df)} no arquivo.")
    if sem_ean:
        print(f"{sem_ean} linha(s) descartada(s) por não ter EAN.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("criar-tabela", help="Cria a tabela iqvia_produtos se nao existir")

    p_carregar = sub.add_parser(
        "carregar", help='Carrega dados de um xlsx do IQVIA (aba "IQVIA") pra tabela iqvia_produtos'
    )
    p_carregar.add_argument(
        "arquivo", help="Caminho do xlsx (ex: CATALOGO_DE_PRODUTOS_202607_AJUSTADO.xlsx)"
    )

    args = parser.parse_args()

    if args.comando == "criar-tabela":
        criar_tabela()
    elif args.comando == "carregar":
        carregar_produtos(args.arquivo)


if __name__ == "__main__":
    main()
