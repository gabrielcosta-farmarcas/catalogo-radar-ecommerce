"""Parser determinístico de composição (CMED e origens que reusam o formato)."""

from __future__ import annotations

import re

_SUFIXOS_HIDRATACAO = (
    " MONOIDRATADA",
    " MONOIDRATADO",
    " DI-HIDRATADA",
    " DI-HIDRATADO",
    " DIHIDRATADA",
    " DIHIDRATADO",
    " HEMI-HIDRATADO",
    " HEMIHIDRATADO",
    " TRI-HIDRATADO",
    " ANIDRO",
    " ANIDRA",
)
_PALAVRAS_PEQUENAS = {"DE", "DA", "DO", "DAS", "DOS", "E"}
_CONC_CMED_RE = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(MCG|MG|G|ML|L|UI|MUI|%)(?:\s*/\s*(ML|G|L))?\s*(?:\+\s*)?",
    re.IGNORECASE,
)


def split_substancias_cmed(substancia):
    return [s.strip() for s in (substancia or "").split(";") if s.strip()]


def normalizar_nome_substancia_cmed(bruto):
    texto = re.sub(r"\s+", " ", bruto or "").strip().upper()
    for sufixo in _SUFIXOS_HIDRATACAO:
        if texto.endswith(sufixo):
            return texto[: -len(sufixo)].strip()
    return texto


def nome_principio_cmed(bruto):
    texto = normalizar_nome_substancia_cmed(bruto)
    partes = []
    for i, palavra in enumerate(texto.split()):
        if i > 0 and palavra in _PALAVRAS_PEQUENAS:
            partes.append(palavra.lower())
        else:
            partes.append(palavra.capitalize())
    return " ".join(partes)


def concentracoes_apresentacao_cmed(apresentacao):
    resto = apresentacao or ""
    achadas = []
    while True:
        match = _CONC_CMED_RE.match(resto)
        if not match:
            break
        valor, unidade, denominador = match.group(1), match.group(2).lower(), match.group(3)
        conc = f"{valor}{unidade}"
        if denominador:
            conc += f"/{denominador.lower()}"
        achadas.append(conc)
        resto = resto[match.end() :]
    return achadas


def parsear_composicao_cmed(substancia, apresentacao):
    """
    Tenta montar principios_ativos sem LLM. Retorna a string se o pareamento
    for seguro; None se a apresentação for ambígua (aí o chamador cai no
    modelo). Sem concentração na apresentação devolve só os nomes - nunca
    inventa mg.
    """
    nomes = [nome_principio_cmed(s) for s in split_substancias_cmed(substancia)]
    if not nomes:
        return None
    concs = concentracoes_apresentacao_cmed(apresentacao)
    if not concs:
        return ", ".join(nomes)
    if len(concs) == len(nomes):
        return ", ".join(f"{nome} {conc}" for nome, conc in zip(nomes, concs))
    return None
