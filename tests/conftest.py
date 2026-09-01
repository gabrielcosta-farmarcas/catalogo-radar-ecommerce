import pytest


@pytest.fixture(autouse=True)
def _sem_postgres(monkeypatch):
    """Travas e classify não precisam de Postgres nem de Anthropic."""

    def indice_vazio():
        return {
            "combinacoes": set(),
            "arvores_por_ramo": {},
            "ids_por_combinacao": {},
            "por_id": {},
        }

    monkeypatch.setattr("categorias.carregar_indice", indice_vazio)
    monkeypatch.setattr("categorias.resolver_id", lambda *a, **k: None)
    monkeypatch.setattr("categorias.por_id", lambda *a, **k: None)

    def aplicar_folha(data, categoria_id=None):
        if not categoria_id:
            data["categoria_id"] = None
            data["departamento"] = None
            data["categoria"] = None
            data["subcategoria"] = None
            return
        data["categoria_id"] = categoria_id

    monkeypatch.setattr("categorias.aplicar_folha", aplicar_folha)
    monkeypatch.setattr(
        "substancias_controladas.substancia_esta_controlada",
        lambda principios: "midazolam" in (principios or "").lower(),
    )
