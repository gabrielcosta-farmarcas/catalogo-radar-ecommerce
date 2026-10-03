# Deploy no Coolify

O Postgres e um container de aplicação sobem no Coolify. **Modo terminal:** o container não serve HTTP
(`CMD ["sleep", "infinity"]` no Dockerfile, sem domínio, sem porta publicada); o pipeline em lote
(`enrich_com_crawler.py`, `db.py carregar-lote`) roda pelo terminal do container (ver passo 5).
Para voltar a expor a API, veja o comentário no fim do `Dockerfile`.

## 1. Postgres gerenciado
1. No Coolify: **New Resource → Database → PostgreSQL 16**. Defina usuário `cadastro`, senha e banco `cadastro_produtos`.
2. Anote o **hostname interno** do recurso (aparece na tela do banco); ele vai em `PG_HOST`.
3. Ative o backup agendado do recurso.

## 2. Levar as tabelas (estrutura + dados de referência, sem produtos)
Sobem as 15 tabelas, mas só as de referência levam dados (CMED, ABCFarma, IQVIA, tarjados, substâncias,
categorias, mapeamentos e vocabulários). `produtos` e `produtos_historico` vão vazias: o lote é carregado
no próprio servidor.
```bash
# no Mac, com o docker-compose local no ar (gera dumps/catalogo_referencia.dump, ~11 MB, ignorado pelo git)
mkdir -p dumps
docker exec cadastro-produtos-db pg_dump -U cadastro -d cadastro_produtos -Fc --no-owner \
  --exclude-table-data=produtos --exclude-table-data=produtos_historico > dumps/catalogo_referencia.dump
scp dumps/catalogo_referencia.dump usuario@servidor-coolify:/tmp/
```
No servidor do Coolify (o container do Postgres aparece em `docker ps`):
```bash
docker cp /tmp/catalogo_referencia.dump <container-postgres>:/tmp/
docker exec <container-postgres> pg_restore -U cadastro -d cadastro_produtos --no-owner /tmp/catalogo_referencia.dump
```
Conferir: `python db.py status` no terminal da aplicação (0 produtos) e
`select count(*) from anvisa_medicamentos;` (26.001 no dump atual).

**Sem SSH, pela UI do Coolify:** banco → **Import Backup** → envie o `.dump` → *Restore From File*. Pegadinhas:
o diálogo de confirmação abre fora da área visível (role a página); ele pede digitar a frase literal
`Confirm Deletion` (não o nome do recurso) e depois a senha de login do Coolify. Sem isso o clique não faz nada
e nenhuma mensagem de erro aparece.

No Coolify o banco criado usa, por padrão, usuário `postgres` e banco `postgres` (não `cadastro`): ajuste
`PG_USER`/`PG_DB` da aplicação. `PG_HOST` é o UUID do recurso do banco (aparece na URL interna).

## 3. Aplicação
**New Resource → Application** apontando para este repositório, com:

| Configuração | Valor |
|---|---|
| Build pack | Dockerfile |
| Porta exposta | 8000 |
| Healthcheck | `GET /health` |
| Storage (volume persistente) | mount em `/app/imagens` |
| Variáveis | as de `.env.example` |

- **Domínio interno:** a API não tem autenticação, e `POST /api/v1/produtos/{ean}/enriquecer` gasta token da Anthropic.
  Deixe o domínio acessível só pela rede interna/VPN.
- **1 worker:** os jobs de enriquecimento ficam em memória (`app/jobs.py`). Não aumente `--workers` nem replique o container.
- **Volume de imagens:** sem ele, as fotos gravadas no enriquecimento somem a cada deploy.
- **Arquitetura:** o build acontece no servidor do Coolify (amd64 em geral); o Dockerfile não depende da arquitetura.

## 4. Verificação
No terminal da aplicação (não há domínio nem `/health` HTTP no modo terminal):
```bash
python db.py status
python -c "import anthropic;print(anthropic.__version__)"   # 0.121.0
```
De fora, nada deve responder: sem domínio na aplicação e *Public access* do banco em Private.

## 5. Rodar o pipeline pelo terminal do Coolify
Na aplicação: **Terminal** (ou `docker exec -it <container-app> bash` no servidor). O container já tem o
código e as variáveis `PG_*` / `ANTHROPIC_API_KEY`.
```bash
python db.py status
python db.py carregar-lote /app/lote.xlsx --col-ean EAN --col-nome Descricao
python enrich_com_crawler.py --help
```
- O xlsx do lote não está na imagem (`*.xlsx` no `.dockerignore`): envie com `scp` + `docker cp <container-app>:/app/`.
- O terminal do Coolify cai se a aba fechar. Para execuções longas use `nohup ... > /app/imagens/pipeline.log 2>&1 &`
  ou `tmux`/`screen` (instale no container se precisar).
- O enriquecimento grava fotos em `/app/imagens`, o volume persistente do passo 3.
