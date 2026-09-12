"""
Claude não faz mais busca própria na internet no pipeline automático - só
formata o que CMED/ABCFarma/Tarjados/IQVIA/crawler já localizaram (ver
pipeline/orchestrator.py). Sem nenhuma fonte confirmando o EAN, vira "não
localizado" sem gastar token nenhum - nunca cai pra ep.call_model.
"""
from types import SimpleNamespace

import pipeline.orchestrator as orchestrator


class _Args(SimpleNamespace):
    model = "modelo-teste"
    sem_verificar_tarja = False
    verify_images = False
    sleep = 0.0


def test_sem_fonte_nenhuma_vira_nao_localizado_sem_chamar_claude(monkeypatch):
    monkeypatch.setattr(orchestrator, "fontes_oficiais", lambda: [])
    monkeypatch.setattr(
        "enrich_com_crawler.buscar_no_crawler", lambda ean, **kw: ({}, [])
    )
    monkeypatch.setattr("pipeline.classify.eh_confiavel", lambda resultado, fontes: False)

    def _explode(*args, **kwargs):
        raise AssertionError("call_model não deveria ser chamado - Claude não busca na internet")

    monkeypatch.setattr("enrich_produtos.call_model", _explode)

    ean, nome_produto, data, usage = orchestrator.run(
        "7891234567890", "produto qualquer", _Args(), client=None
    )

    assert ean == "7891234567890"
    assert data is None
    assert usage == {"tokens": 0, "cache_creation": 0, "cache_read": 0}


def test_fonte_oficial_confirma_sem_passar_por_crawler_ou_claude(monkeypatch):
    fonte_fake = SimpleNamespace(
        name="cmed",
        buscar=lambda ean: {"achou": True},
        mapear=lambda item, ean, client, model, args: (
            {"titulo": "Produto CMED", "origem_enriquecimento": "anvisa_cmed"},
            {"tokens": 42, "cache_creation": 0, "cache_read": 0},
        ),
    )
    monkeypatch.setattr(orchestrator, "fontes_oficiais", lambda: [fonte_fake])

    def _explode_crawler(*args, **kwargs):
        raise AssertionError("não deveria consultar o crawler quando a CMED já achou")

    monkeypatch.setattr("enrich_com_crawler.buscar_no_crawler", _explode_crawler)

    ean, nome_produto, data, usage = orchestrator.run(
        "7891234567890", "produto qualquer", _Args(), client=None
    )

    assert data == {"titulo": "Produto CMED", "origem_enriquecimento": "anvisa_cmed"}
    assert usage["tokens"] == 42
