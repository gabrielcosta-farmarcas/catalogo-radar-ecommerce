from ..base import SiteAdapter, html_to_text, ean_igual, mapear_tarja_texto
from ..models import ProductResult


def _first(lst):
    return lst[0] if lst else None


def _mapear_generico(tipo_medicamento):
    if not tipo_medicamento:
        return None
    return "Sim" if "gener" in tipo_medicamento.strip().lower().replace("é", "e") else "Não"


class DrogariaSPAdapter(SiteAdapter):
    name = "drogaria_sp"

    def search(self, ean):
        s = self._session()
        s.headers.update({
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "x-requested-with": "XMLHttpRequest",
            "referer": "https://www.drogariasaopaulo.com.br/",
        })
        try:
            r = s.get(
                f"https://www.drogariasaopaulo.com.br/api/catalog_system/pub/products/search?fq=alternateIds_Ean%3A{ean}",
                timeout=15,
            )
            if r.status_code != 200:
                return None

            data = r.json()
            if not data:
                return None

            p = data[0]
            items = p.get("items", [])
            if not items:
                return None

            item = items[0]
            if not ean_igual(item.get("ean"), ean):
                return None

            images = item.get("images", [])
            categories = p.get("categories", [])

            return ProductResult(
                ean=ean,
                name=p.get("productName"),
                description=html_to_text(p.get("description")),
                short_description=p.get("metaTagDescription"),
                brand=p.get("brand"),
                category=categories[0].strip("/") if categories else None,
                active_ingredient=_first(p.get("Princípio Ativo", [])),
                ms_register=_first(p.get("Registro MS", [])),
                generico=_mapear_generico(_first(p.get("Tipo de Medicamento", []))),
                tarja=mapear_tarja_texto(_first(p.get("Classificação", []))),
                prescricao_detalhe=_first(p.get("Prescrição Médica", [])),
                url=p.get("link"),
                image1=images[0].get("imageUrl") if len(images) > 0 else None,
                image2=images[1].get("imageUrl") if len(images) > 1 else None,
                image3=images[2].get("imageUrl") if len(images) > 2 else None,
                image4=images[3].get("imageUrl") if len(images) > 3 else None,
                ean_conferido=True,
            )
        except Exception:
            return None
