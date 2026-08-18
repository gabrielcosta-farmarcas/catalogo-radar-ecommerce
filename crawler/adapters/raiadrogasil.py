import json
import re

from ..base import SiteAdapter, html_to_text, ean_igual
from ..models import ProductResult

def _extract_next_data(html):
    match = re.search(r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    return json.loads(match.group(1)) if match else None


def _mapear_tarja(descricaotarja):
    """
    Drogasil/Droga Raia nao tem um campo "Tarja Vermelha/Preta" literal - so
    um texto livre em 'descricaotarja' (ex: "TARJADO EM VERMELHO COM
    RETENCAO DA RECEITA"). Mapeia pro nosso vocabulario fechado; None se nao
    conseguir reconhecer o padrao (mais seguro que arriscar errado).
    """
    if not descricaotarja:
        return None
    texto = descricaotarja.upper()
    if "PRETO" in texto or "PRETA" in texto:
        return "Tarja Preta"
    if "VERMELH" in texto:
        return "Tarja Vermelha"
    if "SEM TARJA" in texto or "ISENTO" in texto or "LIVRE" in texto:
        return "Sem Tarja"
    return None


class RaiaDrogasilBase(SiteAdapter):
    base_url = ""

    def search(self, ean):
        s = self._session_with_tokens()
        try:
            r = s.get(f"{self.base_url}/search?w={ean}", timeout=15)
            if r.status_code != 200:
                return None

            data = _extract_next_data(r.text)
            if not data:
                return None

            pp = data.get("props", {}).get("pageProps", {})
            pp2 = pp.get("pageProps", {})
            products = (pp2 or pp).get("results", {}).get("products", [])
            if not products:
                return None

            product_url = products[0].get("url")
            if not product_url:
                return None

            full_url = f"{self.base_url}{product_url}"
            r2 = s.get(full_url, timeout=15)
            if r2.status_code != 200:
                return None

            detail = _extract_next_data(r2.text)
            if not detail:
                return None

            pd = detail.get("props", {}).get("pageProps", {}).get("productData", {})
            if not pd:
                return None

            attrs = {a["attribute_code"]: a for a in pd.get("custom_attributes", [])}

            def get_attr(code):
                a = attrs.get(code)
                if not a:
                    return None
                vs = a.get("value_string", [])
                return vs[0] if vs else None

            def get_attr_label(code):
                a = attrs.get(code)
                if not a:
                    return None
                vals = a.get("value", [])
                if vals and isinstance(vals[0], dict):
                    return vals[0].get("label")
                vs = a.get("value_string", [])
                return vs[0] if vs else None

            found_ean = get_attr("ean")
            if not found_ean or not ean_igual(found_ean, ean):
                return None

            media = pd.get("mediaGallery", [])

            # "descricaotarja" vem preenchido pro catalogo inteiro, nao so
            # medicamento - visto "PRODUTO SEM TARJA" em shampoo, enxaguante
            # bucal e formula infantil, o que fazia esses produtos serem
            # classificados como Medicamento (ver _indica_medicamento em
            # enrich_com_crawler.py, que trata tarja/prescricao_detalhe
            # preenchidos como prova de medicamento). "grupo"/"codgrupo" e o
            # proprio departamento do catalogo da Drogasil - so vale "1"
            # (MEDICAMENTO) em remedio de verdade (confirmado comparando com
            # EANs da CMED), nunca em perfumaria/beleza/alimento infantil -
            # so extrai tarja/prescricao_detalhe quando o proprio catalogo
            # confirma que é medicamento.
            eh_medicamento_no_catalogo = get_attr("codgrupo") == "1"

            return ProductResult(
                ean=ean,
                name=pd.get("name", ""),
                description=html_to_text(get_attr("description")),
                short_description=html_to_text(get_attr("meta_description")),
                brand=get_attr_label("marca"),
                manufacturer=get_attr_label("fabricante"),
                active_ingredient=get_attr("principioativonovo"),
                dosage=get_attr("dosagem"),
                quantity=get_attr("quantidade"),
                ms_register=get_attr("ms"),
                tarja=_mapear_tarja(get_attr("descricaotarja")) if eh_medicamento_no_catalogo else None,
                prescricao_detalhe=get_attr("descricaotarja") if eh_medicamento_no_catalogo else None,
                leaflet_url=get_attr("linkbula"),
                url=full_url,
                image1=media[0]["file"] if len(media) > 0 else None,
                image2=media[1]["file"] if len(media) > 1 else None,
                image3=media[2]["file"] if len(media) > 2 else None,
                image4=media[3]["file"] if len(media) > 3 else None,
                ean_conferido=True,
            )
        except Exception:
            return None


class DrogasilAdapter(RaiaDrogasilBase):
    name = "drogasil"
    base_url = "https://www.drogasil.com.br"


class DrogaRaiaAdapter(RaiaDrogasilBase):
    name = "drogaraia"
    base_url = "https://www.drogaraia.com.br"
