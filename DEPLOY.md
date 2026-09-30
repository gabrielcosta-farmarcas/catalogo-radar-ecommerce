# Deploy no Coolify

Só a API (FastAPI) sobe no Coolify. O Postgres é um recurso gerenciado separado, e o pipeline em lote
(`enrich_com_crawler.py`, `db.py carregar-lote`) continua rodando fora, apontando para o banco de produção.

## 1. Postgres gerenciado
1. No Coolify: **New Resource → Database → PostgreSQL 16**. Defina usuário `cadastro`, senha e banco `cadastro_produtos`.
2. Anote o **hostname interno** do recurso (aparece na tela do banco); ele vai em `PG_HOST`.
3. Ative o backup agendado do recurso.

## 2. Levar os dados do banco local (304 MB)
```bash
# no Mac, com o docker-compose local no ar
docker exec cadastro-produtos-db pg_dump -U cadastro -d cadastro_produtos -Fc > catalogo.dump
scp catalogo.dump usuario@servidor-coolify:/tmp/
```
No servidor do Coolify (o container do Postgres aparece em `docker ps`):
```bash
docker cp /tmp/catalogo.dump <container-postgres>:/tmp/
docker exec <container-postgres> pg_restore -U cadastro -d cadastro_produtos --no-owner --clean --if-exists /tmp/catalogo.dump
```
Banco novo e vazio? Use `python db.py criar-tabelas` e os `carregar_*.py` com os xlsx de referência.

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
```bash
curl https://<dominio>/health   # {"ok":true,"postgres":true,"anthropic_key":true,...}
```
`/docs` abre o Swagger.
