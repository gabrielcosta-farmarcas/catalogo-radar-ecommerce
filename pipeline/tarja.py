"""Ritual único: crawler (Sara) → verify_tarja_registro se ainda vazio."""

from __future__ import annotations

import dominios
from pipeline.safety.frases import compor_frase_obrigatoria, resolver_retencao

ALLOWED_TARJA = set(dominios.TARJAS)


def aplicar_resultado_verify_tarja(
    data,
    resultado_verif,
    ean,
    *,
    atualizar_registro_ms="if_empty",
):
    """
    Aplica o JSON da verificação dedicada. Não chama a API.

    atualizar_registro_ms:
      False       — não mexe em registro_ms (ABCFarma já traz MS)
      "if_empty"  — preenche só se o cadastro ainda não tem MS (Tarjados/IQVIA)
      "overwrite" — grava o valor devolvido, inclusive None (Claude puro)
    """
    if resultado_verif is None:
        print(
            f"  [aviso] verificação dedicada de tarja/registro_ms "
            f"falhou para EAN {ean} - mantendo valor original sem "
            f"essa confirmação extra."
        )
        return data

    if not resultado_verif.get("confirmado"):
        print(
            f"  [aviso] verificação dedicada não confirmou "
            f"tarja/registro_ms para EAN {ean} - zerando por "
            f"segurança (era: tarja={data.get('tarja')!r}, "
            f"registro_ms={data.get('registro_ms')!r})."
        )
        data["tarja"] = None
        if atualizar_registro_ms:
            data["registro_ms"] = None
        data["precisa_retencao_receita"] = resolver_retencao(
            None, data.get("principios_ativos")
        )
        data["frase_obrigatoria"] = compor_frase_obrigatoria(data, None, True)
        return data

    tarja_verificada = resultado_verif.get("tarja")
    if tarja_verificada is not None and tarja_verificada not in ALLOWED_TARJA:
        print(
            f"  [aviso] verificação dedicada devolveu tarja fora do "
            f"vocabulário para EAN {ean} ({tarja_verificada!r}) - zerada."
        )
        tarja_verificada = None
    if tarja_verificada != data.get("tarja"):
        print(
            f"  [info] tarja corrigida por verificação "
            f"dedicada para EAN {ean}: "
            f"{data.get('tarja')!r} -> {tarja_verificada!r}"
        )
    data["tarja"] = tarja_verificada

    registro_verificado = resultado_verif.get("registro_ms")
    if atualizar_registro_ms == "overwrite":
        if registro_verificado != data.get("registro_ms"):
            print(
                f"  [info] registro_ms corrigido por verificação "
                f"dedicada para EAN {ean}: "
                f"{data.get('registro_ms')!r} -> {registro_verificado!r}"
            )
        data["registro_ms"] = registro_verificado
    elif atualizar_registro_ms == "if_empty":
        if registro_verificado and not data.get("registro_ms"):
            data["registro_ms"] = registro_verificado

    data["precisa_retencao_receita"] = resolver_retencao(
        tarja_verificada, data.get("principios_ativos")
    )
    data["frase_obrigatoria"] = compor_frase_obrigatoria(data, tarja_verificada, True)
    return data


def confirmar_tarja_se_ausente(
    data,
    ean,
    client,
    model,
    principios_ativos,
    usage,
    *,
    verify_tarja=True,
    atualizar_registro_ms="if_empty",
    zerar_se_nao_confirmado=False,
):
    """
    Se ainda não há tarja e verify_tarja, chama verify_tarja_registro.

    ABCFarma / Tarjados-MED / IQVIA-RX: só preenche se confirmado=True.
    Claude puro: se não confirmou, zera tarja e MS (zerar_se_nao_confirmado).
    Falha de API (None) nunca zera — mantém o valor que o safety deixou.
    """
    if not verify_tarja or data.get("tarja"):
        return data, usage

    import enrich_produtos as ep

    resultado_verif, usage_verif = ep.verify_tarja_registro(
        client, model, ean, data.get("titulo"), data.get("marca"), principios_ativos
    )
    usage["tokens"] += usage_verif["tokens"]
    usage["cache_creation"] += usage_verif["cache_creation"]
    usage["cache_read"] += usage_verif["cache_read"]

    if resultado_verif is None:
        print(
            f"  [aviso] verificação dedicada de tarja/registro_ms "
            f"falhou para EAN {ean} - mantendo valor original sem "
            f"essa confirmação extra."
        )
        return data, usage

    if resultado_verif.get("confirmado"):
        aplicar_resultado_verify_tarja(
            data, resultado_verif, ean, atualizar_registro_ms=atualizar_registro_ms
        )
    elif zerar_se_nao_confirmado:
        aplicar_resultado_verify_tarja(
            data, resultado_verif, ean, atualizar_registro_ms=atualizar_registro_ms
        )
    return data, usage
