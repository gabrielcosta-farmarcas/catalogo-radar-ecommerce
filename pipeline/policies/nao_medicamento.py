from __future__ import annotations

from dominios import TARJA_NAO_APLICAVEL
from pipeline.prompts.nao_medicamento import FORMAT_CAMPOS_SEM_CATEGORIA, FORMAT_CAMPOS_SYSTEM


class NaoMedicamentoPolicy:
    tipo = "nao_medicamento"

    def format_system_template(self) -> str:
        return FORMAT_CAMPOS_SYSTEM

    def format_system_template_sem_categorizacao(self) -> str:
        return FORMAT_CAMPOS_SEM_CATEGORIA

    def apply_invariants(self, data: dict, ean: str) -> None:
        data["tarja"] = TARJA_NAO_APLICAVEL
        data["precisa_retencao_receita"] = False

        if data.get("registro_ms"):
            print(
                f"  [info] registro_ms removido para EAN {ean} (produto não "
                f"é medicamento): {data['registro_ms']}"
            )
            data["registro_ms"] = None
        if data.get("generico"):
            data["generico"] = None

        if data.get("imagem_url"):
            from enrich_produtos import check_imagem_tamanho_minimo

            ok, motivo, conteudo = check_imagem_tamanho_minimo(data["imagem_url"])
            if not ok:
                print(
                    f"  [aviso] imagem descartada para EAN {ean} ({motivo}): "
                    f"{data['imagem_url']}"
                )
                data["imagem_url"] = None
                data.pop("_imagem_bytes", None)
            elif conteudo:
                data["_imagem_bytes"] = conteudo

    def apply_title_checks(self, data: dict, ean: str) -> None:
        titulo = data.get("titulo") or ""
        marca_atual = data.get("marca")
        if marca_atual and titulo:
            if titulo.strip().lower().startswith(marca_atual.strip().lower()):
                print(
                    f"  [aviso] título de EAN {ean} começa pela marca ({marca_atual!r}), "
                    f"mas o padrão de não-medicamento manda categoria do produto "
                    f"primeiro - revisar manualmente: {titulo!r}"
                )
