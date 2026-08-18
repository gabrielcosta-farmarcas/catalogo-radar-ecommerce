import json

from ..base import SiteAdapter, html_to_text, ean_igual
from ..models import ProductResult

PICK = "__pickRuntime=appsEtag%2Cblocks%2CblocksTree%2Ccomponents%2CcontentMap%2Cextensions%2Cmessages%2Cpage%2Cpages%2Cquery%2CqueryData%2Croute%2CruntimeMeta%2Csettings"


def _get_spec(specs, name):
    for s in specs:
        if s.get("name") == name:
            vals = s.get("values", [])
            return vals[0] if vals else None
    return None


def _mapear_generico(genericos_flag, tipo_medicamento):
    """
    Venancio expoe um flag binario "Genericos" ('1'/'0'), mais confiavel que
    tentar ler o texto de "Tipo Medicamento" (usado só como fallback).
    """
    if genericos_flag is not None:
        return "Sim" if str(genericos_flag).strip() == "1" else "Não"
    if tipo_medicamento:
        return "Sim" if "gener" in tipo_medicamento.strip().lower().replace("é", "e") else "Não"
    return None


def _mapear_tarja(venda_controlada, tipo_receita):
    """
    O Venancio nao expoe "Tarja Vermelha/Preta" como string direta (diferente
    de Drogaria SP/Pacheco/Drogal) - so campos booleanos tipo "Venda
    Controlada" e um texto livre em "Tipo Receita". Mapeia pro nosso
    vocabulario fechado; None se nao der pra inferir com confianca (mais
    seguro deixar em branco do que arriscar Tarja Vermelha por precaução).
    """
    if venda_controlada is None:
        return None
    if str(venda_controlada).strip().upper() != "SIM":
        return "Sem Tarja"
    if tipo_receita and "amarela" in str(tipo_receita).lower():
        return "Tarja Preta"
    return None


class VenancioAdapter(SiteAdapter):
    name = "venancio"

    def search(self, ean):
        s = self._session()
        s.headers["Accept"] = "application/json"
        try:
            url = f"https://www.drogariavenancio.com.br/{ean}?_q={ean}&map=ft&{PICK}&__device=desktop"
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

            # Extract specs from specificationGroups
            specs = []
            for group in p.get("specificationGroups", []):
                if group.get("name") != "allSpecifications":
                    specs.extend(group.get("specifications", []))

            categories = p.get("categories", [])
            category = categories[0].strip("/") if categories else None

            # "Venda Controlada"/"Retenção Receita"/"Tipo Receita" vem
            # preenchido pro catalogo inteiro, nao so medicamento - visto
            # "Venda Controlada: NAO" em shampoo, enxaguante bucal e formula
            # infantil, o que fazia _mapear_tarja devolver "Sem Tarja" (e
            # _indica_medicamento em enrich_com_crawler.py tratar isso como
            # prova de medicamento). O topo do breadcrumb de categoria
            # ("/Medicamentos/...") e o proprio catalogo da Venancio dizendo
            # em que departamento o produto esta - confirmado comparando com
            # EANs da CMED - so extrai tarja/prescricao_detalhe quando bate.
            eh_medicamento_no_catalogo = any(
                c.strip("/").split("/")[0] == "Medicamentos" for c in categories
            )

            indicacao = html_to_text(_get_spec(specs, "Indicações"))
            contraind = html_to_text(_get_spec(specs, "Contra indicações"))
            partes = []
            if indicacao:
                partes.append(f"Indicacoes: {indicacao}")
            if contraind:
                partes.append(f"Contra-Indicacoes: {contraind}")
            description = ", ".join(partes) if partes else None

            return ProductResult(
                ean=ean,
                name=p.get("productName"),
                description=description,
                brand=p.get("brand"),
                category=category,
                active_ingredient=_get_spec(specs, "Principio Ativo"),
                ms_register=_get_spec(specs, "Registro Anvisa"),
                generico=_mapear_generico(
                    _get_spec(specs, "Genericos"),
                    _get_spec(specs, "Tipo Medicamento"),
                ),
                tarja=_mapear_tarja(
                    _get_spec(specs, "Venda Controlada"),
                    _get_spec(specs, "Tipo Receita"),
                ) if eh_medicamento_no_catalogo else None,
                prescricao_detalhe=(", ".join(
                    filter(None, [
                        f"Venda Controlada: {_get_spec(specs, 'Venda Controlada')}" if _get_spec(specs, "Venda Controlada") else None,
                        f"Retenção Receita: {_get_spec(specs, 'Retenção Receita')}" if _get_spec(specs, "Retenção Receita") else None,
                        f"Tipo Receita: {_get_spec(specs, 'Tipo Receita')}" if _get_spec(specs, "Tipo Receita") else None,
                    ])
                ) or None) if eh_medicamento_no_catalogo else None,
                url=f"https://www.drogariavenancio.com.br/{link_text}/p",
                image1=images[0].get("imageUrl") if len(images) > 0 else None,
                image2=images[1].get("imageUrl") if len(images) > 1 else None,
                image3=images[2].get("imageUrl") if len(images) > 2 else None,
                image4=images[3].get("imageUrl") if len(images) > 3 else None,
                ean_conferido=True,
            )
        except Exception:
            return None
