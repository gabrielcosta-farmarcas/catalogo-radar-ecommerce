from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from swagger_ui_bundle import swagger_ui_path

from app.config import settings
from app.errors import AppError
from app.routers import categorias, enriquecimento, fontes, health, produtos
from app.schemas.common import ErroResposta

logger = logging.getLogger(__name__)


def _erro(status_code: int, code: str, message: str, details=None) -> JSONResponse:
    corpo = {"error": {"code": code, "message": message}}
    if details is not None:
        corpo["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=corpo)


def _swagger_html(title: str) -> str:
    # Assets locais (swagger-ui-bundle) — o /docs padrão do FastAPI puxa jsDelivr e
    # fica em branco se o CDN não carregar.
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{title} - Swagger</title>
  <link rel="stylesheet" href="/swagger-static/swagger-ui.css"/>
  <link rel="icon" href="/swagger-static/favicon-32x32.png"/>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="/swagger-static/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({{
      url: "/openapi.json",
      dom_id: "#swagger-ui",
      deepLinking: true,
      presets: [SwaggerUIBundle.presets.apis],
      layout: "BaseLayout",
      tryItOutEnabled: true
    }});
  </script>
</body>
</html>
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=(
            "API do catálogo para o frontend. Contrato versionado em /api/v1. "
            "Enriquecimento de um EAN pode levar mais de 60s — use timeout de 120s."
        ),
        openapi_tags=[
            {"name": "health", "description": "Disponibilidade da API, Postgres e crédito da Anthropic."},
            {"name": "produtos", "description": "Fila, ficha, histórico e estatísticas do catálogo."},
            {"name": "enriquecimento", "description": "Roda o pipeline (CMED → ABCFarma → IQVIA → crawler → Claude)."},
            {"name": "fontes", "description": "Consulta rápida às bases oficiais, sem Claude."},
            {"name": "categorias", "description": "Árvore oficial para filtros e formulários."},
        ],
        docs_url=None,
        redoc_url=None,
        responses={
            422: {"model": ErroResposta},
            500: {"model": ErroResposta},
        },
    )
    # Swagger UI 4 (swagger-ui-bundle) só aceita openapi: 3.0.n, não 3.1.0.
    app.openapi_version = "3.0.2"
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return _erro(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return _erro(422, "validation_error", "Payload inválido.", jsonable_encoder(exc.errors()))

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _erro(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("erro não tratado")
        return _erro(500, "internal_error", "Erro interno.")

    prefix = settings.api_prefix
    app.include_router(health.router)
    app.include_router(health.router, prefix=prefix)
    app.include_router(produtos.router, prefix=prefix)
    app.include_router(enriquecimento.router, prefix=prefix)
    app.include_router(fontes.router, prefix=prefix)
    app.include_router(categorias.router, prefix=prefix)

    app.mount(
        "/swagger-static",
        StaticFiles(directory=str(swagger_ui_path)),
        name="swagger-static",
    )

    @app.get("/", include_in_schema=False)
    def raiz() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    @app.get("/docs", include_in_schema=False)
    def swagger() -> HTMLResponse:
        return HTMLResponse(_swagger_html(settings.app_name))

    return app


app = create_app()
