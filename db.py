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
-- vocabulário fechado: códigos estáveis, nomes só na tabela de referência.
CREATE TABLE IF NOT EXISTS tipos_produto (
    codigo TEXT PRIMARY KEY,
    nome   TEXT NOT NULL UNIQUE
);
INSERT INTO tipos_produto (codigo, nome) VALUES
    ('medicamento', 'Medicamento'),
    ('nao_medicamento', 'Não Medicamento')
ON CONFLICT (codigo) DO UPDATE SET nome = EXCLUDED.nome;

CREATE TABLE IF NOT EXISTS tarjas (
    codigo TEXT PRIMARY KEY,
    nome   TEXT NOT NULL UNIQUE
);
INSERT INTO tarjas (codigo, nome) VALUES
    ('sem_tarja', 'Sem Tarja'),
    ('vermelha', 'Tarja Vermelha'),
    ('preta', 'Tarja Preta'),
    ('nao_aplicavel', 'Não aplicável')
ON CONFLICT (codigo) DO UPDATE SET nome = EXCLUDED.nome;

DO $$ BEGIN
    CREATE TYPE fase_produto AS ENUM ('pendente', 'concluido', 'nao_localizado');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    CREATE TYPE origem_categorizacao AS ENUM ('mapeamento_iqvia', 'mapeamento_cmed', 'ia');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- árvore oficial: produtos.categoria_id e os de-paras apontam pra categorias.id.
-- CREATE aqui (antes de produtos) pra instalação nova não falhar na FK.
CREATE TABLE IF NOT EXISTS categorias (
    id            SERIAL PRIMARY KEY,
    tipo_produto  TEXT NOT NULL REFERENCES tipos_produto(codigo),
    departamento  TEXT NOT NULL,
    categoria     TEXT NOT NULL,
    subcategoria  TEXT NOT NULL,
    ativo         BOOLEAN NOT NULL DEFAULT true
);
CREATE UNIQUE INDEX IF NOT EXISTS categorias_chave_natural ON categorias (
    tipo_produto, departamento, categoria, subcategoria
);

CREATE TABLE IF NOT EXISTS produtos (
    id                      BIGSERIAL PRIMARY KEY,
    ean                     VARCHAR(14) UNIQUE NOT NULL,
    nome_produto            TEXT NOT NULL,

    titulo                  TEXT,
    marca                   TEXT,
    fabricante              TEXT,
    tipo_produto            TEXT REFERENCES tipos_produto(codigo),
    registro_ms             TEXT,
    generico                BOOLEAN,
    tarja                   TEXT REFERENCES tarjas(codigo),
    precisa_retencao_receita BOOLEAN,
    principios_ativos       TEXT,
    descricao_curta         TEXT,
    frase_obrigatoria       TEXT,
    categoria_id            BIGINT REFERENCES categorias(id),
    origem_categorizacao    origem_categorizacao,
    imagem_url              TEXT,
    pagina_produto_url      TEXT,
    preco_pesquisado        TEXT,
    data_pesquisa           DATE,
    origem_enriquecimento   TEXT,
    confirmado_anvisa_cmed  BOOLEAN NOT NULL DEFAULT false,
    precisa_validacao_humana BOOLEAN NOT NULL DEFAULT false,
    mensagem_validacao_humana TEXT,

    fase_atual              fase_produto NOT NULL DEFAULT 'pendente',

    modelo                  TEXT,
    tokens_utilizados       INTEGER NOT NULL DEFAULT 0,
    tokens_cache_gravados   INTEGER NOT NULL DEFAULT 0,
    tokens_cache_lidos      INTEGER NOT NULL DEFAULT 0,

    criado_em               TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_produtos_fase ON produtos (fase_atual);
CREATE INDEX IF NOT EXISTS idx_produtos_categoria_id ON produtos (categoria_id);
CREATE INDEX IF NOT EXISTS idx_produtos_tipo_produto ON produtos (tipo_produto);
CREATE INDEX IF NOT EXISTS idx_produtos_validacao_humana
    ON produtos (precisa_validacao_humana) WHERE precisa_validacao_humana;

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


def criar_tabelas():
    with conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
    print("Tabelas criadas/confirmadas: categorias, produtos, produtos_historico.")


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
