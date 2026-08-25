# Catálogo Radar E-commerce

## Backlog

1. [ ] Mapear as tabelas do banco (`produtos`, `anvisa_medicamentos`, `abcfarma_medicamentos`, `iqvia_produtos`, `categorias`, entre outras usadas em `db.py` / `app/db.py`) e entender onde dá pra usar FK entre elas e o que precisa ser padronizado antes disso.







## O que o projeto faz

Pipeline e API para enriquecer automaticamente o cadastro de produtos de e-commerce a partir do EAN — nome, descrição, categoria, imagem e dados regulatórios de medicamentos — cruzando bases oficiais, crawlers de concorrentes e busca agentic via Claude.

Dado um EAN pendente de cadastro, o pipeline (`enrich_com_crawler.py`) tenta preencher os dados do produto em camadas, da mais barata/confiável para a mais cara, parando na primeira que encontra algo aproveitável:

1. **CMED** (`carregar_cmed.py` / `cmed.py`) — base oficial ANVISA/CMED. Se o EAN está lá, é medicamento com certeza, tarja incluída.
2. **ABCFarma** (`carregar_abcfarma.py` / `abcfarma.py`) — segunda fonte oficial; confirma medicamento mas não a tarja.
3. **IQVIA** (`carregar_iqvia.py` / `iqvia.py`) — catálogo de parceiro, cobre também não-medicamento (cosmético, alimento etc.) e distingue RX de MIP.
4. **Crawler** (`crawler/`) — raspa sites de farmácias concorrentes (Araujo, Drogal, Drogaria Pacheco, Drogaria SP, Panvel, Raia/Drogasil, Sara/bulário, Ultrafarma, Venancio, VTEX) por EAN, sem custo.
5. **Claude** (`enrich_produtos.py`) — busca agentic completa (web search/fetch) via API Anthropic, usada só quando as camadas acima não resolvem.

Cada produto grava sua `origem_enriquecimento` e, quando o dado não vem de fonte oficial suficientemente confiável (ex.: medicamento confirmado só via Claude, ou tarja não confirmada por bulário), é marcado com `precisa_validacao_humana=Sim` para revisão antes de ir ao e-commerce.

A categorização segue uma árvore oficial (departamento → categoria → subcategoria), armazenada no Postgres e carregada por `carregar_categorias.py`.

## Componentes

- **API HTTP** (`app/`, FastAPI) — expõe o pipeline para o frontend em `/api/v1`: fila de produtos, ficha detalhada, disparo de enriquecimento (síncrono ou por job), consulta rápida às fontes oficiais (sem Claude) e árvore de categorias. Documentação Swagger em `/docs`.
- **Scripts de carga** (`carregar_*.py`) — importam as bases oficiais (CMED, ABCFarma, IQVIA, categorias, substâncias controladas) para o Postgres.
- **Crawler** (`crawler/`) — adapters por farmácia concorrente, usados como fonte gratuita antes de acionar o Claude.
- **Protótipo de frontend** (`prototipo-frontend/`) — página estática de demonstração do radar de catálogo.
- **Banco de dados** — Postgres via Docker Compose (`docker-compose.yml`), acesso em `db.py` / `app/db.py`.

## Como rodar

```bash
docker compose up -d
pip install -r requirements.txt
```

Configure `ANTHROPIC_API_KEY` num arquivo `.env` na raiz (nunca comitar a chave).

Subir a API:

```bash
uvicorn api:app --reload
```

Rodar o pipeline via CLI:

```bash
python enrich_com_crawler.py --eans 7891234567890,7899876543210 --limit 20
```
