"""
Script pra extrair as listas de substâncias controladas de dois PDFs
oficiais e carregar na tabela `substancias_controladas` do Postgres -
tabela de referência/consulta, não usada hoje por enrich_produtos.py.

Fontes suportadas:
- Portaria SVS/MS nº 344/1998 (com atualizações da Anvisa): listas A1, A2,
  A3, B1, B2, C1, C2, C3, C5, D1, D2, E. A Lista F (substâncias proscritas)
  não é extraída - depende de um PDF com essa lista completa (o PDF de
  referência usado aqui vem cortado nela).
- Instrução Normativa da Anvisa que define a lista de antimicrobianos e de
  agonistas de GLP-1 sujeitos a retenção de receita (RDC nº 471/2021) - 2
  listas, cada uma com sua validade de receita em dias.

Uso:
    pip install pypdf   # extração de texto do PDF
    python carregar_substancias_controladas.py criar-tabela
    python carregar_substancias_controladas.py carregar-portaria-344 "portaria 344.pdf"
    python carregar_substancias_controladas.py carregar-retencao-rdc471 "IN 360.pdf"

Requer Postgres rodando (docker-compose up -d) e psycopg2-binary instalado.
"""

import argparse
import re

import psycopg2.extras
from pypdf import PdfReader

from db import conectar

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS substancias_controladas (
    id                     SERIAL PRIMARY KEY,
    fonte                  TEXT NOT NULL,   -- 'portaria_344' | 'in_360_2025'
    lista                  TEXT NOT NULL,   -- 'A1'..'E', ou 'antimicrobianos'/'glp1'
    descricao_lista        TEXT NOT NULL,
    substancia             TEXT NOT NULL,
    validade_receita_dias  INTEGER,         -- só preenchido pra fonte in_360_2025
    criado_em              TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def criar_tabela():
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
    print("Tabela criada/confirmada: substancias_controladas.")


def _extrair_texto(caminho_pdf):
    reader = PdfReader(caminho_pdf)
    texto = "\n".join(page.extract_text() or "" for page in reader.pages)
    texto = re.sub(r"=== PAGINA \d+ ===", "", texto)
    # ligaduras "fi"/"fl" que o pypdf às vezes extrai como um glyph só,
    # em vez das duas letras (ex: "besiﬂoxacino" em vez de "besifloxacino")
    texto = texto.replace("ﬁ", "fi").replace("ﬂ", "fl")
    # PDFs exportados do site in.gov.br (Diário Oficial da União) repetem em
    # toda página um rodapé com data/hora de acesso e a URL da publicação -
    # ambos têm ":" (ex: "https://...") que quebra a busca por ":" usada
    # pra achar onde a lista numerada começa, então remove antes de tudo
    texto = re.sub(r"^\d{2}/\d{2}/\d{4}, \d{2}:\d{2}.*$", "", texto, flags=re.MULTILINE)
    texto = re.sub(r"^https://\S+.*$", "", texto, flags=re.MULTILINE)
    return texto


# ---------------------------------------------------------------------------
# Portaria 344 - listas A1, A2, A3, B1, B2, C1, C2, C3, C5, D1, D2, E
# ---------------------------------------------------------------------------

DESCRICAO_LISTAS_344 = {
    "A1": "Substâncias Entorpecentes",
    "A2": "Substâncias Entorpecentes de Uso Permitido Somente em Concentrações Especiais",
    "A3": "Substâncias Psicotrópicas (Notificação de Receita A)",
    "B1": "Substâncias Psicotrópicas (Notificação de Receita B)",
    "B2": "Substâncias Psicotrópicas Anorexígenas",
    "C1": "Outras Substâncias Sujeitas a Controle Especial",
    "C2": "Substâncias Retinoicas",
    "C3": "Substâncias Imunossupressoras",
    "C5": "Substâncias Anabolizantes",
    "D1": "Substâncias Precursoras de Entorpecentes e/ou Psicotrópicos",
    "D2": "Insumos Químicos Utilizados para Fabricação e Síntese de Entorpecentes e/ou Psicotrópicos",
    "E": "Plantas e Fungos Proscritos que Podem Originar Substâncias Entorpecentes e/ou Psicotrópicas",
}


def _extrair_itens_numerados(bloco):
    """
    Extrai itens no formato "N. Nome" (ou "N - Nome") de um bloco de texto,
    um por linha. Quando o número vem sozinho numa linha (ex: "27.") - a
    quebra de página às vezes separa o número do nome - junta com a linha
    seguinte.
    """
    linhas = [l.strip() for l in bloco.split("\n") if l.strip()]
    itens = []
    i = 0
    while i < len(linhas):
        m = re.match(r"^(\d+)\s*[.\-]\s*(.*)$", linhas[i])
        if m:
            numero, nome = m.groups()
            nome = nome.strip()
            if not nome and i + 1 < len(linhas):
                i += 1
                nome = linhas[i].strip()
            # normaliza espaços (o pypdf às vezes extrai múltiplos espaços
            # como tab) e tira pontuação/conjunção de fechamento de frase
            # (o último item de uma lista costuma vir "nome, e" ou "nome.")
            nome = re.sub(r"\s+", " ", nome).strip()
            nome = re.sub(r"[;,.]?\s*\be\b\.?\s*$", "", nome).strip(" ;,.")
            if nome:
                itens.append((int(numero), nome))
        i += 1
    return itens


def parsear_portaria_344(caminho_pdf):
    """
    Retorna lista de tuplas (lista, descricao_lista, substancia). Cada lista
    é isolada entre seu marcador "LISTA - XX" e o "ADENDO" seguinte (as
    notas do adendo não são substâncias). Loga se a numeração encontrada
    tiver algum buraco (indício de erro de extração, não necessariamente um
    problema - a IN 360/2025 antimicrobianos, por exemplo, tem um buraco de
    verdade no original).
    """
    texto = _extrair_texto(caminho_pdf)
    pedacos = re.split(r"LISTA\s*-\s*([A-Z0-9]+)\s*\n", texto)

    linhas_saida = []
    for i in range(1, len(pedacos), 2):
        lista = pedacos[i]
        if lista not in DESCRICAO_LISTAS_344:
            continue
        conteudo = pedacos[i + 1] if i + 1 < len(pedacos) else ""
        corpo = conteudo.split("ADENDO", 1)[0]
        # remove cabeçalhos ("LISTA DAS SUBSTÂNCIAS...", "(Sujeitas a...)")
        corpo = "\n".join(
            l for l in corpo.split("\n")
            if not l.strip().startswith("LISTA") and not l.strip().startswith("(")
        )
        itens = _extrair_itens_numerados(corpo)

        numeros = [n for n, _ in itens]
        buracos = sorted(set(range(1, max(numeros) + 1)) - set(numeros)) if numeros else []
        if buracos:
            print(f"  [aviso] Lista {lista}: números ausentes na numeração: {buracos}")
        print(f"  Lista {lista}: {len(itens)} substância(s) extraída(s).")

        descricao = DESCRICAO_LISTAS_344[lista]
        for _, nome in itens:
            linhas_saida.append((lista, descricao, nome))

    return linhas_saida


def carregar_portaria_344(caminho_pdf):
    linhas = parsear_portaria_344(caminho_pdf)
    tuplas = [(  "portaria_344", lista, descricao, nome, None) for lista, descricao, nome in linhas]

    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM substancias_controladas WHERE fonte = 'portaria_344'")
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO substancias_controladas
                    (fonte, lista, descricao_lista, substancia, validade_receita_dias)
                VALUES %s
                """,
                tuplas,
            )
        conn.commit()
    print(f"{len(tuplas)} substância(s) da Portaria 344 carregada(s) (12 listas, A1 a E - sem Lista F).")


# ---------------------------------------------------------------------------
# IN (Anvisa) que define a lista de antimicrobianos e GLP-1 sujeitos a
# retenção de receita, nos termos da RDC nº 471/2021
# ---------------------------------------------------------------------------

VALIDADE_ANTIMICROBIANOS_DIAS = 10
VALIDADE_GLP1_DIAS = 90


def parsear_retencao_rdc471(caminho_pdf):
    """
    Retorna lista de tuplas (lista, descricao_lista, substancia,
    validade_receita_dias) com as 2 listas do documento: antimicrobianos
    (Art. 1º) e agonistas de GLP-1 (Art. 2º).
    """
    texto = _extrair_texto(caminho_pdf)

    bloco_antimicrobianos = texto.split("Art. 1º", 1)[1].split("Art. 2º", 1)[0]
    # a lista em si começa depois do último ":" antes do primeiro item
    # numerado (o texto do artigo antes disso também tem números de RDC/DOU)
    inicio = bloco_antimicrobianos.rfind(":")
    itens_antimicrobianos = _extrair_itens_numerados(
        bloco_antimicrobianos[inicio + 1:] if inicio != -1 else bloco_antimicrobianos
    )
    numeros = [n for n, _ in itens_antimicrobianos]
    if numeros:
        buracos = sorted(set(range(1, max(numeros) + 1)) - set(numeros))
        if buracos:
            print(f"  [aviso] antimicrobianos: números ausentes na numeração original: {buracos}")
    print(f"  Antimicrobianos: {len(itens_antimicrobianos)} substância(s) extraída(s).")

    bloco_glp1 = texto.split("Art. 2º", 1)[1]
    inicio = bloco_glp1.rfind(":")
    itens_glp1 = _extrair_itens_numerados(bloco_glp1[inicio + 1:] if inicio != -1 else bloco_glp1)
    print(f"  GLP-1: {len(itens_glp1)} substância(s) extraída(s).")

    saida = [
        ("antimicrobianos", "Antimicrobianos de Uso sob Prescrição e Retenção de Receita",
         nome, VALIDADE_ANTIMICROBIANOS_DIAS)
        for _, nome in itens_antimicrobianos
    ]
    saida += [
        ("glp1", "Agonistas do Receptor do GLP-1 de Uso sob Prescrição e Retenção de Receita",
         nome, VALIDADE_GLP1_DIAS)
        for _, nome in itens_glp1
    ]
    return saida


def carregar_retencao_rdc471(caminho_pdf):
    linhas = parsear_retencao_rdc471(caminho_pdf)
    tuplas = [
        ("in_360_2025", lista, descricao, nome, validade)
        for lista, descricao, nome, validade in linhas
    ]

    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM substancias_controladas WHERE fonte = 'in_360_2025'")
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO substancias_controladas
                    (fonte, lista, descricao_lista, substancia, validade_receita_dias)
                VALUES %s
                """,
                tuplas,
            )
        conn.commit()
    print(f"{len(tuplas)} substância(s) de retenção (antimicrobianos + GLP-1) carregada(s).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("criar-tabela", help="Cria a tabela substancias_controladas se nao existir")

    p1 = sub.add_parser(
        "carregar-portaria-344",
        help="Extrai as listas A1-E da Portaria 344 de um PDF e substitui esses dados na tabela",
    )
    p1.add_argument("arquivo", help="Caminho do PDF da Portaria 344 (Anexo I, com as listas)")

    p2 = sub.add_parser(
        "carregar-retencao-rdc471",
        help="Extrai as listas de antimicrobianos e GLP-1 (retenção de receita, RDC 471/2021) de um PDF",
    )
    p2.add_argument("arquivo", help="Caminho do PDF da IN da Anvisa com essas duas listas")

    args = parser.parse_args()

    if args.comando == "criar-tabela":
        criar_tabela()
    elif args.comando == "carregar-portaria-344":
        carregar_portaria_344(args.arquivo)
    elif args.comando == "carregar-retencao-rdc471":
        carregar_retencao_rdc471(args.arquivo)


if __name__ == "__main__":
    main()
