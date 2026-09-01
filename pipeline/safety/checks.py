"""Travas pós-hoc. Delegam invariantes de tipo para a policy."""

from __future__ import annotations

from datetime import date

import categorias
import dominios
from dominios import (
    ORIGEM_ABCFARMA,
    ORIGEM_ANVISA_CMED,
    ORIGEM_IQVIA,
    ORIGEM_TARJADOS,
    eh_medicamento,
    origem_codigo,
)
from pipeline.policies.base import policy_for
from pipeline.policies.medicamento import CONCENTRACAO_RE, TITULO_MAX_RECOMENDADO
from pipeline.safety.frases import compor_frase_obrigatoria

CAMPOS_DEPENDENTES_DE_FONTE = (
    "registro_ms",
    "principios_ativos",
    "departamento",
    "categoria",
    "tarja",
    "preco_pesquisado",
)

ALLOWED_TARJA = set(dominios.TARJAS)


def validar_categorizacao(data):
    departamento = data.get("departamento")
    categoria = data.get("categoria")
    subcategoria = data.get("subcategoria")
    categoria_id = data.get("categoria_id")

    if not categoria_id and not departamento and not categoria and not subcategoria:
        categorias.aplicar_folha(data, None)
        return True, None

    if not categorias.carregar_indice()["combinacoes"]:
        if categoria_id:
            categorias.aplicar_folha(data, categoria_id)
        return True, None

    tipo = data.get("tipo_produto")
    if categoria_id:
        folha = categorias.por_id(categoria_id)
        if folha and folha.get("tipo_produto") == tipo:
            categorias.aplicar_folha(data, folha["id"])
            return True, None
        categorias.aplicar_folha(data, None)
        return False, (
            f"tipo_cadastro={tipo!r} categoria_id={categoria_id!r} não existe "
            "na árvore oficial"
        )

    resolvido = categorias.resolver_id(tipo, departamento, categoria, subcategoria)
    if resolvido:
        categorias.aplicar_folha(data, resolvido)
        return True, None
    categorias.aplicar_folha(data, None)
    return False, (
        f"tipo_cadastro={tipo!r} departamento={departamento!r} "
        f"categoria={categoria!r} subcategoria={subcategoria!r} não existe "
        "na árvore oficial"
    )


def apply_safety_checks(data, ean):
    """
    Travas de segurança pós-hoc. Invariantes de medicamento vs não-medicamento
    saem da ProductPolicy; o restante (data, árvore, frase, fabricante) é comum.
    """
    dominios.normalizar_cadastro(data)
    policy = policy_for(data)
    is_medicamento = eh_medicamento(data)

    policy.apply_invariants(data, ean)

    origem = origem_codigo(data)
    origem_confiavel_sem_url = origem in (
        ORIGEM_ANVISA_CMED,
        ORIGEM_ABCFARMA,
        ORIGEM_TARJADOS,
        ORIGEM_IQVIA,
    )

    data["data_pesquisa"] = date.today().isoformat() if data.get("preco_pesquisado") else None

    categorizacao_ok, motivo_categorizacao = validar_categorizacao(data)
    if not categorizacao_ok:
        print(f"  [aviso] categorização inválida para EAN {ean} ({motivo_categorizacao}) - zerada.")
        categorias.aplicar_folha(data, None)
        data["_categorizacao_invalida"] = motivo_categorizacao

    tarja = data.get("tarja")
    frase_final = compor_frase_obrigatoria(data, tarja, is_medicamento)
    if frase_final != data.get("frase_obrigatoria"):
        print(
            f"  [info] frase_obrigatoria recomposta para EAN {ean}: "
            f"{data.get('frase_obrigatoria')!r} -> {frase_final!r}"
        )
    data["frase_obrigatoria"] = frase_final

    policy.apply_title_checks(data, ean)

    if is_medicamento:
        titulo = data.get("titulo") or ""
        n_concentracoes = len(CONCENTRACAO_RE.findall(titulo))
        if data.get("marca") and n_concentracoes >= 3:
            print(
                f"  [aviso] título de EAN {ean} pode estar listando composição "
                f"completa indevidamente (marca + {n_concentracoes} concentrações) "
                f"- revisar manualmente: {titulo!r}"
            )
        elif len(titulo) > TITULO_MAX_RECOMENDADO:
            print(
                f"  [aviso] título de EAN {ean} tem {len(titulo)} caracteres "
                f"(> {TITULO_MAX_RECOMENDADO}) - revisar se está buscável: "
                f"{titulo!r}"
            )

    marca_atual = data.get("marca")
    fabricante_atual = data.get("fabricante")
    if (
        marca_atual
        and fabricante_atual
        and marca_atual.strip().lower() == fabricante_atual.strip().lower()
    ):
        print(
            f"  [aviso] EAN {ean} tem fabricante igual à marca ({fabricante_atual!r}) "
            f"- provável fabricante não confirmado de verdade, revisar manualmente."
        )

    if (
        not data.get("pagina_produto_url")
        and any(data.get(c) for c in CAMPOS_DEPENDENTES_DE_FONTE)
        and not origem_confiavel_sem_url
    ):
        print(
            f"  [aviso] EAN {ean} sem pagina_produto_url mas com campos "
            f"dependentes de fonte preenchidos - revisar se os dados foram "
            f"confirmados de verdade ou inferidos sem base."
        )

    return data
