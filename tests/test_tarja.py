from dominios import TARJA_PRETA, TARJA_VERMELHA, TIPO_MEDICAMENTO
from pipeline.tarja import aplicar_resultado_verify_tarja


def _data(**kwargs):
    base = {
        "tipo_produto": TIPO_MEDICAMENTO,
        "titulo": "Dormire 15mg",
        "tarja": None,
        "registro_ms": None,
        "principios_ativos": "Maleato de Midazolam 15mg",
        "precisa_retencao_receita": None,
        "frase_obrigatoria": None,
    }
    base.update(kwargs)
    return base


def test_verify_confirmado_preenche_tarja_e_ms_se_vazio():
    data = aplicar_resultado_verify_tarja(
        _data(),
        {"confirmado": True, "tarja": TARJA_VERMELHA, "registro_ms": "1.2.3"},
        "789",
        atualizar_registro_ms="if_empty",
    )
    assert data["tarja"] == TARJA_VERMELHA
    assert data["registro_ms"] == "1.2.3"
    assert data["precisa_retencao_receita"] is True


def test_verify_nao_confirmado_zera_quando_pedido():
    data = aplicar_resultado_verify_tarja(
        _data(tarja=TARJA_PRETA, registro_ms="old"),
        {"confirmado": False},
        "789",
        atualizar_registro_ms="overwrite",
    )
    assert data["tarja"] is None
    assert data["registro_ms"] is None


def test_verify_none_nao_zera():
    original = _data(tarja=TARJA_PRETA, registro_ms="old")
    data = aplicar_resultado_verify_tarja(original, None, "789")
    assert data["tarja"] == TARJA_PRETA
    assert data["registro_ms"] == "old"


def test_abcfarma_nao_sobrescreve_ms():
    data = aplicar_resultado_verify_tarja(
        _data(registro_ms="abc-ms"),
        {"confirmado": True, "tarja": TARJA_VERMELHA, "registro_ms": "outro"},
        "789",
        atualizar_registro_ms=False,
    )
    assert data["registro_ms"] == "abc-ms"
    assert data["tarja"] == TARJA_VERMELHA
