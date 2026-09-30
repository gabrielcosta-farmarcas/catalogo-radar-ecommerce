"""Sinais de tipo e confiança a partir do crawler consolidado."""

from __future__ import annotations

import re

REGISTRO_MS_PLACEHOLDERS = {
    "ISENTO", "N/A", "NA", "NAO SE APLICA", "NÃO SE APLICA",
    "NAO POSSUI", "NÃO POSSUI", "-", "0", "",
}

CAMPOS_CONFIANCA = ("ms_register", "active_ingredient")
FONTES_MINIMAS_SEM_CAMPOS_REGULATORIOS = 2

_CATEGORIA_INDICA_MEDICAMENTO_RE = re.compile(r"rem[eé]dio|medicamento", re.IGNORECASE)


def registro_ms_valido(valor):
    if not valor:
        return None
    if str(valor).strip().upper() in REGISTRO_MS_PLACEHOLDERS:
        return None
    return valor


def eh_confiavel(resultado, fontes):
    """
    Medicamento: exige registro_ms ou princípio_ativo (campos que só site com
    ficha técnica farmacêutica de verdade expõe). Não-medicamento nunca tem
    esses campos, então usa como sinal de confiança 2+ sites com EAN
    conferido - ean_conferido já é o próprio site confirmando, na ficha do
    produto, que aquele EAN é dele (ver adapters em crawler/adapters/*.py),
    não uma busca aproximada, então o EAN batendo em 2+ fontes independentes
    é o sinal - o nome pode variar entre varejistas (apelido comercial,
    reordenação) sem indicar produto errado.
    """
    if any(resultado.get(c) for c in CAMPOS_CONFIANCA):
        return True
    conferidos = resultado.get("_fontes_ean_conferido") or []
    return len(conferidos) >= FONTES_MINIMAS_SEM_CAMPOS_REGULATORIOS


def indica_medicamento(resultado):
    """
    True se o crawler achou algum sinal de que o produto é medicamento.
    Usa ficha técnica (MS, princípio, tarja, prescrição) ou breadcrumb
    de categoria — nunca a descrição livre.
    """
    if any(
        resultado.get(c)
        for c in ("ms_register", "active_ingredient", "tarja", "prescricao_detalhe")
    ):
        return True
    return bool(_CATEGORIA_INDICA_MEDICAMENTO_RE.search(resultado.get("category") or ""))
