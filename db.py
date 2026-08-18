"""
Camada de banco de dados pra gerenciar o fluxo de enriquecimento via Batch API,
em etapas. Módulo isolado de propósito - não importa nem é importado por
enrich_produtos.py / enrich_com_crawler.py, pra não acoplar o fluxo em tempo
real (que já funciona) com o fluxo em lote (que ainda está em experimentação).
Importa só `cmed` (consulta à tabela oficial da ANVISA) - isso não fere o
isolamento, é só acesso a dado, não acopla com o fluxo de enriquecimento em
tempo real.

3 tabelas:
- produtos: 1 linha por EAN - estado atual e resultado final (o que hoje é o
  xlsx).
- batches: 1 linha por lote submetido à Batch API da Anthropic - o lote tem
  ciclo de vida próprio (id, status, criado em X, processado em Y),
  compartilhado por até 100 mil EANs de uma vez.
- batch_items: ponte entre as duas - resolve o problema de que os resultados
  da Batch API voltam FORA DE ORDEM, identificados só por um custom_id, e
  permite um mesmo EAN aparecer em mais de um batch (fase 1: enriquecimento;
  fase 3: verificação de tarja).

Fluxo de fases de `produtos.fase_atual` (nesta ordem):
    aguardando_cmed -> aguardando_crawler -> aguardando_batch_enriquecimento
                                                              |
                     aguardando_batch_formatacao <-----------+ (se achou na CMED)
                                  |                           |
                                  +-----------> aguardando_batch_tarja (só se NÃO veio da CMED)
                                                              |
                                                          concluido | nao_localizado

Se o EAN está na tabela `medicamentos` (CMED/ANVISA), é tratado como verdade
absoluta: tipo_cadastro, registro_ms, fabricante, generico e tarja vêm direto
de lá, sem precisar da fase de verificação de tarja (`aguardando_batch_tarja`)
- só falta uma formatação leve de título/composição/categoria
(`aguardando_batch_formatacao`). Ver `verificar_cmed()`.

Uso básico:
    python db.py criar-tabelas
    python db.py carregar-eans eans_estoque_sem_venda_12m.xlsx
    python db.py verificar-cmed

Requer Postgres rodando (docker-compose up -d) e psycopg2-binary instalado.
"""

import argparse
import json
import os
import sys

import pandas as pd
import psycopg2
import psycopg2.extras

import cmed

DB_CONFIG = {
    "host": os.environ.get("PG_HOST", "localhost"),
    "port": os.environ.get("PG_PORT", "5433"),
    "user": os.environ.get("PG_USER", "cadastro"),
    "password": os.environ.get("PG_PASSWORD", "cadastro"),
    "dbname": os.environ.get("PG_DB", "cadastro_produtos"),
}


def conectar():
    return psycopg2.connect(**DB_CONFIG)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS produtos (
    ean                     TEXT PRIMARY KEY,
    nome_produto            TEXT NOT NULL,

    -- resultado final (mesmo schema do xlsx, mesma ordem de RESULT_COLUMNS)
    titulo                  TEXT,
    marca                   TEXT,
    fabricante              TEXT,
    tipo_cadastro           TEXT,
    registro_ms             TEXT,
    generico                TEXT,
    tarja                   TEXT,
    principios_ativos       TEXT,
    descricao_curta         TEXT,
    frase_obrigatoria       TEXT,
    departamento            TEXT,
    categoria               TEXT,
    subcategoria            TEXT,
    imagem_url              TEXT,
    pagina_produto_url      TEXT,
    origem_enriquecimento   TEXT,  -- anvisa_cmed (GGREM ...) | crawler+claude (sites) | claude
    confirmado_anvisa_cmed  TEXT,  -- Sim | Não - mesmo sentido da coluna equivalente no xlsx
    precisa_validacao_humana TEXT,  -- Sim | Não - medicamento achado só via Claude/web
    mensagem_validacao_humana TEXT, -- texto pra fila humana; null quando Não

    -- estado geral do fluxo
    fase_atual              TEXT NOT NULL DEFAULT 'aguardando_cmed',
    -- aguardando_cmed | aguardando_crawler | aguardando_batch_enriquecimento |
    -- aguardando_batch_formatacao | aguardando_batch_tarja | concluido | nao_localizado

    precisa_verificar_tarja BOOLEAN,  -- false quando confirmado_anvisa_cmed=Sim (tarja ja e verdade absoluta)

    -- resultado da tabela oficial da ANVISA (fase -1, sincrono, sem custo de
    -- token, verdade absoluta quando achar - ver verificar_cmed())
    status_cmed             TEXT NOT NULL DEFAULT 'pendente',  -- pendente | achou | nao_achou
    dados_cmed              JSONB,                              -- linha da tabela medicamentos, se achou

    -- resultado do crawler (fase 0, sincrono, sem custo de token)
    status_crawler          TEXT NOT NULL DEFAULT 'pendente',  -- pendente | achou | nao_achou
    fontes_crawler          TEXT,                               -- ex: "drogasil,pacheco"
    dados_crawler           JSONB,                               -- ProductResult consolidado

    tokens_utilizados       INTEGER NOT NULL DEFAULT 0,
    tokens_cache_gravados   INTEGER NOT NULL DEFAULT 0,
    tokens_cache_lidos      INTEGER NOT NULL DEFAULT 0,

    criado_em               TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS batches (
    id                    SERIAL PRIMARY KEY,
    batch_id_anthropic    TEXT UNIQUE NOT NULL,  -- id retornado por client.messages.batches.create
    fase                  TEXT NOT NULL,          -- enriquecimento | tarja
    status                TEXT NOT NULL DEFAULT 'em_andamento',  -- em_andamento | concluido | erro
    total_requests        INTEGER NOT NULL DEFAULT 0,
    criado_em             TIMESTAMPTZ NOT NULL DEFAULT now(),
    concluido_em          TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS batch_items (
    id           SERIAL PRIMARY KEY,
    batch_id     INTEGER NOT NULL REFERENCES batches(id),
    ean          TEXT NOT NULL REFERENCES produtos(ean),
    custom_id    TEXT NOT NULL,   -- o custom_id usado na requisicao do batch (costuma ser o proprio EAN + fase)
    resultado    JSONB,           -- resposta bruta desse item, depois de buscar batches.results()
    processado   BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (batch_id, custom_id)
);

CREATE INDEX IF NOT EXISTS idx_produtos_fase ON produtos (fase_atual);
CREATE INDEX IF NOT EXISTS idx_batch_items_batch ON batch_items (batch_id);
"""

# pra tabelas criadas antes da integração com a CMED - SCHEMA_SQL acima só
# cria colunas em tabela nova (CREATE TABLE IF NOT EXISTS não altera uma
# tabela já existente). Tudo idempotente, seguro rodar de novo.
MIGRACOES_SQL = """
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS status_cmed TEXT NOT NULL DEFAULT 'pendente';
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS dados_cmed JSONB;
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS confirmado_anvisa_cmed TEXT;
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS precisa_validacao_humana TEXT;
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS mensagem_validacao_humana TEXT;
ALTER TABLE produtos ALTER COLUMN fase_atual SET DEFAULT 'aguardando_cmed';
"""


def criar_tabelas():
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(MIGRACOES_SQL)

            # linhas que já existiam antes da CMED entrar no fluxo e ainda não
            # foram tocadas pelo crawler - manda pra fase_atual=aguardando_cmed
            # pra passarem pela verificação oficial (grátis) antes do crawler.
            # Só afeta quem está 100% intocado (status_crawler ainda
            # 'pendente'), então nunca sobrescreve trabalho já feito.
            cur.execute(
                """
                UPDATE produtos
                SET fase_atual = 'aguardando_cmed'
                WHERE fase_atual = 'aguardando_crawler' AND status_crawler = 'pendente'
                """
            )
            linhas_movidas = cur.rowcount
        conn.commit()
    print("Tabelas criadas/confirmadas: produtos, batches, batch_items.")
    if linhas_movidas:
        print(f"{linhas_movidas} linha(s) intocada(s) movida(s) pra aguardando_cmed.")


def carregar_eans(caminho_xlsx):
    """
    Le o xlsx de entrada (EAN, Nome do produto) e insere na tabela produtos,
    ignorando EANs ja existentes (idempotente - pode rodar de novo sem
    duplicar).
    """
    df = pd.read_excel(caminho_xlsx)
    with conectar() as conn:
        with conn.cursor() as cur:
            inseridos = 0
            for _, row in df.iterrows():
                ean = str(row["EAN"]).strip()
                nome = str(row["Nome do produto"] or "").strip().lower()
                if nome == "nan":
                    nome = ""
                cur.execute(
                    """
                    INSERT INTO produtos (ean, nome_produto)
                    VALUES (%s, %s)
                    ON CONFLICT (ean) DO NOTHING
                    """,
                    (ean, nome),
                )
                inseridos += cur.rowcount
        conn.commit()
    print(f"{inseridos} EAN(s) novo(s) inserido(s) de {len(df)} no arquivo.")


def contar_por_fase():
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fase_atual, count(*) FROM produtos GROUP BY fase_atual ORDER BY 1"
            )
            for fase, qtd in cur.fetchall():
                print(f"  {fase}: {qtd}")


def _carregar_indice_cmed():
    """
    Carrega a tabela medicamentos inteira numa vez e monta um índice
    {ean_normalizado: linha} - muito mais rápido que uma consulta por EAN
    quando há muitas linhas pra verificar (produtos pode ter centenas de
    milhares de linhas; a CMED tem só ~26 mil).
    """
    campos = cmed.CAMPOS + ("ean_1", "ean_2", "ean_3")
    indice = {}
    with cmed.conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(campos)} FROM medicamentos")
            for linha in cur.fetchall():
                registro = dict(zip(campos, linha))
                for chave_ean in ("ean_1", "ean_2", "ean_3"):
                    valor = registro.get(chave_ean)
                    if valor:
                        indice[valor] = registro
    return indice


def verificar_cmed():
    """
    Verifica todo produto com fase_atual='aguardando_cmed' contra a tabela
    oficial da ANVISA (medicamentos). Se achar, o EAN é tratado como verdade
    absoluta: tipo_cadastro/registro_ms/fabricante/generico/tarja vêm direto
    de lá, sem gastar token nenhum, e a fase de verificação dedicada de tarja
    (aguardando_batch_tarja) nunca é necessária pra esse produto - só falta
    uma formatação leve de título/composição/categoria
    (aguardando_batch_formatacao). Se não achar, cai pra aguardando_crawler
    (fluxo normal, sem mudança nenhuma).
    """
    indice = _carregar_indice_cmed()
    print(f"{len(indice)} EAN(s) indexados da tabela medicamentos.")

    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ean FROM produtos WHERE fase_atual = 'aguardando_cmed'")
            pendentes = [row[0] for row in cur.fetchall()]

        achados = 0
        with conn.cursor() as cur:
            for ean in pendentes:
                medicamento = indice.get(cmed.normalizar_ean(ean))
                if medicamento is None:
                    cur.execute(
                        """
                        UPDATE produtos
                        SET status_cmed = 'nao_achou', fase_atual = 'aguardando_crawler',
                            atualizado_em = now()
                        WHERE ean = %s
                        """,
                        (ean,),
                    )
                    continue

                tarja = cmed.TARJA_CMED_PARA_SCHEMA.get(medicamento["tarja"])
                generico = "Sim" if medicamento["tipo_produto"] == "Genérico" else "Não"
                origem = f"{cmed.ORIGEM_ANVISA_CMED} (GGREM {medicamento['codigo_ggrem']})"
                cur.execute(
                    """
                    UPDATE produtos
                    SET tipo_cadastro = 'Medicamento',
                        registro_ms = %s,
                        fabricante = %s,
                        generico = %s,
                        tarja = %s,
                        origem_enriquecimento = %s,
                        confirmado_anvisa_cmed = 'Sim',
                        precisa_validacao_humana = 'Não',
                        mensagem_validacao_humana = NULL,
                        precisa_verificar_tarja = false,
                        status_cmed = 'achou',
                        dados_cmed = %s,
                        fase_atual = 'aguardando_batch_formatacao',
                        atualizado_em = now()
                    WHERE ean = %s
                    """,
                    (
                        medicamento["registro"],
                        medicamento["laboratorio"],
                        generico,
                        tarja,
                        origem,
                        json.dumps(medicamento, ensure_ascii=False),
                        ean,
                    ),
                )
                achados += 1
        conn.commit()

    print(f"{achados} produto(s) confirmado(s) pela CMED de {len(pendentes)} verificado(s).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("criar-tabelas", help="Cria as 3 tabelas se nao existirem")

    p_carregar = sub.add_parser("carregar-eans", help="Carrega EANs de um xlsx pra tabela produtos")
    p_carregar.add_argument("arquivo", help="Caminho do xlsx (colunas EAN, Nome do produto)")

    sub.add_parser("status", help="Mostra quantos produtos estao em cada fase")

    sub.add_parser(
        "verificar-cmed",
        help="Verifica produtos aguardando_cmed contra a tabela oficial da ANVISA (medicamentos)",
    )

    args = parser.parse_args()

    if args.comando == "criar-tabelas":
        criar_tabelas()
    elif args.comando == "carregar-eans":
        carregar_eans(args.arquivo)
    elif args.comando == "status":
        contar_por_fase()
    elif args.comando == "verificar-cmed":
        verificar_cmed()


if __name__ == "__main__":
    main()
