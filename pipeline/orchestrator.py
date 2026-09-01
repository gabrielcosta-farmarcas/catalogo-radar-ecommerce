"""Cadeia de fontes oficiais + crawler + Claude."""

from __future__ import annotations

import time
from typing import Callable, Protocol

from dominios import ORIGEM_CLAUDE


class CatalogSource(Protocol):
    name: str

    def buscar(self, ean: str):
        ...

    def mapear(self, item, ean, client, model, args) -> tuple:
        ...


def _mapear_cmed(item, ean, client, model, args):
    import enrich_com_crawler as fluxo

    return fluxo.mapear_cmed_para_schema(item, ean, client, model)


def _mapear_abcfarma(item, ean, client, model, args):
    import enrich_com_crawler as fluxo

    return fluxo.mapear_abcfarma_para_schema(
        item, ean, client, model, verify_tarja=not args.sem_verificar_tarja
    )


def _mapear_tarjados(item, ean, client, model, args):
    import enrich_com_crawler as fluxo

    return fluxo.mapear_tarjados_para_schema(
        item, ean, client, model, verify_tarja=not args.sem_verificar_tarja
    )


def _mapear_iqvia(item, ean, client, model, args):
    import enrich_com_crawler as fluxo

    return fluxo.mapear_iqvia_para_schema(
        item, ean, client, model, verify_tarja=not args.sem_verificar_tarja
    )


class _Fonte:
    def __init__(self, name: str, buscar: Callable, mapear: Callable):
        self.name = name
        self._buscar = buscar
        self._mapear = mapear

    def buscar(self, ean):
        return self._buscar(ean)

    def mapear(self, item, ean, client, model, args):
        return self._mapear(item, ean, client, model, args)


def fontes_oficiais() -> list[_Fonte]:
    import abcfarma
    import cmed
    import iqvia
    import tarjados

    return [
        _Fonte("cmed", cmed.buscar_medicamento_anvisa, _mapear_cmed),
        _Fonte("abcfarma", abcfarma.buscar_medicamento_abcfarma, _mapear_abcfarma),
        _Fonte("tarjados", tarjados.buscar_produto_tarjado, _mapear_tarjados),
        _Fonte("iqvia", iqvia.buscar_produto_iqvia, _mapear_iqvia),
    ]


def run(ean, nome_produto, args, client):
    """
    Mesmo fluxo de enrich_com_crawler.worker: primeira fonte que achar o EAN
    formata e devolve. Crawler se confiável; senão Claude.
    """
    import enrich_com_crawler as fluxo
    import enrich_produtos as ep
    from pipeline.classify import eh_confiavel

    usage_total = {"tokens": 0, "cache_creation": 0, "cache_read": 0}

    def avisar(etapa, mensagem):
        callback = getattr(args, "on_progress", None)
        if callback:
            callback(etapa, mensagem)

    rotulo_fonte = {
        "abcfarma": "ABCFarma",
        "tarjados": "base de Tarjados",
        "iqvia": "IQVIA",
    }

    avisar("cmed", "Consultando CMED/ANVISA")
    for fonte in fontes_oficiais():
        if fonte.name != "cmed":
            avisar(fonte.name, f"Consultando {rotulo_fonte.get(fonte.name, fonte.name)}")
        item = fonte.buscar(ean)
        if item is not None:
            avisar("formatacao", f"Formatando dados de {fonte.name}")
            data, usage = fonte.mapear(item, ean, client, args.model, args)
            return ean, nome_produto, data, usage

    avisar("crawler", "Buscando em farmácias")
    resultado, fontes = fluxo.buscar_no_crawler(ean)

    if eh_confiavel(resultado, fontes):
        avisar("formatacao", "Formatando dados do crawler")
        data, usage = fluxo.mapear_para_schema(resultado, fontes, client, args.model)
        return ean, nome_produto, data, usage

    avisar("claude", "Buscando com Claude (pode demorar)")
    time.sleep(args.sleep)
    data, usage_claude = ep.call_model(
        client,
        args.model,
        ean,
        nome_produto,
        verify_images=args.verify_images,
        verify_tarja=not args.sem_verificar_tarja,
        pistas_nao_confirmadas=fluxo.montar_pistas_nao_confirmadas(resultado, fontes),
    )
    for chave in usage_total:
        usage_total[chave] += usage_claude[chave]
    if data is not None:
        data["origem_enriquecimento"] = ORIGEM_CLAUDE
        data["origem_referencia"] = None
        data["confirmado_anvisa_cmed"] = False
        data = ep.marcar_validacao_humana(data)
    return ean, nome_produto, data, usage_total
