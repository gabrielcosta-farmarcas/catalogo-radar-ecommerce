from pipeline.composition import parsear_composicao_cmed
from pipeline.prompts.medicamento import FORMAT_CAMPOS_SYSTEM as PACK_MED
from pipeline.prompts.nao_medicamento import FORMAT_CAMPOS_SYSTEM as PACK_NMED
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
