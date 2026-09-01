"""Correção determinística de sal no título (medicamento)."""

from __future__ import annotations

import re

_SAIS_CONHECIDOS = (
    "CLORIDRATO", "BROMIDRATO", "BROMIDRETO", "MALEATO", "BESILATO",
    "SUCCINATO", "HEMIFUMARATO", "FUMARATO", "MESILATO", "OXALATO",
    "HEMITARTARATO", "TARTARATO", "CITRATO", "FOSFATO", "SULFATO",
)


def sal_no_texto(texto):
    texto_upper = (texto or "").upper()
    for sal in _SAIS_CONHECIDOS:
        if re.search(rf"\b{sal}\b", texto_upper):
            return sal
    return None


def corrigir_sal_titulo(data, ean):
    """
    Corrige o título que troca o sal do princípio ativo por outro mais
    "familiar" (ex: Cloridrato vs Maleato de Midazolam). Só o caso de um
    único princípio ativo; múltiplos só avisam.
    """
    titulo = data.get("titulo") or ""
    principios = data.get("principios_ativos") or ""
    sal_titulo = sal_no_texto(titulo)
    if not sal_titulo:
        return

    if "+" in principios or "," in principios:
        print(
            f"  [aviso] título de EAN {ean} tem sal ({sal_titulo!r}) e "
            f"principios_ativos tem múltiplos componentes - confira "
            f"manualmente se bate: titulo={titulo!r} | "
            f"principios_ativos={principios!r}"
        )
        return

    sal_principio = sal_no_texto(principios)
    if sal_principio and sal_principio != sal_titulo:
        match_original = re.search(rf"\b{sal_principio}\b", principios, re.IGNORECASE)
        substituto = match_original.group(0) if match_original else sal_principio.capitalize()
        titulo_corrigido = re.sub(
            rf"\b{sal_titulo}\b", substituto, titulo, flags=re.IGNORECASE
        )
        print(
            f"  [aviso] título de EAN {ean} trocou o sal do princípio ativo "
            f"(tinha {sal_titulo!r}, principios_ativos confirma "
            f"{sal_principio!r}) - corrigido: {titulo!r} -> {titulo_corrigido!r}"
        )
        data["titulo"] = titulo_corrigido
