"""
Classifica cada `categoria_bruta` DISTINTA da CMED (classe_terapeutica, com
sufixo "(tipo_produto CMED: Fitoterápico)" quando aplicável - mesma
derivação já usada em mapear_cmed_para_schema, ver enrich_com_crawler.py)
na árvore oficial de categorização, uma vez por combinação - mesmo raciocínio
de mapear_categorias_iqvia.py, aplicado à CMED (~576 combinações distintas
cobrindo 26 mil medicamentos, em vez de perguntar pro Claude produto por
produto). Popula a tabela `mapeamento_categoria_cmed` pra revisão humana
antes de ser usada no fluxo de enriquecimento - essa integração é um passo
separado, não feito por este script.

Uso:
    python mapear_categorias_cmed.py criar-tabela
    python mapear_categorias_cmed.py popular

Requer ANTHROPIC_API_KEY (.env na raiz do projeto) e Postgres rodando.
"""

import argparse
import json
import os
import re
import sys

from anthropic import Anthropic, APIStatusError, APIConnectionError

import categorias
from db import conectar


def _carregar_dotenv():
    """Mesmo loader de enrich_produtos.py - duplicado de propósito, pra não
    acoplar este script ao módulo de enriquecimento inteiro só por isso."""
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(caminho):
        return
    with open(caminho, encoding="utf-8") as arquivo:
        for linha in arquivo:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, _, valor = linha.partition("=")
            valor = valor.strip().strip('"').strip("'")
            os.environ.setdefault(chave.strip(), valor)


_carregar_dotenv()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mapeamento_categoria_cmed (
    id                    BIGSERIAL PRIMARY KEY,
    categoria_bruta       TEXT NOT NULL,
    departamento          TEXT,
    categoria             TEXT,
    subcategoria          TEXT,
    revisado_humanamente  BOOLEAN NOT NULL DEFAULT false,
    criado_em             TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS mapeamento_categoria_cmed_chave ON mapeamento_categoria_cmed (categoria_bruta);
"""

CLASSIFICACAO_SYSTEM = """Você classifica CLASSES TERAPÊUTICAS de medicamento na árvore oficial abaixo. \
Cada item da lista é uma classe terapêutica inteira da base oficial da ANVISA/CMED (ex: "N3A - \
ANTIEPILÉPTICOS") - não é um produto específico, é a classificação regulatória de um grupo de \
medicamentos. Quando o item tiver o sufixo "(tipo_produto CMED: Fitoterápico)", trate como \
medicamento fitoterápico/natural mesmo que a classe terapêutica soe como outra coisa - a CMED já \
confirmou isso, tem prioridade sobre o nome da classe. Você não pesquisa nem inventa - usa só a \
árvore.

Pra cada item, ache a SUBCATEGORIA mais específica que descreve o USO desse grupo de medicamentos \
(raciocine de baixo pra cima: ache a subcategoria certa primeiro, procurando em toda a árvore, \
depois copie departamento e categoria EXATAMENTE da MESMA linha da árvore onde essa subcategoria \
está - nunca combine departamento de uma linha com categoria de outra). Se nenhuma subcategoria da \
árvore descrever bem esse tipo de medicamento, responda null nos três campos pra esse item.

ÁRVORE OFICIAL (ramo Medicamento):
{ARVORE_RAMO}

Responda com um array JSON, UM OBJETO POR ITEM DA LISTA, NA MESMA ORDEM, sem markdown e sem texto \
fora do array: [{"departamento": str|null, "categoria": str|null, "subcategoria": str|null}, ...]"""


def criar_tabela():
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
    print("Tabela criada/confirmada: mapeamento_categoria_cmed.")


def extrair_categorias_brutas_distintas(conn):
    """
    Mesma derivação de categoria_bruta já usada em mapear_cmed_para_schema
    (enrich_com_crawler.py): classe_terapeutica sozinha, ou com o sufixo de
    fitoterápico quando tipo_produto confirma isso.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT classe_terapeutica, tipo_produto FROM anvisa_medicamentos")
        linhas = cur.fetchall()

    vistas = set()
    resultado = []
    for classe, tipo_produto in linhas:
        bruta = classe
        if tipo_produto == "Fitoterápico":
            bruta = f"{classe} (tipo_produto CMED: Fitoterápico)"
        if bruta in vistas:
            continue
        vistas.add(bruta)
        resultado.append(bruta)
    return resultado


def _extrair_json_array(texto):
    texto = texto.strip()
    fence = re.search(r"```(?:json)?\s*(\[.*\])\s*```", texto, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    inicio, fim = texto.find("["), texto.rfind("]")
    if inicio != -1 and fim != -1 and fim > inicio:
        return json.loads(texto[inicio : fim + 1])
    raise json.JSONDecodeError("nenhum array JSON encontrado", texto, 0)


def classificar_lote(client, model, arvore_ramo, lote):
    """
    Classifica um lote de categoria_bruta numa única chamada. Retorna lista
    de dicts {departamento, categoria, subcategoria} na mesma ordem do
    lote, ou None por item se a chamada falhar.
    """
    itens = "\n".join(f"{i+1}. {bruta}" for i, bruta in enumerate(lote))
    system = CLASSIFICACAO_SYSTEM.replace("{ARVORE_RAMO}", arvore_ramo or "")
    mensagem = f"Classifique estas {len(lote)} classes terapêuticas:\n\n{itens}"

    try:
        response = client.messages.create(
            model=model,
            max_tokens=200 * len(lote),
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": mensagem}],
        )
        texto = "".join(b.text for b in response.content if b.type == "text")
        resultado = _extrair_json_array(texto)
    except (APIStatusError, APIConnectionError, json.JSONDecodeError, ValueError) as exc:
        print(f"  [erro] falha ao classificar lote: {exc}", file=sys.stderr)
        return [None] * len(lote)

    if len(resultado) != len(lote):
        print(
            f"  [aviso] lote de {len(lote)} itens voltou com {len(resultado)} "
            "resultados - descartando o lote inteiro (evita desalinhar item com resultado).",
            file=sys.stderr,
        )
        return [None] * len(lote)
    return resultado


def popular(model="claude-haiku-4-5-20251001", tamanho_lote=25):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or "COLOQUE_SUA_KEY_AQUI" in api_key:
        sys.exit("Erro: defina uma API key válida da Anthropic (.env na raiz do projeto).")

    combinacoes_validas, arvores_por_ramo = categorias.carregar_arvore()
    arvore_medicamento = arvores_por_ramo.get("Medicamento", "")
    if not arvore_medicamento:
        sys.exit("Erro: tabela categorias vazia ou sem ramo Medicamento - rode carregar_categorias.py antes.")

    client = Anthropic()
    conn = conectar()
    try:
        categorias_brutas = extrair_categorias_brutas_distintas(conn)
        print(f"{len(categorias_brutas)} categoria(s) bruta(s) distinta(s) da CMED encontrada(s).")

        gravadas = 0
        zeradas = 0
        for inicio in range(0, len(categorias_brutas), tamanho_lote):
            lote = categorias_brutas[inicio : inicio + tamanho_lote]
            resultados = classificar_lote(client, model, arvore_medicamento, lote)

            with conn.cursor() as cur:
                for bruta, resultado in zip(lote, resultados):
                    dep = cat = sub = None
                    if resultado:
                        dep = resultado.get("departamento")
                        cat = resultado.get("categoria")
                        sub = resultado.get("subcategoria")
                        if ("Medicamento", dep, cat, sub) not in combinacoes_validas:
                            if dep or cat or sub:
                                zeradas += 1
                            dep = cat = sub = None
                    cur.execute(
                        """
                        INSERT INTO mapeamento_categoria_cmed
                            (categoria_bruta, departamento, categoria, subcategoria)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (categoria_bruta)
                        DO UPDATE SET departamento = EXCLUDED.departamento,
                                      categoria = EXCLUDED.categoria,
                                      subcategoria = EXCLUDED.subcategoria,
                                      revisado_humanamente = false,
                                      atualizado_em = now()
                        """,
                        (bruta, dep, cat, sub),
                    )
                    gravadas += 1
            conn.commit()
            print(f"  {min(inicio + tamanho_lote, len(categorias_brutas))}/{len(categorias_brutas)} classificado(s)")

        print(
            f"\nConcluído. {gravadas} categoria(s) gravada(s) "
            f"({zeradas} zerada(s) por não bater com a árvore oficial). "
            "Tudo com revisado_humanamente=false - revisar antes de usar no fluxo de enriquecimento."
        )
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("criar-tabela", help="Cria a tabela mapeamento_categoria_cmed")
    p_popular = sub.add_parser(
        "popular", help="Extrai classes terapêuticas distintas da CMED e classifica via Claude"
    )
    p_popular.add_argument("--model", default="claude-haiku-4-5-20251001")
    p_popular.add_argument(
        "--tamanho-lote", type=int, default=25, help="Categorias classificadas por chamada"
    )
    args = parser.parse_args()

    if args.comando == "criar-tabela":
        criar_tabela()
    elif args.comando == "popular":
        popular(model=args.model, tamanho_lote=args.tamanho_lote)


if __name__ == "__main__":
    main()
