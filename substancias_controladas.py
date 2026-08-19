"""
Consulta a tabela `substancias_controladas` (carregada por
carregar_substancias_controladas.py a partir da Portaria 344 e da IN da
Anvisa sobre retenção de receita - RDC nº 471/2021) pra decidir se um
princípio ativo exige retenção de receita.

Módulo separado de propósito - conexão própria, não importa db.py, mesmo
padrão de cmed.py/categorias.py. Usado por
enrich_com_crawler.mapear_cmed_para_schema: só entra em jogo quando a CMED
já confirmou o medicamento mas a tarja não é Preta nem Sem Tarja (ver regra
combinada com o time de negócio) - decide se essa Tarja Vermelha exige
retenção mesmo assim.
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

# substâncias que aparecem numa lista de controle da Portaria 344, mas cujo
# próprio ADENDO da lista isenta explicitamente de retenção (texto oficial:
# "VENDA SOB PRESCRIÇÃO MÉDICA SEM RETENÇÃO DE RECEITA") - sem essa exceção,
# essas duas cairiam como "precisa retenção" por engano, quando a própria
# Anvisa diz o contrário. Ver adendo 16 da Lista B1 (carisoprodol) e adendo 2
# da Lista C1 (loperamida). Não é uma regra confirmada com o time de negócio
# ainda - avaliar antes de confiar cegamente nela.
EXCECOES_SEM_RETENCAO = {"carisoprodol", "loperamida"}


def conectar():
    return psycopg2.connect(**DB_CONFIG)


def _normalizar(texto):
    return re.sub(r"\s+", " ", texto or "").strip().lower()


def carregar_nomes():
    """
    Retorna o set (normalizado, minúsculo) com o nome de cada substância da
    tabela, já sem as exceções de EXCECOES_SEM_RETENCAO. Set vazio se a
    tabela estiver vazia/inexistente ou o Postgres estiver fora do ar (o
    chamador segue sem essa informação, em vez de quebrar).
    """
    try:
        with conectar() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT substancia FROM substancias_controladas")
                nomes = {_normalizar(row[0]) for row in cur.fetchall()}
    except psycopg2.OperationalError:
        return set()
    return nomes - EXCECOES_SEM_RETENCAO


def _compilar_padrao(nomes):
    if not nomes:
        return None
    alternativas = "|".join(re.escape(nome) for nome in nomes if nome)
    return re.compile(rf"\b(?:{alternativas})\b", re.IGNORECASE) if alternativas else None


NOMES_CONTROLADOS = carregar_nomes()
PADRAO_CONTROLADAS = _compilar_padrao(NOMES_CONTROLADOS)


def substancia_esta_controlada(principios_ativos):
    """
    Recebe o texto de principios_ativos já formatado (ex: "Midazolam 15mg,
    Fenobarbital 100mg") e verifica se ALGUMA substância controlada aparece
    como palavra(s) inteira(s) no texto - cobre o caso do princípio ativo
    vir com o nome do sal (ex: "Cloridrato de Midazolam"), já que a lista
    oficial só tem o nome base ("Midazolam"): o \\b garante que só bate
    quando "midazolam" aparece como palavra própria, não como pedaço de
    outra palavra.

    Não distingue forma farmacêutica - algumas exceções da Portaria 344 só
    valem para uso tópico (ex: retinoicas da Lista C2, anabolizantes da
    Lista C5), e isso não dá pra inferir só do nome do princípio ativo;
    nesses casos o retorno pode ser um falso positivo de retenção e a linha
    deve ser conferida manualmente.
    """
    if not PADRAO_CONTROLADAS or not principios_ativos:
        return False
    return bool(PADRAO_CONTROLADAS.search(principios_ativos))
