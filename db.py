"""
Camada de banco de dados da tabela `produtos` - 1 linha por EAN, com o estado
atual do fluxo (`fase_atual`) e o resultado final do enriquecimento (o que
antes era o xlsx). Módulo isolado de propósito - não importa nem é importado
por enrich_produtos.py / enrich_com_crawler.py (que têm sua própria conexão e
seus próprios helpers pra ler/escrever em `produtos`, ver DB_CONFIG em
enrich_produtos.py).

Fluxo de fases de `produtos.fase_atual`:
    pendente -> concluido | nao_localizado

A verificação contra a CMED/ANVISA acontece dentro do próprio fluxo em tempo
real (enrich_com_crawler.mapear_cmed_para_schema), não aqui - esse módulo só
gerencia a fila de EANs pendentes e o resultado final.

Uso básico:
    python db.py criar-tabelas
    python db.py carregar-eans eans_estoque_sem_venda_12m.xlsx
    python db.py status

Requer Postgres rodando (docker-compose up -d) e psycopg2-binary instalado.
"""

import argparse
import os

import pandas as pd
import psycopg2
import psycopg2.extras

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
    id                      BIGSERIAL PRIMARY KEY,
    ean                     TEXT UNIQUE NOT NULL,
    nome_produto            TEXT NOT NULL,

    -- resultado final (mesmo schema do xlsx, mesma ordem de RESULT_COLUMNS)
    titulo                  TEXT,
    marca                   TEXT,
    fabricante              TEXT,
    tipo_cadastro           TEXT,
    registro_ms             TEXT,
    generico                TEXT,
    tarja                   TEXT,
    precisa_retencao_receita TEXT,
    principios_ativos       TEXT,
    descricao_curta         TEXT,
    frase_obrigatoria       TEXT,
    departamento            TEXT,
    categoria               TEXT,
    subcategoria            TEXT,
    origem_categorizacao    TEXT,  -- mapeamento_iqvia | mapeamento_cmed (de-para revisado) | ia (decisão da IA)
    imagem_url              TEXT,
    pagina_produto_url      TEXT,
    preco_pesquisado        TEXT,
    data_pesquisa           TEXT,
    origem_enriquecimento   TEXT,  -- anvisa_cmed (GGREM ...) | crawler+claude (sites) | claude
    confirmado_anvisa_cmed  TEXT,  -- Sim | Não - mesmo sentido da coluna equivalente no xlsx
    precisa_validacao_humana TEXT,  -- Sim | Não - medicamento achado só via Claude/web
    mensagem_validacao_humana TEXT, -- texto pra fila humana; null quando Não

    -- estado geral do fluxo
    fase_atual              TEXT NOT NULL DEFAULT 'pendente',
    -- pendente | concluido | nao_localizado

    model                   TEXT,  -- model ID da Anthropic que gerou esta versão (ex: claude-haiku-4-5-20251001)
    tokens_utilizados       INTEGER NOT NULL DEFAULT 0,
    tokens_cache_gravados   INTEGER NOT NULL DEFAULT 0,
    tokens_cache_lidos      INTEGER NOT NULL DEFAULT 0,

    -- timestamps sempre por último, por convenção
    criado_em               TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_produtos_fase ON produtos (fase_atual);

-- timeline de versões de cada produto - uma linha por vez que ele foi
-- enriquecido (inclusive a primeira), gravada pelo próprio código Python em
-- enrich_produtos.salvar_resultado (não por trigger - decisão deliberada de
-- manter regra de negócio na aplicação, não no banco). Tabela sozinha já é
-- a timeline completa pra tela de acompanhamento - não precisa combinar com
-- o estado atual de produtos.
--
-- Snapshot inteiro (fase_resultado, titulo, marca, ..., tokens_*) fica em
-- `dados` (JSONB) em vez de uma coluna por campo - listar_historico
-- (app/repos/produtos.py) sempre lê a linha inteira por EAN, nunca filtra
-- por campo individual do snapshot, então um campo novo no enriquecimento
-- não exige ALTER TABLE aqui (só em produtos, que é consultada por campo).
-- produto_id/ean continuam colunas reais (referência) e versionado_em
-- também (ordenação/timestamp da linha).
CREATE TABLE IF NOT EXISTS produtos_historico (
    id             BIGSERIAL PRIMARY KEY,
    produto_id     BIGINT NOT NULL REFERENCES produtos(id),
    ean            TEXT NOT NULL,
    dados          JSONB NOT NULL,
    versionado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_produtos_historico_ean ON produtos_historico (ean);
"""

# pra tabelas criadas antes da integração com a CMED - SCHEMA_SQL acima só
# cria colunas em tabela nova (CREATE TABLE IF NOT EXISTS não altera uma
# tabela já existente). Tudo idempotente, seguro rodar de novo.
MIGRACOES_SQL = """
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS confirmado_anvisa_cmed TEXT;
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS precisa_validacao_humana TEXT;
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS mensagem_validacao_humana TEXT;
ALTER TABLE produtos ALTER COLUMN fase_atual SET DEFAULT 'pendente';
-- renomeia o valor antigo (nome preso a uma pré-checagem de CMED que não
-- existe mais, ver módulo verificar_cmed removido) pro nome atual
UPDATE produtos SET fase_atual = 'pendente' WHERE fase_atual = 'aguardando_cmed';

-- campos usados por enrich_produtos.py/enrich_com_crawler.py que ainda não
-- estavam no schema (ver RESULT_COLUMNS em enrich_produtos.py)
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS precisa_retencao_receita TEXT;
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS preco_pesquisado TEXT;
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS data_pesquisa TEXT;

-- batches/batch_items eram o esqueleto de um fluxo em lote via Batch API que
-- nunca foi implementado (nenhuma função lia/gravava neles) - removidas.
DROP TABLE IF EXISTS batch_items;
DROP TABLE IF EXISTS batches;

-- id numérico como chave primária (ean fica só UNIQUE) - migração histórica,
-- já aplicada neste banco e coberta em SCHEMA_SQL pra instalação nova. Não
-- fica mais aqui como drop+recreate porque toda FK nova pra produtos(id)
-- (ex: produtos_historico) passa a depender do índice de produtos_pkey, e
-- recriá-lo a cada `criar-tabelas` quebraria essas FKs sem necessidade.

-- status_crawler/fontes_crawler/dados_crawler eram placeholders pra uma fase
-- de crawler que nunca foi implementada aqui (nenhuma função grava neles) -
-- removidos. O crawler real (enrich_com_crawler.py) roda fora dessa máquina
-- de fases, direto no fluxo em tempo real.
ALTER TABLE produtos DROP COLUMN IF EXISTS status_crawler;
ALTER TABLE produtos DROP COLUMN IF EXISTS fontes_crawler;
ALTER TABLE produtos DROP COLUMN IF EXISTS dados_crawler;

-- sinaliza se departamento/categoria/subcategoria vieram de um de-para já
-- revisado por humano (mapeamento_categoria_iqvia/_cmed) ou de uma decisão
-- da IA na hora (sem de-para pra essa combinação, ou fonte sem de-para
-- ainda - ABCFarma/crawler/Claude puro)
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS origem_categorizacao TEXT;
ALTER TABLE produtos DROP CONSTRAINT IF EXISTS produtos_origem_categorizacao_check;
ALTER TABLE produtos ADD CONSTRAINT produtos_origem_categorizacao_check
    CHECK (origem_categorizacao IS NULL OR origem_categorizacao IN ('mapeamento_iqvia', 'mapeamento_cmed', 'ia'));

-- model ID que gerou a versão atual - ajuda a explicar divergência entre
-- execuções (ex: troca de modelo entre um reprocessamento e outro). Em
-- produtos_historico, model já vem dentro de `dados` (JSONB) - ver migração
-- de colapso do snapshot mais abaixo.
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS model TEXT;

-- status_cmed/dados_cmed/precisa_verificar_tarja só eram gravados por
-- verificar_cmed() (pré-checagem grátis contra a CMED, nunca integrada ao
-- fluxo real) - o fluxo em tempo real já faz sua própria consulta direta à
-- CMED (mapear_cmed_para_schema) e já expõe a fonte via
-- origem_enriquecimento, então esses 3 campos e a função foram removidos.
ALTER TABLE produtos DROP COLUMN IF EXISTS status_cmed;
ALTER TABLE produtos DROP COLUMN IF EXISTS dados_cmed;
ALTER TABLE produtos DROP COLUMN IF EXISTS precisa_verificar_tarja;

-- produtos_historico: colapsa o snapshot (fase_resultado, titulo, marca, ...,
-- tokens_*) em `dados` JSONB - ver CREATE TABLE acima. Condicional porque só
-- faz sentido rodar numa tabela ainda no formato antigo (coluna `titulo`
-- existindo); idempotente, roda de novo sem erro depois de já ter migrado.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'produtos_historico' AND column_name = 'titulo'
    ) THEN
        ALTER TABLE produtos_historico ADD COLUMN IF NOT EXISTS dados JSONB;
        UPDATE produtos_historico SET dados = jsonb_build_object(
            'fase_resultado', fase_resultado,
            'titulo', titulo,
            'marca', marca,
            'fabricante', fabricante,
            'tipo_cadastro', tipo_cadastro,
            'registro_ms', registro_ms,
            'generico', generico,
            'tarja', tarja,
            'precisa_retencao_receita', precisa_retencao_receita,
            'principios_ativos', principios_ativos,
            'descricao_curta', descricao_curta,
            'frase_obrigatoria', frase_obrigatoria,
            'departamento', departamento,
            'categoria', categoria,
            'subcategoria', subcategoria,
            'origem_categorizacao', origem_categorizacao,
            'imagem_url', imagem_url,
            'pagina_produto_url', pagina_produto_url,
            'preco_pesquisado', preco_pesquisado,
            'data_pesquisa', data_pesquisa,
            'origem_enriquecimento', origem_enriquecimento,
            'confirmado_anvisa_cmed', confirmado_anvisa_cmed,
            'precisa_validacao_humana', precisa_validacao_humana,
            'mensagem_validacao_humana', mensagem_validacao_humana,
            'model', model,
            'tokens_utilizados', tokens_utilizados,
            'tokens_cache_gravados', tokens_cache_gravados,
            'tokens_cache_lidos', tokens_cache_lidos
        )
        WHERE dados IS NULL;
        ALTER TABLE produtos_historico ALTER COLUMN dados SET NOT NULL;

        ALTER TABLE produtos_historico DROP COLUMN fase_resultado;
        ALTER TABLE produtos_historico DROP COLUMN titulo;
        ALTER TABLE produtos_historico DROP COLUMN marca;
        ALTER TABLE produtos_historico DROP COLUMN fabricante;
        ALTER TABLE produtos_historico DROP COLUMN tipo_cadastro;
        ALTER TABLE produtos_historico DROP COLUMN registro_ms;
        ALTER TABLE produtos_historico DROP COLUMN generico;
        ALTER TABLE produtos_historico DROP COLUMN tarja;
        ALTER TABLE produtos_historico DROP COLUMN precisa_retencao_receita;
        ALTER TABLE produtos_historico DROP COLUMN principios_ativos;
        ALTER TABLE produtos_historico DROP COLUMN descricao_curta;
        ALTER TABLE produtos_historico DROP COLUMN frase_obrigatoria;
        ALTER TABLE produtos_historico DROP COLUMN departamento;
        ALTER TABLE produtos_historico DROP COLUMN categoria;
        ALTER TABLE produtos_historico DROP COLUMN subcategoria;
        ALTER TABLE produtos_historico DROP COLUMN origem_categorizacao;
        ALTER TABLE produtos_historico DROP COLUMN imagem_url;
        ALTER TABLE produtos_historico DROP COLUMN pagina_produto_url;
        ALTER TABLE produtos_historico DROP COLUMN preco_pesquisado;
        ALTER TABLE produtos_historico DROP COLUMN data_pesquisa;
        ALTER TABLE produtos_historico DROP COLUMN origem_enriquecimento;
        ALTER TABLE produtos_historico DROP COLUMN confirmado_anvisa_cmed;
        ALTER TABLE produtos_historico DROP COLUMN precisa_validacao_humana;
        ALTER TABLE produtos_historico DROP COLUMN mensagem_validacao_humana;
        ALTER TABLE produtos_historico DROP COLUMN model;
        ALTER TABLE produtos_historico DROP COLUMN tokens_utilizados;
        ALTER TABLE produtos_historico DROP COLUMN tokens_cache_gravados;
        ALTER TABLE produtos_historico DROP COLUMN tokens_cache_lidos;
    END IF;
END $$;
"""


def criar_tabelas():
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(MIGRACOES_SQL)
        conn.commit()
    print("Tabelas criadas/confirmadas: produtos.")


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




def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("criar-tabelas", help="Cria produtos/produtos_historico se nao existirem")

    p_carregar = sub.add_parser("carregar-eans", help="Carrega EANs de um xlsx pra tabela produtos")
    p_carregar.add_argument("arquivo", help="Caminho do xlsx (colunas EAN, Nome do produto)")

    sub.add_parser("status", help="Mostra quantos produtos estao em cada fase")

    args = parser.parse_args()

    if args.comando == "criar-tabelas":
        criar_tabelas()
    elif args.comando == "carregar-eans":
        carregar_eans(args.arquivo)
    elif args.comando == "status":
        contar_por_fase()


if __name__ == "__main__":
    main()
