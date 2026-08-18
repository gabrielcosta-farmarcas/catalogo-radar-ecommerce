import json

from ..base import SiteAdapter, html_to_text, ean_igual, mapear_tarja_texto
from ..models import ProductResult

PICK = "__pickRuntime=appsEtag%2Cblocks%2CblocksTree%2Ccomponents%2CcontentMap%2Cextensions%2Cmessages%2Cpage%2Cpages%2Cquery%2CqueryData%2Croute%2CruntimeMeta%2Csettings"


def _get_prop(props, name):
    for p in props:
        if p.get("name") == name:
            vals = p.get("values", [])
            return vals[0] if vals else None
    return None


def _mapear_generico(tipo_medicamento):
    if not tipo_medicamento:
        return None
    return "Sim" if "gener" in tipo_medicamento.strip().lower().replace("é", "e") else "Não"


class DrogalAdapter(SiteAdapter):
    name = "drogal"

    def search(self, ean):
        s = self._session()
        s.headers["Accept"] = "application/json"
        try:
            url = f"https://www.drogal.com.br/{ean}?_q={ean}&map=ft&{PICK}&__device=desktop"
            r = s.get(url, timeout=15)
            if r.status_code != 200:
                return None

            data = r.json()
            qd = data.get("queryData", [])
            if not qd:
                return None

            search_data = json.loads(qd[0].get("data", "{}"))
            products = search_data.get("productSearch", {}).get("products", [])
            if not products:
                return None

            p = products[0]
            items = p.get("items", [])
            if not items:
                return None

            item = items[0]
            if not ean_igual(item.get("ean"), ean):
                return None

            images = item.get("images", [])
            link_text = p.get("linkText", "")

            props = p.get("properties", [])

            indicacao = html_to_text(_get_prop(props, "Indicação"))
            contraind = html_to_text(_get_prop(props, "Contraindicação"))
            reacoes = html_to_text(_get_prop(props, "Reações Adversas"))
            como_usar = html_to_text(_get_prop(props, "Como Usar"))

            parts = []
            if indicacao:
                parts.append(f"Indicacao: {indicacao}")
            if contraind:
                parts.append(f"Contraindicacao: {contraind}")
            if como_usar:
                parts.append(f"Como Usar: {como_usar}")
            if reacoes:
                parts.append(f"Reacoes Adversas: {reacoes}")
            description = ", ".join(parts) if parts else None

            categories = p.get("categories", [])
            category = categories[0].strip("/") if categories else None

            return ProductResult(
                ean=ean,
                name=p.get("productName"),
                description=description,
                short_description=_get_prop(props, "Aviso Legal"),
                brand=p.get("brand"),
                manufacturer=p.get("brand"),
                category=category,
                active_ingredient=_get_prop(props, "Princípio Ativo"),
                generico=_mapear_generico(_get_prop(props, "Tipo de Medicamento")),
                tarja=mapear_tarja_texto(_get_prop(props, "Classificação")),
                prescricao_detalhe=_get_prop(props, "Prescrição Médica"),
                leaflet_url=_get_prop(props, "Bula"),
                url=f"https://www.drogal.com.br/{link_text}/p",
                image1=images[0].get("imageUrl") if len(images) > 0 else None,
                image2=images[1].get("imageUrl") if len(images) > 1 else None,
                image3=images[2].get("imageUrl") if len(images) > 2 else None,
                image4=images[3].get("imageUrl") if len(images) > 3 else None,
                ean_conferido=True,
            )
        except Exception:
            return None
