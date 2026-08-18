import json
import re

from ..base import SiteAdapter
from ..models import ProductResult

# sara.com.br serve os arquivos (imagem, bula em PDF) com caminho relativo -
# esse é o host que os hospeda (visto no <link rel="preconnect"> da própria
# página)
CDN_BASE = "https://cdn.sara.com.br/"


def _extrair_props_apresentacao(html):
    """
    sara.com.br é um site Next.js (App Router) - os dados do produto não
    vêm em JSON-LD nem em HTML estático, e sim embutidos em chunks de React
    Server Components (`self.__next_f.push([1, "<id>:<json>"])`). Acha o
    chunk que contém "currentPresentation" e devolve seus props já
    decodificados (chaves: currentPresentation, presentations, product).
    Retorna None se não achar (produto não é medicamento registrado, ou o
    formato da página mudou).
    """
    for match in re.finditer(r"self\.__next_f\.push\((\[.*?\])\)\s*</script>", html, re.DOTALL):
        try:
            item = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if len(item) != 2 or not isinstance(item[1], str):
            continue
        texto = item[1]
        if "currentPresentation" not in texto:
            continue
        _, _, resto = texto.partition(":")
        try:
            dados = json.loads(resto)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(dados, list) and len(dados) >= 4 and isinstance(dados[3], dict):
            return dados[3]
    return None


def _mapear_tarja(stripe):
    """stripe vem em texto livre (ex: "Tarja vermelha") - normaliza pro
    vocabulário fechado usado no resto do sistema."""
    if not stripe:
        return None
    texto = stripe.upper()
    if "PRETA" in texto:
        return "Tarja Preta"
    if "VERMELHA" in texto:
        return "Tarja Vermelha"
    if "SEM TARJA" in texto or "ISENTO" in texto:
        return "Sem Tarja"
    return None


def _mapear_generico(regulatory_class):
    """regulatoryClass usa o mesmo vocabulário da CMED (Novo/Similar/
    Genérico/Biológico/...) - só "Genérico" conta como genérico de verdade."""
    if not regulatory_class:
        return None
    return "Sim" if regulatory_class.strip().lower() == "genérico" else "Não"


def _limpar_texto(valor):
    if not valor:
        return None
    texto = str(valor).strip()
    return texto or None


class SaraAdapter(SiteAdapter):
    """
    sara.com.br não é uma farmácia (não vende produto, não tem preço) - é um
    portal de bulário: só tem página pra medicamento com registro na ANVISA,
    com dados regulatórios ricos (classe terapêutica, tarja, registro,
    princípio ativo, laboratório, bula em PDF). Por isso funciona diferente
    dos outros adapters - não tem passo de busca separado: a própria URL
    /produto/<ean> já redireciona (301) pra página certa quando o EAN existe,
    e devolve 404 quando não existe ou quando o produto não é medicamento
    registrado (cosmético, fralda etc. - fora do escopo do site).
    """

    name = "sara"

    def search(self, ean):
        s = self._session()
        try:
            r = s.get(f"https://www.sara.com.br/produto/{ean}", timeout=15)
            if r.status_code != 200:
                return None

            props = _extrair_props_apresentacao(r.text)
            if not props:
                return None

            apresentacao = props.get("currentPresentation") or {}
            produto = props.get("product") or {}

            # a mesma URL respondeu com um EAN "placeholder" (0000...) de
            # outro produto (KYMRIAH), então confere se o EAN da apresentação
            # bate mesmo com o que foi pedido antes de aceitar o resultado
            eans_da_pagina = {apresentacao.get(f"ean{n}") for n in (1, 2, 3)}
            if ean not in eans_da_pagina:
                return None

            leaflet = apresentacao.get("presentationLeaflet") or {}
            leaflet_path = leaflet.get("patientLeaflet") or leaflet.get("professionalLeaflet")
            image_path = apresentacao.get("imageUrl")

            nome_bruto = " ".join(
                parte
                for parte in (produto.get("name"), apresentacao.get("presentationFriendly"))
                if parte
            ).strip()

            return ProductResult(
                ean=ean,
                name=nome_bruto or produto.get("name"),
                brand=produto.get("name"),
                manufacturer=(produto.get("company") or {}).get("companyName"),
                category=apresentacao.get("therapeuticalClass"),
                active_ingredient=produto.get("activeIngredient"),
                dosage=apresentacao.get("drugUnit"),
                quantity=apresentacao.get("drugQuantity") or apresentacao.get("presentationFriendly"),
                ms_register=_limpar_texto(apresentacao.get("anvisaCode")),
                generico=_mapear_generico(produto.get("regulatoryClass")),
                tarja=_mapear_tarja(apresentacao.get("stripe")),
                leaflet_url=f"{CDN_BASE}{leaflet_path}" if leaflet_path else None,
                url=r.url,
                image1=f"{CDN_BASE}{image_path}" if image_path else None,
                ean_conferido=True,
            )
        except Exception:
            return None
