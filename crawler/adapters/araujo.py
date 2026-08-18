from ..base import SiteAdapter, ean_igual
from ..models import ProductResult


class AraujoAdapter(SiteAdapter):
    name = "araujo"

    def search(self, ean):
        s = self._session()
        s.headers.update({
            "Accept": "*/*",
            "x-requested-with": "XMLHttpRequest",
            "referer": f"https://www.araujo.com.br/busca?q={ean}&lang=pt_BR",
        })
        try:
            url = f"https://www.araujo.com.br/on/demandware.store/Sites-Araujo-Site/pt_BR/SearchServices-GetSuggestionsJson?q={ean}&lang=pt_BR"
            r = s.get(url, timeout=15)
            if r.status_code != 200:
                return None

            data = r.json()
            products = data.get("suggestions", {}).get("product", {}).get("products", [])
            if not products:
                return None

            p = products[0]
            encontrado = p.get("ean") or p.get("barcode") or p.get("upc")
            if encontrado and not ean_igual(encontrado, ean):
                return None

            product_url = p.get("url", "")
            if product_url and not product_url.startswith("http"):
                product_url = f"https://www.araujo.com.br{product_url}"

            return ProductResult(
                ean=ean,
                name=p.get("name"),
                url=product_url,
                image1=p.get("imageUrl"),
                ean_conferido=bool(encontrado),
            )
        except Exception:
            return None
