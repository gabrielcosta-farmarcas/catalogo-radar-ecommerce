from pipeline.safety.frases import (
    FRASE_GENERICO,
    FRASE_LEITE,
    FRASE_MEDICAMENTO_GERAL,
    FRASE_SUPLEMENTO,
    FRASE_VENDA_PRESCRICAO,
    FRASE_VENDA_PRESCRICAO_RETENCAO,
    compor_frase_obrigatoria,
    resolver_retencao,
)
from pipeline.safety.human_review import marcar_validacao_humana
from pipeline.safety.titulo import corrigir_sal_titulo


def __getattr__(name):
    if name in ("apply_safety_checks", "validar_categorizacao"):
        from pipeline.safety.checks import apply_safety_checks, validar_categorizacao

        return {
            "apply_safety_checks": apply_safety_checks,
            "validar_categorizacao": validar_categorizacao,
        }[name]
    raise AttributeError(name)


__all__ = [
    "apply_safety_checks",
    "validar_categorizacao",
    "compor_frase_obrigatoria",
    "resolver_retencao",
    "marcar_validacao_humana",
    "corrigir_sal_titulo",
    "FRASE_GENERICO",
    "FRASE_LEITE",
    "FRASE_MEDICAMENTO_GERAL",
    "FRASE_SUPLEMENTO",
    "FRASE_VENDA_PRESCRICAO",
    "FRASE_VENDA_PRESCRICAO_RETENCAO",
]
