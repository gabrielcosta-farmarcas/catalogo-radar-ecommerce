from __future__ import annotations

import re

from dominios import (
    ORIGEM_ANVISA_CMED,
    eh_verdadeiro,
    origem_codigo,
)
from pipeline.prompts.medicamento import FORMAT_CAMPOS_SYSTEM
from pipeline.safety.frases import resolver_retencao
from pipeline.safety.titulo import corrigir_sal_titulo

CONCENTRACAO_RE = re.compile(r"\d+[.,]?\d*\s*(?:mg|mcg|g|ml|l|ui)\b", re.IGNORECASE)
TITULO_MAX_RECOMENDADO = 90
SUPLEMENTO_CONTRADICAO_RE = re.compile(
    r"suplemento alimentar|isento de registro|não é (?:um )?medicamento",
    re.IGNORECASE,
)


class MedicamentoPolicy:
    tipo = "medicamento"

    def format_system_template(self) -> str:
        return FORMAT_CAMPOS_SYSTEM

    def apply_invariants(self, data: dict, ean: str) -> None:
        import dominios

        ALLOWED_TARJA = set(dominios.TARJAS)
        tarja = data.get("tarja")
        if tarja is not None and tarja not in ALLOWED_TARJA:
            print(f"  [aviso] tarja inválida para EAN {ean} ({tarja!r}) - zerada.")
            data["tarja"] = tarja = None

        origem = origem_codigo(data)
        origem_e_cmed = origem == ORIGEM_ANVISA_CMED
        tarja_iqvia_mip_confirmada = eh_verdadeiro(data.get("tarja_confirmada_iqvia_mip"))

        if (
            not data.get("pagina_produto_url")
            and tarja is not None
            and not origem_e_cmed
            and not tarja_iqvia_mip_confirmada
        ):
            print(
                f"  [aviso] tarja zerada automaticamente para EAN {ean} - "
                f"medicamento sem pagina_produto_url (fonte não confirmada), "
                f"valor descartado: {tarja!r}"
            )
            data["tarja"] = tarja = None

        data["precisa_retencao_receita"] = resolver_retencao(
            tarja, data.get("principios_ativos")
        )

        if data.get("imagem_url"):
            print(
                f"  [info] imagem removida para EAN {ean} (medicamento): "
                f"{data['imagem_url']}"
            )
            data["imagem_url"] = None

    def apply_title_checks(self, data: dict, ean: str) -> None:
        corrigir_sal_titulo(data, ean)
        titulo = data.get("titulo") or ""
        principios = data.get("principios_ativos")
        if data.get("marca") and principios:
            itens = [p.strip() for p in principios.split(",") if p.strip()]
            if 1 <= len(itens) <= 2:
                nomes = []
                for item in itens:
                    match_nome = re.match(r"([^\d]+)", item)
                    if match_nome:
                        nomes.append(match_nome.group(1).strip())
                titulo_lower = titulo.lower()
                palavras_relevantes = [
                    palavra
                    for nome in nomes
                    for palavra in nome.split()
                    if len(palavra) > 3
                ]
                if palavras_relevantes and not any(
                    palavra.lower() in titulo_lower for palavra in palavras_relevantes
                ):
                    print(
                        f"  [aviso] título de EAN {ean} tem marca + 1-2 "
                        f"princípios ativos mas não cita nenhum deles - revisar: "
                        f"{titulo!r} (princípios: {principios!r})"
                    )

        texto_gerado = f"{titulo} {data.get('descricao_curta') or ''}"
        if SUPLEMENTO_CONTRADICAO_RE.search(texto_gerado):
            print(
                f"  [aviso] EAN {ean} classificado como Medicamento, mas o texto "
                f"gerado sugere suplemento/isento de registro - revisar "
                f"tipo_cadastro: {texto_gerado[:200]!r}"
            )
