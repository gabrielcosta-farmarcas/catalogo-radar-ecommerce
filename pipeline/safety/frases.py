"""Retenção de receita e frase obrigatória — determinístico, sem LLM."""

from __future__ import annotations

import re

import substancias_controladas
from dominios import (
    TARJA_NAO_APLICAVEL,
    TARJA_PRETA,
    TARJA_SEM,
    TARJA_VERMELHA,
    eh_verdadeiro,
)

FRASE_VENDA_PRESCRICAO = "VENDA SOB PRESCRIÇÃO MÉDICA."
FRASE_VENDA_PRESCRICAO_RETENCAO = (
    "VENDA SOB PRESCRIÇÃO MÉDICA - SÓ PODE SER VENDIDO COM RETENÇÃO DA "
    "RECEITA."
)
FRASE_MEDICAMENTO_GERAL = (
    "ESTE É UM MEDICAMENTO. SEU USO PODE TRAZER RISCOS. PROCURE O MÉDICO E O "
    "FARMACÊUTICO. LEIA A BULA. SE PERSISTIREM OS SINTOMAS, O MÉDICO DEVERÁ SER "
    "CONSULTADO."
)
FRASE_GENERICO = "Medicamento Genérico Lei nº 9.787, de 1999."
FRASE_SUPLEMENTO = "Isento de registro conforme RDC nº 240/18."
FRASE_LEITE = (
    "O MINISTÉRIO DA SAÚDE INFORMA: O ALEITAMENTO MATERNO EVITA INFECÇÕES E "
    "ALERGIAS E É RECOMENDADO ATÉ OS DOIS ANOS DE IDADE OU MAIS."
)

FORMULA_INFANTIL_RE = re.compile(
    r"f[oó]rmula(?:s)? infantil|"
    r"leite(?:s)? (?:infantil|de in[ií]cio|de seguimento|de crescimento)|"
    r"aleitamento materno evita",
    re.IGNORECASE,
)


def resolver_retencao(tarja, principios_ativos):
    """
    Decide precisa_retencao_receita a partir da tarja já normalizada
    e dos princípios ativos. Preta → True. Sem tarja / não aplicável →
    False. Ausente → None. Vermelha → cruza substancias_controladas.
    """
    if tarja == TARJA_PRETA:
        return True
    if tarja in (TARJA_SEM, TARJA_NAO_APLICAVEL):
        return False
    if tarja is None:
        return None
    if tarja == TARJA_VERMELHA:
        return substancias_controladas.substancia_esta_controlada(principios_ativos)
    return None


def compor_frase_obrigatoria(data, tarja, is_medicamento):
    partes = []
    if eh_verdadeiro(data.get("precisa_retencao_receita")):
        partes.append(FRASE_VENDA_PRESCRICAO_RETENCAO)
    elif tarja in (TARJA_VERMELHA, TARJA_PRETA):
        partes.append(FRASE_VENDA_PRESCRICAO)
    if is_medicamento:
        partes.append(FRASE_MEDICAMENTO_GERAL)
    if is_medicamento and eh_verdadeiro(data.get("generico")):
        partes.append(FRASE_GENERICO)
    if data.get("departamento") == "Suplementos Alimentares" and not (
        data.get("categoria") == "Sistema Digestivo"
        and data.get("subcategoria") in ("Enzimas", "Probióticos")
    ):
        partes.append(FRASE_SUPLEMENTO)

    texto_produto = " ".join(
        str(data.get(campo) or "")
        for campo in (
            "titulo",
            "descricao_curta",
            "categoria",
            "subcategoria",
            "departamento",
            "frase_obrigatoria",
        )
    )
    if FORMULA_INFANTIL_RE.search(texto_produto):
        partes.append(FRASE_LEITE)

    return " ".join(partes) if partes else None
