from __future__ import annotations

from fastapi import APIRouter

from app.schemas.referencias import AnthropicCredito, Health
from app.services import anthropic_credito as anthropic_credito_service
from app.services import health as health_service

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
def health() -> Health:
    return health_service.status()


@router.get(
    "/anthropic/credito",
    response_model=AnthropicCredito,
    summary="Verifica se a Anthropic ainda tem crédito",
    description=(
        "Faz um ping de 1 token. A Anthropic não informa o saldo restante na API "
        "comum; se o crédito acabou, a chamada falha com 'credit balance is too low'."
    ),
)
def anthropic_credito() -> AnthropicCredito:
    return anthropic_credito_service.verificar()
