"""
Consulta a tabela `abcfarma_medicamentos` (carregada por carregar_abcfarma.py a
partir do xlsx da ABCFarma) por EAN.

Segunda fonte de "verdade absoluta" de que um EAN é medicamento, ao lado da
CMED (cmed.py) - camada 0 do enrich_com_crawler.py: se o EAN está na CMED OU
na ABCFarma, o produto É medicamento com certeza, sem precisar gastar tokens
de busca. Diferente da CMED, a base da ABCFarma não traz TARJA - ver
ORIGEM_ABCFARMA e o comentário em enrich_com_crawler.py sobre por que um
match só na ABCFarma ainda passa pela verificação dedicada de tarja.
"""

import os
import sys

import psycopg2

from cmed import normalizar_ean

DB_CONFIG = {
    "host": os.environ.get("PG_HOST", "localhost"),
    "port": os.environ.get("PG_PORT", "5433"),
    "user": os.environ.get("PG_USER", "cadastro"),
    "password": os.environ.get("PG_PASSWORD", "cadastro"),
    "dbname": os.environ.get("PG_DB", "cadastro_produtos"),
}

CAMPOS = (
    "codigo_produto", "ean", "descricao_produto", "apresentacao",
    "laboratorio", "registro_anvisa", "tipo_medicamento", "principio_ativo",
    "produto_referencia", "ggrem",
)

# marcador de origem usado em origem_enriquecimento - mesmo padrão de
# cmed.ORIGEM_ANVISA_CMED, duplicado aqui de propósito pra não criar
# dependência circular entre os dois módulos de fonte oficial
ORIGEM_ABCFARMA = "abcfarma"


def conectar():
    return psycopg2.connect(**DB_CONFIG)


# evita imprimir o mesmo aviso de "tabela não existe" uma vez por EAN quando
# rodando com concorrência - só a primeira ocorrência é logada
_tabela_ausente_avisada = False


def buscar_medicamento_abcfarma(ean):
    """
    Busca o EAN (normalizado) na tabela abcfarma_medicamentos. Retorna um
    dict com os campos da ABCFarma, ou None se o EAN não está na base -
    nesse caso o chamador deve seguir o fluxo normal (CMED já foi checada
    antes disso; se também não achou lá, cai pro crawler/Claude).

    Se a tabela ainda não foi criada (esquema não rodou
    'carregar_abcfarma.py criar-tabela' ainda), também trata como "não
    achou" em vez de derrubar o processamento inteiro - visto acontecer:
    UndefinedTable travava TODAS as linhas do lote, não só as que
    passariam por essa camada.

    O EAN não é chave única nessa base (a mesma ABCFarma repete o mesmo EAN
    em códigos de produto diferentes, ex: reformulação/atualização de
    cadastro) - prioriza a linha com registro_anvisa preenchido, que é o
    dado mais útil pro resto do fluxo.
    """
    global _tabela_ausente_avisada
    ean_normalizado = normalizar_ean(ean)
    if not ean_normalizado:
        return None

    try:
        with conectar() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {', '.join(CAMPOS)} FROM abcfarma_medicamentos "
                    "WHERE ean = %s "
                    "ORDER BY (registro_anvisa IS NULL), codigo_produto "
                    "LIMIT 1",
                    (ean_normalizado,),
                )
                row = cur.fetchone()
    except psycopg2.errors.UndefinedTable:
        if not _tabela_ausente_avisada:
            print(
                "[aviso] tabela abcfarma_medicamentos não existe ainda - "
                "rode 'python carregar_abcfarma.py criar-tabela' (e "
                "'carregar <xlsx>' pra ter dados). Seguindo sem essa "
                "camada por enquanto.",
                file=sys.stderr,
            )
            _tabela_ausente_avisada = True
        return None

    if row is None:
        return None
    return dict(zip(CAMPOS, row))
