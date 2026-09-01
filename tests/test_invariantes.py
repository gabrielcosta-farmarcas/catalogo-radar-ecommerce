from dominios import (
    ORIGEM_ANVISA_CMED,
    ORIGEM_CLAUDE,
    ORIGEM_IQVIA,
    TARJA_NAO_APLICAVEL,
    TARJA_PRETA,
    TARJA_SEM,
    TARJA_VERMELHA,
    TIPO_MEDICAMENTO,
    TIPO_NAO_MEDICAMENTO,
)
from pipeline.safety.checks import apply_safety_checks
from pipeline.safety.frases import (
    FRASE_LEITE,
    FRASE_SUPLEMENTO,
    resolver_retencao,
)
from pipeline.safety.human_review import (
    MENSAGEM_VALIDACAO_CMED_TARJA,
    marcar_validacao_humana,
)
from pipeline.safety.titulo import corrigir_sal_titulo


def _med(**kwargs):
    data = {
        "tipo_produto": TIPO_MEDICAMENTO,
        "titulo": "Novalgina 1g Dipirona 20 Comprimidos",
        "marca": "Novalgina",
        "origem_enriquecimento": ORIGEM_ANVISA_CMED,
        "pagina_produto_url": None,
        "tarja": TARJA_SEM,
        "principios_ativos": "Dipirona 1g",
        "imagem_url": "https://exemplo.com/foto.jpg",
    }
    data.update(kwargs)
    return data


def _nmed(**kwargs):
    data = {
        "tipo_produto": TIPO_NAO_MEDICAMENTO,
        "titulo": "Pomada Creme Assaduras Hipoglós 40g",
        "marca": "Hipoglós",
        "origem_enriquecimento": ORIGEM_CLAUDE,
        "tarja": TARJA_PRETA,
        "registro_ms": "123456",
        "generico": True,
        "imagem_url": None,
    }
    data.update(kwargs)
    return data


def test_midazolam_corrige_sal_no_titulo():
    data = _med(
        titulo="Dormire Cloridrato de Midazolam 15mg 30 Comprimidos",
        marca="Dormire",
        principios_ativos="Maleato de Midazolam 15mg",
        tarja=TARJA_VERMELHA,
    )
    corrigir_sal_titulo(data, "789")
    assert "Maleato" in data["titulo"]
    assert "Cloridrato" not in data["titulo"]


def test_medicamento_remove_imagem():
    data = apply_safety_checks(_med(), "789")
    assert data["imagem_url"] is None


def test_nao_medicamento_carimba_tarja_e_tira_ms():
    data = apply_safety_checks(_nmed(), "789")
    assert data["tarja"] == TARJA_NAO_APLICAVEL
    assert data["precisa_retencao_receita"] is False
    assert data["registro_ms"] is None
    assert data["generico"] is None


def test_antiacido_tarja_preta_sem_fonte_e_zerada():
    data = apply_safety_checks(
        _med(
            origem_enriquecimento=ORIGEM_CLAUDE,
            pagina_produto_url=None,
            tarja=TARJA_PRETA,
            titulo="Estomazil Antiácido 5g",
        ),
        "789",
    )
    assert data["tarja"] is None
    assert data["precisa_retencao_receita"] is None


def test_cmed_mantem_tarja_sem_url():
    data = apply_safety_checks(
        _med(tarja=TARJA_VERMELHA, pagina_produto_url=None),
        "789",
    )
    assert data["tarja"] == TARJA_VERMELHA


def test_iqvia_mip_mantem_sem_tarja_sem_url():
    data = apply_safety_checks(
        _med(
            origem_enriquecimento=ORIGEM_IQVIA,
            tarja=TARJA_SEM,
            pagina_produto_url=None,
            tarja_confirmada_iqvia_mip=True,
        ),
        "789",
    )
    assert data["tarja"] == TARJA_SEM
    assert data["precisa_retencao_receita"] is False


def test_tarja_preta_exige_retencao():
    assert resolver_retencao(TARJA_PRETA, None) is True
    assert resolver_retencao(TARJA_SEM, None) is False
    assert resolver_retencao(None, "Midazolam") is None
    assert resolver_retencao(TARJA_VERMELHA, "Maleato de Midazolam 15mg") is True
    assert resolver_retencao(TARJA_VERMELHA, "Dipirona 1g") is False


def test_cmed_sem_tarja_entra_fila_humana():
    data = marcar_validacao_humana(
        _med(tarja=None, origem_enriquecimento=ORIGEM_ANVISA_CMED)
    )
    assert data["precisa_validacao_humana"] is True
    assert data["mensagem_validacao_humana"] == MENSAGEM_VALIDACAO_CMED_TARJA


def test_nao_medicamento_nao_entra_fila():
    data = marcar_validacao_humana(
        apply_safety_checks(_nmed(origem_enriquecimento=ORIGEM_CLAUDE), "789")
    )
    assert data["precisa_validacao_humana"] is False


def test_abcfarma_outros_suplemento_entra_fila():
    data = marcar_validacao_humana(
        _med(
            origem_enriquecimento="abcfarma",
            tarja=TARJA_SEM,
            tarja_confirmada_bulario=True,
            _suspeita_suplemento_abcfarma=True,
        )
    )
    assert data["precisa_validacao_humana"] is True
    assert "OUTROS" in data["mensagem_validacao_humana"]


def test_rdc_240_nao_aplica_em_enzimas_probioticos():
    data = apply_safety_checks(
        _nmed(
            titulo="Probiótico Lactobacillus 30 Cápsulas",
            departamento="Suplementos Alimentares",
            categoria="Sistema Digestivo",
            subcategoria="Probióticos",
            registro_ms=None,
            generico=None,
        ),
        "789",
    )
    assert FRASE_SUPLEMENTO not in (data.get("frase_obrigatoria") or "")


def test_formula_infantil_anexa_frase_leite():
    data = apply_safety_checks(
        _nmed(
            titulo="Fórmula Infantil Aptamil 2 400g",
            registro_ms=None,
            generico=None,
        ),
        "789",
    )
    assert FRASE_LEITE in data["frase_obrigatoria"]
