"""Sinais de tipo e confiança a partir do crawler consolidado."""

from __future__ import annotations

import difflib
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


def nomes_concordam(nomes):
    """True se pelo menos 2 nomes batem (um contém o outro ou similaridade
    de sequência >= 0.55). Nomes demais divergentes = outro produto."""
    chaves = []
    for nome in nomes:
        chave = re.sub(r"\s+", " ", (nome or "")).strip().upper()
        if chave:
            chaves.append(chave)
    if len(chaves) < 2:
        return False
    base = chaves[0]
    for outro in chaves[1:]:
        if base in outro or outro in base:
            continue
        if difflib.SequenceMatcher(None, base, outro).ratio() < 0.55:
            return False
    return True


def eh_confiavel(resultado, fontes):
    """
    Medicamento: exige registro_ms ou princípio_ativo (campos que só site com
    ficha técnica farmacêutica de verdade expõe). Não-medicamento nunca tem
    esses campos, então usa como sinal de confiança 2+ sites com EAN
    conferido E nomes parecidos.
    """
    if any(resultado.get(c) for c in CAMPOS_CONFIANCA):
        return True
    conferidos = resultado.get("_fontes_ean_conferido") or []
    if len(conferidos) < FONTES_MINIMAS_SEM_CAMPOS_REGULATORIOS:
        return False
    return nomes_concordam(resultado.get("_nomes") or [])


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
