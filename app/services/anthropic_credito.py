from __future__ import annotations

from anthropic import APIConnectionError, APIStatusError, Anthropic, AuthenticationError

from app.config import settings
from app.schemas.referencias import AnthropicCredito

_SINAIS_SEM_CREDITO = (
    "credit balance is too low",
    "credit balance too low",
    "purchase credits",
    "billing_error",
    "insufficient credit",
    "insufficient_quota",
    "spend limit",
    "spend cap",
    "plans & billing",
)


def _texto_erro(exc: BaseException) -> str:
    partes = [str(exc)]
    body = getattr(exc, "body", None)
    if body is not None:
        partes.append(str(body))
    mensagem = getattr(exc, "message", None)
    if mensagem:
        partes.append(str(mensagem))
    return " ".join(partes).lower()


def _eh_sem_credito(exc: APIStatusError) -> bool:
    if exc.status_code == 402:
        return True
    tipo = ""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        erro = body.get("error") or {}
        if isinstance(erro, dict):
            tipo = str(erro.get("type") or "")
    if tipo == "billing_error":
        return True
    blob = _texto_erro(exc)
    return any(sinal in blob for sinal in _SINAIS_SEM_CREDITO)


def verificar() -> AnthropicCredito:
    """
    A Anthropic não publica saldo restante na API comum.
    Esta chamada faz um ping mínimo (1 token). Se o crédito acabou, a
    própria API devolve 400/402 com 'credit balance is too low'.
    """
    if not settings.anthropic_configurada():
        return AnthropicCredito(
            ok=False,
            credito=None,
            chave_configurada=False,
            codigo="chave_ausente",
            mensagem="ANTHROPIC_API_KEY não está configurada.",
        )

    try:
        cliente = Anthropic(timeout=20.0)
        cliente.messages.create(
            model=settings.modelo_padrao,
            max_tokens=1,
            messages=[{"role": "user", "content": "ok"}],
        )
    except AuthenticationError as exc:
        return AnthropicCredito(
            ok=False,
            credito=None,
            chave_configurada=True,
            codigo="chave_invalida",
            mensagem="A API key da Anthropic foi rejeitada.",
            http_status_anthropic=getattr(exc, "status_code", 401),
        )
    except APIStatusError as exc:
        if _eh_sem_credito(exc):
            return AnthropicCredito(
                ok=False,
                credito=False,
                chave_configurada=True,
                codigo="sem_credito",
                mensagem=(
                    "Créditos da Anthropic esgotados ou limite de gasto atingido. "
                    "Recarregue em console.anthropic.com → Settings → Billing."
                ),
                http_status_anthropic=exc.status_code,
            )
        return AnthropicCredito(
            ok=False,
            credito=None,
            chave_configurada=True,
            codigo="indisponivel",
            mensagem=f"Anthropic respondeu HTTP {exc.status_code}: {exc}",
            http_status_anthropic=exc.status_code,
        )
    except APIConnectionError as exc:
        return AnthropicCredito(
            ok=False,
            credito=None,
            chave_configurada=True,
            codigo="indisponivel",
            mensagem=f"Não foi possível conectar na Anthropic: {exc}",
        )
    except Exception as exc:
        return AnthropicCredito(
            ok=False,
            credito=None,
            chave_configurada=True,
            codigo="indisponivel",
            mensagem=str(exc),
        )

    return AnthropicCredito(
        ok=True,
        credito=True,
        chave_configurada=True,
        codigo="ok",
        mensagem="A Anthropic aceitou a chamada — há crédito para usar a API.",
        http_status_anthropic=200,
    )
