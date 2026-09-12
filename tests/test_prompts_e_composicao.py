from pipeline.composition import parsear_composicao_cmed
from pipeline.prompts.medicamento import (
    FORMAT_CAMPOS_SEM_CATEGORIA as PACK_MED_COPY,
    FORMAT_CAMPOS_SYSTEM as PACK_MED,
)
from pipeline.prompts.nao_medicamento import (
    FORMAT_CAMPOS_SEM_CATEGORIA as PACK_NMED_COPY,
    FORMAT_CAMPOS_SYSTEM as PACK_NMED,
)
from pipeline.policies.base import policy_for
from dominios import TIPO_MEDICAMENTO, TIPO_NAO_MEDICAMENTO


def test_pack_med_tem_sal_sem_few_shot_nmed():
    assert "Maleato de Midazolam" in PACK_MED
    assert "Hipoglós" not in PACK_MED
    assert "Fralda Pampers" not in PACK_MED
    assert "Genérico" in PACK_MED


def test_pack_nmed_tem_objeto_primeiro_sem_regra_de_sal():
    assert "Hipoglós" in PACK_NMED
    assert "Johnson's Baby" in PACK_NMED
    assert "Maleato de Midazolam" not in PACK_NMED
    assert "COM REV" not in PACK_NMED
    assert "NUNCA comece pela marca" in PACK_NMED


def test_policy_escolhe_pack_pelo_tipo():
    med = policy_for(TIPO_MEDICAMENTO).format_system_template()
    nmed = policy_for(TIPO_NAO_MEDICAMENTO).format_system_template()
    assert med is PACK_MED
    assert nmed is PACK_NMED


def test_pack_sem_categoria_mantem_regras_de_copy_sem_arvore():
    med = policy_for(TIPO_MEDICAMENTO).format_system_template_sem_categorizacao()
    nmed = policy_for(TIPO_NAO_MEDICAMENTO).format_system_template_sem_categorizacao()
    assert med is PACK_MED_COPY
    assert nmed is PACK_NMED_COPY
    assert "{ARVORE_RAMO}" not in med
    assert "{ARVORE_RAMO}" not in nmed
    assert "ÁRVORE DE CATEGORIZAÇÃO OFICIAL" not in med
    assert "ÁRVORE DE CATEGORIZAÇÃO OFICIAL" not in nmed
    assert "Maleato de Midazolam" in med
    assert "Hipoglós" in nmed
    assert '{"titulo": str|null, "descricao_curta": str|null}' in med
    saida_med = med.split("Responda APENAS")[-1]
    assert '"departamento":' not in saida_med
    assert '"categoria":' not in saida_med
    assert '"subcategoria":' not in saida_med
    assert "{ARVORE_RAMO}" in PACK_MED
    assert "{ARVORE_RAMO}" in PACK_NMED


def test_parsear_composicao_cmed_pareia_n_para_n():
    texto = parsear_composicao_cmed(
        "MALEATO DE MIDAZOLAM",
        "15 MG COM REV CT BL AL PLAS OPC X 30",
    )
    assert texto == "Maleato de Midazolam 15mg"


def test_parsear_composicao_ambigua_devolve_none():
    assert parsear_composicao_cmed(
        "HIDROXIDO DE ALUMINIO; HIDROXIDO DE MAGNESIO; CARBONATO DE CALCIO",
        "185 MG + 235 MG PO EFERV CT ENV AL X 6",
    ) is None


def test_formatar_sem_arvore_quando_categoria_ja_mapeada(monkeypatch):
    import enrich_produtos as ep

    capturado = {}

    def fake_chamar(client, model, system, mensagem, max_tokens=400):
        capturado["system"] = system
        capturado["mensagem"] = mensagem
        return (
            {
                "titulo": "Novalgina 1g Dipirona 20 Comprimidos",
                "descricao_curta": None,
                "departamento": "ignorar",
                "categoria": "ignorar",
                "subcategoria": "ignorar",
            },
            {"tokens": 1, "cache_creation": 0, "cache_read": 0},
        )

    monkeypatch.setattr(ep, "_chamar_formatacao_campos", fake_chamar)
    data, _usage = ep.formatar_campos_confirmados(
        None,
        "modelo",
        TIPO_MEDICAMENTO,
        "Novalgina",
        "Dipirona 1g",
        "20 COM",
        "NOVALGINA",
        categoria_bruta="M2A - ANALGESICOS",
        categoria_ja_mapeada=True,
    )
    system_texto = capturado["system"][0]["text"]
    mensagem = capturado["mensagem"]
    assert "ÁRVORE DE CATEGORIZAÇÃO OFICIAL" not in system_texto
    assert "categoria bruta do site" not in mensagem
    assert "Gere titulo e descricao_curta." in mensagem
    assert "departamento/categoria/subcategoria" not in mensagem
    assert data["titulo"] == "Novalgina 1g Dipirona 20 Comprimidos"
    assert data["departamento"] is None
    assert data["categoria"] is None
    assert data["subcategoria"] is None


def test_formatar_pede_categoria_quando_nao_mapeada(monkeypatch):
    import enrich_produtos as ep

    capturado = {}

    def fake_chamar(client, model, system, mensagem, max_tokens=400):
        capturado["mensagem"] = mensagem
        return (
            {
                "titulo": "Novalgina 1g Dipirona 20 Comprimidos",
                "descricao_curta": None,
                "departamento": "Dor e Febre",
                "categoria": "Analgésicos",
                "subcategoria": "Dipirona",
            },
            {"tokens": 1, "cache_creation": 0, "cache_read": 0},
        )

    monkeypatch.setattr(ep, "_chamar_formatacao_campos", fake_chamar)
    data, _usage = ep.formatar_campos_confirmados(
        None,
        "modelo",
        TIPO_MEDICAMENTO,
        "Novalgina",
        "Dipirona 1g",
        "20 COM",
        "NOVALGINA",
        categoria_bruta="M2A - ANALGESICOS",
    )
    assert "categoria bruta do site" in capturado["mensagem"]
    assert "Gere titulo, descricao_curta e departamento/categoria/subcategoria." in capturado["mensagem"]
    assert data["departamento"] == "Dor e Febre"
