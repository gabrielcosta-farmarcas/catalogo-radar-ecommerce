FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# fotos gravadas pelo enriquecimento (imagens/medicamentos e imagens/nao-medicamentos).
# Montar um volume persistente do Coolify neste caminho, senão somem a cada deploy.
# Roda como root de propósito: volumes do Coolify nascem root:root e salvar_imagem_local
# engole o erro de escrita, então com usuário sem permissão as fotos sumiriam em silêncio.
VOLUME ["/app/imagens"]

# Modo terminal: nenhum processo escuta porta (a API HTTP não tem autenticação e o endpoint
# /enriquecer gasta token da Anthropic). O container só fica de pé para o terminal do Coolify
# rodar os scripts (db.py, enrich_com_crawler.py, carregar_*.py). Sem EXPOSE e sem healthcheck HTTP.
# O HEALTHCHECK abaixo é trivial de propósito: `HEALTHCHECK NONE` faz o Coolify tentar ler
# .State.Health.Status (inexistente) e o deploy falha.
# Para voltar a servir a API, troque o CMD abaixo por:
#   CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
# (1 worker: o estado dos jobs fica em memória, app/jobs.py) e restaure o HEALTHCHECK em /health.
HEALTHCHECK --interval=60s --timeout=3s --retries=1 CMD true
CMD ["sleep", "infinity"]
