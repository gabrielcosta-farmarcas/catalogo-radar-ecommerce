from pipeline.classify import eh_confiavel, indica_medicamento, registro_ms_valido


def test_placeholder_ms_nao_vale():
    assert registro_ms_valido("ISENTO") is None
    assert registro_ms_valido("N/A") is None
    assert registro_ms_valido("1.2345.6789") == "1.2345.6789"


def test_indica_medicamento_por_breadcrumb_nao_por_descricao():
    seringa = {
        "description": "Seringa para aplicação de medicamentos",
        "category": "Higiene > Curativos",
    }
    assert indica_medicamento(seringa) is False

    remedio = {"category": "Remédios > Dor e Febre"}
    assert indica_medicamento(remedio) is True

    com_ms = {"ms_register": "1234567"}
    assert indica_medicamento(com_ms) is True


def test_eh_confiavel_uma_fonte_sem_ms_nao_fecha():
    resultado = {
        "_fontes_ean_conferido": ["araujo"],
        "_nomes": ["Fralda Pampers XXG"],
    }
    assert eh_confiavel(resultado, ["araujo"]) is False


def test_eh_confiavel_duas_fontes_com_nomes():
    resultado = {
        "_fontes_ean_conferido": ["panvel", "pacheco"],
        "_nomes": ["Fralda Pampers Confort Sec XXG", "Pampers Confort Sec XXG"],
    }
    assert eh_confiavel(resultado, ["panvel", "pacheco"]) is True


def test_eh_confiavel_com_principio_ativo():
    resultado = {"active_ingredient": "Dipirona 1g", "_fontes_ean_conferido": []}
    assert eh_confiavel(resultado, ["sara"]) is True
