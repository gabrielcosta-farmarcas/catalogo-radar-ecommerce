"""
Classifica cada combinação DISTINTA de taxonomia da IQVIA (area_farmacia +
sub_cat1..4) na árvore oficial de categorização, uma vez por combinação - em
vez de pedir pro Claude decidir isso produto por produto (~260 mil vezes,
com risco de inconsistência entre rodadas, ver mapear_iqvia_para_schema em
enrich_com_crawler.py). Popula a tabela `mapeamento_categoria_iqvia` pra
revisão humana antes de ser usada no fluxo de enriquecimento - essa
integração é um passo separado, não feito por este script.

Uso:
    python mapear_categorias_iqvia.py criar-tabela
    python mapear_categorias_iqvia.py popular

Requer ANTHROPIC_API_KEY (mesmo esquema de enrich_produtos.py - arquivo .env
na raiz do projeto) e Postgres rodando (docker-compose up -d).
"""

import argparse
import json
import os
import re
import sys

import psycopg2
import psycopg2.extras
from anthropic import Anthropic, APIStatusError, APIConnectionError

import categorias
import iqvia
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
CREATE TABLE IF NOT EXISTS mapeamento_categoria_iqvia (
    id                    BIGSERIAL PRIMARY KEY,
    tipo_cadastro         TEXT NOT NULL,
    area_farmacia         TEXT,
    sub_cat1              TEXT,
    sub_cat2              TEXT,
    sub_cat3              TEXT,
    sub_cat4              TEXT,
    departamento          TEXT,
    categoria             TEXT,
    subcategoria          TEXT,
    revisado_humanamente  BOOLEAN NOT NULL DEFAULT false,
    criado_em             TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS mapeamento_categoria_iqvia_chave ON mapeamento_categoria_iqvia (
    tipo_cadastro,
    coalesce(area_farmacia, ''), coalesce(sub_cat1, ''), coalesce(sub_cat2, ''),
    coalesce(sub_cat3, ''), coalesce(sub_cat4, '')
);
"""

CLASSIFICACAO_SYSTEM = """Você classifica TIPOS de produto farmacêutico/e-commerce na árvore oficial \
abaixo. Cada item da lista é uma classificação bruta de um catálogo parceiro (IQVIA) - não é um \
produto específico, é uma categoria inteira de produtos daquele tipo. Você não pesquisa nem inventa \
- usa só a árvore.

Pra cada item, ache a SUBCATEGORIA mais específica que descreve esse TIPO de produto (raciocine de \
baixo pra cima: ache a subcategoria certa primeiro, procurando em toda a árvore, depois copie \
departamento e categoria EXATAMENTE da MESMA linha da árvore onde essa subcategoria está - nunca \
combine departamento de uma linha com categoria de outra). Se nenhuma subcategoria da árvore \
descrever bem esse tipo de produto, responda null nos três campos pra esse item.

ÁRVORE OFICIAL:
{ARVORE_RAMO}

Responda com um array JSON, UM OBJETO POR ITEM DA LISTA, NA MESMA ORDEM, sem markdown e sem texto \
fora do array: [{"departamento": str|null, "categoria": str|null, "subcategoria": str|null}, ...]"""


def criar_tabela():
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
    print("Tabela criada/confirmada: mapeamento_categoria_iqvia.")


def extrair_combinacoes_distintas(conn):
    """
    Combinações distintas de (tipo_cadastro, area_farmacia, sub_cat1..4) na
    IQVIA. tipo_cadastro é derivado por linha a partir de setor_nec_aberto
    (iqvia.eh_medicamento) antes de aplicar DISTINCT - necessário porque a
    MESMA combinação de sub_cat pode conter produtos de tipos diferentes
    (ex: "LINHA INFANTIL > TRATAMENTO > POMADAS" tem tanto pomada cosmética
    quanto pomada medicamentosa) - sem isso, a checagem por linha vira uma
    checagem por grupo e perde essa distinção.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT setor_nec_aberto, area_farmacia, sub_cat1, sub_cat2, sub_cat3, sub_cat4 "
            "FROM iqvia_produtos"
        )
        linhas = cur.fetchall()

    vistos = set()
    combinacoes = []
    for setor, area, s1, s2, s3, s4 in linhas:
        tipo = "Medicamento" if iqvia.eh_medicamento(setor) else "Não Medicamento"
        chave = (tipo, area, s1, s2, s3, s4)
        if chave in vistos:
            continue
        vistos.add(chave)
        combinacoes.append(chave)
    return combinacoes


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
    Classifica um lote de combinações (mesmo tipo_cadastro) numa única
    chamada. Retorna lista de dicts {departamento, categoria, subcategoria}
    na mesma ordem do lote, ou None por item se a chamada falhar.
    """
    itens = "\n".join(
        f"{i+1}. {' > '.join(p for p in (area, s1, s2, s3, s4) if p)}"
        for i, (_tipo, area, s1, s2, s3, s4) in enumerate(lote)
    )
    system = CLASSIFICACAO_SYSTEM.replace("{ARVORE_RAMO}", arvore_ramo or "")
    mensagem = f"Classifique estes {len(lote)} tipos de produto:\n\n{itens}"

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
    if not arvores_por_ramo:
        sys.exit("Erro: tabela categorias vazia ou inexistente - rode carregar_categorias.py antes.")

    client = Anthropic()
    conn = conectar()
    try:
        combinacoes = extrair_combinacoes_distintas(conn)
        print(f"{len(combinacoes)} combinação(ões) distinta(s) de taxonomia da IQVIA encontrada(s).")

        gravadas = 0
        zeradas = 0
        for tipo in ("Medicamento", "Não Medicamento"):
            do_tipo = [c for c in combinacoes if c[0] == tipo]
            arvore_ramo = arvores_por_ramo.get(tipo, "")
            print(f"\n=== {tipo}: {len(do_tipo)} combinação(ões) ===")

            for inicio in range(0, len(do_tipo), tamanho_lote):
                lote = do_tipo[inicio : inicio + tamanho_lote]
                resultados = classificar_lote(client, model, arvore_ramo, lote)

                with conn.cursor() as cur:
                    for (t, area, s1, s2, s3, s4), resultado in zip(lote, resultados):
                        dep = cat = sub = None
                        if resultado:
                            dep = resultado.get("departamento")
                            cat = resultado.get("categoria")
                            sub = resultado.get("subcategoria")
                            if (t, dep, cat, sub) not in combinacoes_validas:
                                if dep or cat or sub:
                                    zeradas += 1
                                dep = cat = sub = None
                        cur.execute(
                            """
                            INSERT INTO mapeamento_categoria_iqvia
                                (tipo_cadastro, area_farmacia, sub_cat1, sub_cat2, sub_cat3, sub_cat4,
                                 departamento, categoria, subcategoria)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (tipo_cadastro, coalesce(area_farmacia, ''), coalesce(sub_cat1, ''),
                                         coalesce(sub_cat2, ''), coalesce(sub_cat3, ''), coalesce(sub_cat4, ''))
                            DO UPDATE SET departamento = EXCLUDED.departamento,
                                          categoria = EXCLUDED.categoria,
                                          subcategoria = EXCLUDED.subcategoria,
                                          revisado_humanamente = false,
                                          atualizado_em = now()
                            """,
                            (t, area, s1, s2, s3, s4, dep, cat, sub),
                        )
                        gravadas += 1
                conn.commit()
                print(f"  {min(inicio + tamanho_lote, len(do_tipo))}/{len(do_tipo)} classificado(s)")

        print(
            f"\nConcluído. {gravadas} combinação(ões) gravada(s) "
            f"({zeradas} zerada(s) por não bater com a árvore oficial). "
            "Tudo com revisado_humanamente=false - revisar antes de usar no fluxo de enriquecimento."
        )
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("criar-tabela", help="Cria a tabela mapeamento_categoria_iqvia")
    p_popular = sub.add_parser(
        "popular", help="Extrai combinações distintas da IQVIA e classifica via Claude"
    )
    p_popular.add_argument("--model", default="claude-haiku-4-5-20251001")
    p_popular.add_argument(
        "--tamanho-lote", type=int, default=25, help="Combinações classificadas por chamada"
    )
    args = parser.parse_args()

    if args.comando == "criar-tabela":
        criar_tabela()
    elif args.comando == "popular":
        popular(model=args.model, tamanho_lote=args.tamanho_lote)


if __name__ == "__main__":
    main()
