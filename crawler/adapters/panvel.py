import re
import uuid

from ..base import SiteAdapter, html_to_text, ean_igual
from ..models import ProductResult


class PanvelAdapter(SiteAdapter):
    name = "panvel"

    def search(self, ean):
        session_id = str(uuid.uuid4())
        s = self._session()
        s.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "app-token": "ZYkPuDaVJEiD",
            "client-ip": "1",
            "finger-print": "890b639879c8ac58ebf7832ca45a661c",
            "origin": "https://www.panvel.com",
            "referer": f"https://www.panvel.com/panvel/buscarProduto.do?termoPesquisa={ean}",
            "search-new": "A",
            "sessionid": session_id,
            "source": "desktop",
            "user-id": "0",
        })
        s.cookies.set("cookie_state", "SP")
        s.cookies.set("UF", "SP")
        s.cookies.set("appName", "search-v2")
        s.cookies.set("sessionId", session_id)

        try:
            r = s.post(
                "https://www.panvel.com/api/v3/search?type=CSR&uf=SP",
                json={
                    "term": ean,
                    "itemsPerPage": 24,
                    "currentPage": 1,
                    "assortment": "mais relevantes",
                    "filters": [],
                    "searchOffers": False,
                    "searchType": "term",
                },
                timeout=15,
            )
            if r.status_code != 200:
                return None

            data = r.json()
            items = data.get("items", [])
            if not items:
                return None

            item = items[0]
            event = item.get("event", {}) or {}
            encontrado = (
                item.get("ean")
                or item.get("barcode")
                or item.get("gtin")
                or event.get("itemEan")
                or event.get("ean")
            )
            if encontrado and not ean_igual(encontrado, ean):
                return None
            panvel_code = item.get("panvelCode")

            cats = [event.get(k) for k in ("itemCategory1", "itemCategory2", "itemCategory3") if event.get(k)]

            description = None
            leaflet_url = None
            if panvel_code:
                try:
                    rd = s.get(f"https://www.panvel.com/api/v2/catalog/{panvel_code}", timeout=15)
                    if rd.status_code == 200:
                        description = html_to_text(rd.json().get("description"))
                except Exception:
                    pass

                try:
                    rb = s.get(f"https://www.panvel.com/panvel/x/{panvel_code}/bula", timeout=15)
                    if rb.status_code == 200:
                        m = re.search(r'https://[^"\'<>\s]+\.pdf[^"\'<>\s]*', rb.text)
                        if m:
                            leaflet_url = m.group(0)
                except Exception:
                    pass

            return ProductResult(
                ean=ean,
                name=item.get("name"),
                description=description,
                brand=item.get("brandName"),
                quantity=item.get("presentationTitle"),
                category=" > ".join(cats) if cats else None,
                leaflet_url=leaflet_url,
                url=item.get("link"),
                image1=item.get("image"),
                ean_conferido=bool(encontrado),
            )
        except Exception:
            return None
