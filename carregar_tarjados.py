"""
Script pra carga inicial da base de medicamentos tarjados ("base de tarjados
- ecommerce.xlsx", aba "Base Completa") na tabela `medicamentos_tarjados` do
Postgres - quarta fonte de referência, ao lado da CMED, ABCFarma e IQVIA (ver
cmed.py, abcfarma.py e iqvia.py). Curadoria própria do time (Farmarcas),
atualizada diariamente, restrita a itens com tarja/controle (TIPO DE PRODUTO
em RX/CONTROLADO/NÃO INFORMADO/NÃO MEDICAMENTO - já vem sem OTC).

Depois dessa carga inicial, a manutenção linha a linha (inserir/atualizar/
apagar EAN) é feita por um CRUD à parte, não por este script - por isso a
carga aqui é só insert-idempotente (ON CONFLICT DO NOTHING), mesmo padrão de
carregar_iqvia.py / carregar_cmed.py / carregar_abcfarma.py: roda de novo sem
duplicar, mas não sobrescreve edição feita pelo CRUD.

Uso:
    python carregar_tarjados.py criar-tabela
    python carregar_tarjados.py carregar "base de tarjados - ecommerce.xlsx"

Requer Postgres rodando (docker-compose up -d) e psycopg2-binary instalado.
"""

import argparse

import pandas as pd
import psycopg2.extras

from cmed import normalizar_ean
from db import conectar

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS medicamentos_tarjados (
    id                BIGSERIAL PRIMARY KEY,
    ean               TEXT UNIQUE NOT NULL,
    ncm               TEXT,
    grupo             TEXT,
    produto           TEXT NOT NULL,
    laboratorio       TEXT,
    holding           TEXT,
    divisao           TEXT,
    tipo_produto      TEXT NOT NULL,
    secao             TEXT,
    subsecao          TEXT,
    setor_nec_aberto  TEXT,
    nec1              TEXT,
    nec2              TEXT,
    nec3              TEXT,
    molecula          TEXT,
    data_alteracao    DATE,
    data_inclusao     DATE,
    criado_em         TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em     TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def criar_tabela():
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
    print("Tabela criada/confirmada: medicamentos_tarjados.")


def _limpar_texto(valor):
    """Normaliza célula do xlsx: NaN vira None."""
    if pd.isna(valor):
        return None
    texto = str(valor).strip()
    return texto or None


def _limpar_ean(valor):
    """Como _limpar_texto, mas também normaliza o EAN (ver cmed.normalizar_ean)."""
    texto = _limpar_texto(valor)
    if texto is None:
        return None
    return normalizar_ean(texto) or None


def _limpar_ncm(valor):
    """Como _limpar_texto, mas remove o ".0" que o pandas acrescenta em
    coluna numérica com célula vazia (NCM vem como float nesse xlsx)."""
    texto = _limpar_texto(valor)
    if texto is None:
        return None
    return texto[:-2] if texto.endswith(".0") else texto


def _limpar_data(valor):
    if pd.isna(valor):
        return None
    return pd.Timestamp(valor).date()


def _linha_para_tupla(row):
    return (
        _limpar_ean(row["EAN"]),
        _limpar_ncm(row["NCM"]),
        _limpar_texto(row["GRUPO"]),
        _limpar_texto(row["PRODUTO"]),
        _limpar_texto(row["LABORATORIO"]),
        _limpar_texto(row["HOLDING"]),
        _limpar_texto(row["DIVISAO"]),
        _limpar_texto(row["TIPO DE PRODUTO"]),
        _limpar_texto(row["SECAO"]),
        _limpar_texto(row["SUBSECAO"]),
        _limpar_texto(row["SETOR NEC ABERTO"]),
        _limpar_texto(row["NEC 1"]),
        _limpar_texto(row["NEC 2"]),
        _limpar_texto(row["NEC 3"]),
        _limpar_texto(row["MOLECULA - A-Z"]),
        _limpar_data(row["DATA DE ALTERACÃO"]),
        _limpar_data(row["DATA DE INCLUSÃO"]),
    )


def carregar_tarjados(caminho_xlsx):
    """
    Lê a aba "Base Completa" do xlsx e insere na tabela medicamentos_tarjados,
    ignorando EAN já existente (idempotente - pode rodar de novo sem
    duplicar). Linha sem EAN (ou sem PRODUTO/TIPO DE PRODUTO, campos
    obrigatórios) é descartada.
    """
    df = pd.read_excel(caminho_xlsx, sheet_name="Base Completa")
    df.columns = [str(c).strip() for c in df.columns]

    tuplas = []
    descartadas = 0
    for _, row in df.iterrows():
        tupla = _linha_para_tupla(row)
        if not tupla[0] or not tupla[3] or not tupla[7]:
            descartadas += 1
            continue
        tuplas.append(tupla)

    with conectar() as conn:
        with conn.cursor() as cur:
            resultado = psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO medicamentos_tarjados (
                    ean, ncm, grupo, produto, laboratorio, holding, divisao,
                    tipo_produto, secao, subsecao, setor_nec_aberto, nec1,
                    nec2, nec3, molecula, data_alteracao, data_inclusao
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
    print(f"{inseridos} medicamento(s) tarjado(s) novo(s) inserido(s) de {len(df)} no arquivo.")
    if descartadas:
        print(f"{descartadas} linha(s) descartada(s) por faltar EAN, PRODUTO ou TIPO DE PRODUTO.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("criar-tabela", help="Cria a tabela medicamentos_tarjados se nao existir")

    p_carregar = sub.add_parser(
        "carregar",
        help='Carga inicial de um xlsx da base de tarjados (aba "Base Completa") pra medicamentos_tarjados',
    )
    p_carregar.add_argument(
        "arquivo", help='Caminho do xlsx (ex: "base de tarjados - ecommerce.xlsx")'
    )

    args = parser.parse_args()

    if args.comando == "criar-tabela":
        criar_tabela()
    elif args.comando == "carregar":
        carregar_tarjados(args.arquivo)


if __name__ == "__main__":
    main()
