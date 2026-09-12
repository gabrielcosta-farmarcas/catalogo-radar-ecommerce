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
