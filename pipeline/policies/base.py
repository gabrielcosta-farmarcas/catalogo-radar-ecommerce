from __future__ import annotations

from typing import Protocol

from dominios import TIPO_MEDICAMENTO, TIPO_NAO_MEDICAMENTO, eh_medicamento


class ProductPolicy(Protocol):
    tipo: str

    def apply_invariants(self, data: dict, ean: str) -> None:
        ...

    def apply_title_checks(self, data: dict, ean: str) -> None:
        ...

    def format_system_template(self) -> str:
        ...

    def format_system_template_sem_categorizacao(self) -> str:
        ...


def validar_imagem_minima(data: dict, ean: str) -> None:
    """
    Baixa e valida a dimensão mínima de imagem_url, comum a medicamento e
    não-medicamento desde que medicamento passou a levar imagem também
    (só muda o diretório de destino, ver salvar_imagem_local).
    """
    if not data.get("imagem_url"):
        return
    from enrich_produtos import check_imagem_tamanho_minimo

    ok, motivo, conteudo = check_imagem_tamanho_minimo(data["imagem_url"])
    if not ok:
        print(f"  [aviso] imagem descartada para EAN {ean} ({motivo}): {data['imagem_url']}")
        data["imagem_url"] = None
        data.pop("_imagem_bytes", None)
    elif conteudo:
        data["_imagem_bytes"] = conteudo


def policy_for(data_ou_tipo) -> ProductPolicy:
    from pipeline.policies.medicamento import MedicamentoPolicy
    from pipeline.policies.nao_medicamento import NaoMedicamentoPolicy

    if isinstance(data_ou_tipo, dict):
        if eh_medicamento(data_ou_tipo):
            return MedicamentoPolicy()
        return NaoMedicamentoPolicy()
    if data_ou_tipo == TIPO_MEDICAMENTO:
        return MedicamentoPolicy()
    if data_ou_tipo == TIPO_NAO_MEDICAMENTO:
        return NaoMedicamentoPolicy()
    return NaoMedicamentoPolicy()
