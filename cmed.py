"""
Consulta a tabela `anvisa_medicamentos` (carregada por carregar_cmed.py a
partir do cmed_carga.xlsx, base oficial da ANVISA) por EAN.

Módulo separado de propósito - conexão própria, não importa db.py (que é
isolado pro fluxo de batch experimental). Usado como camada 0 do
enrich_com_crawler.py: se o EAN está na CMED, o produto É medicamento com
certeza (fonte oficial ANVISA) e os dados vêm direto da tabela, sem gastar
tokens de busca nem precisar da segunda verificação de tarja/registro_ms que
o restante do fluxo faz para fontes menos confiáveis (crawler/Google/busca
agentic do Claude).
"""

import os
import re

import psycopg2

DB_CONFIG = {
    "host": os.environ.get("PG_HOST", "localhost"),
    "port": os.environ.get("PG_PORT", "5433"),
    "user": os.environ.get("PG_USER", "cadastro"),
    "password": os.environ.get("PG_PASSWORD", "cadastro"),
    "dbname": os.environ.get("PG_DB", "cadastro_produtos"),
}

CAMPOS = (
    "codigo_ggrem", "substancia", "laboratorio", "registro",
    "produto", "apresentacao", "classe_terapeutica", "tipo_produto", "tarja",
)

# a tabela vem com valores de TARJA no formato bruto da planilha da ANVISA -
# traduz pro vocabulário fechado usado no resto do sistema (ALLOWED_TARJA em
# enrich_produtos.py). "- (*)" é o próprio dado oficial vindo sem essa
# informação (não é exclusivo de nenhum tipo de produto - ver análise da
# planilha), então fica de fora do mapa de propósito: campo não confirmado
# deve virar None, nunca um valor adivinhado - regra que o resto do código já
# segue (ver apply_safety_checks). "sob restrição" ainda é tarja vermelha
# (venda sob prescrição, só que com controle adicional de retenção de receita).
TARJA_CMED_PARA_SCHEMA = {
    "Tarja Vermelha": "Tarja Vermelha",
    "Tarja Vermelha sob restrição": "Tarja Vermelha",
    "Tarja Preta": "Tarja Preta",
    "Tarja Sem Tarja": "Sem Tarja",
}

# marcador de origem usado em origem_enriquecimento - apply_safety_checks usa
# esse prefixo para dispensar a exigência de pagina_produto_url (a CMED é uma
# tabela de referência, não uma página web, mas é uma fonte oficial confiável)
ORIGEM_ANVISA_CMED = "anvisa_cmed"


def conectar():
    return psycopg2.connect(**DB_CONFIG)


def normalizar_ean(valor):
    """
    EANs às vezes chegam com zero(s) à esquerda (ex: alguns ERPs gravam EAN-13
    como GTIN-14 preenchendo com '0' na frente: 05702150153890). Como EAN é
    numérico por natureza, remove qualquer zero à esquerda convertendo para
    int e de volta - 05702150153890 -> 5702150153890, que é como a tabela
    anvisa_medicamentos guarda o valor (ver carregar_cmed.py).
    """
    digitos = re.sub(r"\D", "", str(valor or ""))
    if not digitos:
        return ""
    normalizado = str(int(digitos))
    # "0" não é um EAN válido - é como a própria ANVISA marca "sem código de
    # barras informado" em algumas linhas da CMED (ex: KYMRIAH, ADACNE PEROX),
    # e sem essa checagem qualquer EAN de entrada só com zeros bateria com
    # essas linhas por engano.
    return "" if normalizado == "0" else normalizado


def buscar_medicamento_anvisa(ean):
    """
    Busca o EAN (normalizado) em ean_1/ean_2/ean_3 da tabela anvisa_medicamentos.
    Retorna um dict com os campos da CMED, ou None se o EAN não está na base
    oficial da ANVISA - nesse caso o chamador deve seguir o fluxo normal
    (crawler/Google/Claude), já que "não está na CMED" não significa "não é
    medicamento" (a base pode estar desatualizada ou o produto pode ser novo).
    """
    ean_normalizado = normalizar_ean(ean)
    if not ean_normalizado:
        return None

    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(CAMPOS)} FROM anvisa_medicamentos "
                "WHERE ean_1 = %s OR ean_2 = %s OR ean_3 = %s LIMIT 1",
                (ean_normalizado, ean_normalizado, ean_normalizado),
            )
            row = cur.fetchone()

    if row is None:
        return None
    return dict(zip(CAMPOS, row))
