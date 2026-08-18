import json
import re

from ..base import SiteAdapter
from ..models import ProductResult


class UltraFarmaAdapter(SiteAdapter):
    name = "ultrafarma"

    def search(self, ean):
        s = self._session()
        try:
            url = f"https://www.ultrafarma.com.br/busca?t={ean}"
            r = s.get(url, timeout=15)
            if r.status_code != 200:
                return None

            if not re.search(rf'data-attr="cod_ean"\s+data-attr-value="{re.escape(ean)}"', r.text):
                return None

            name = None
            m = re.search(r'<h1[^>]*>(.*?)</h1>', r.text, re.DOTALL)
            if m:
                name = m.group(1).strip()

            price = None
            m = re.search(r'R\$\s*([\d.,]+)', r.text)
            if m:
                try:
                    price = float(m.group(1).replace(".", "").replace(",", "."))
                except ValueError:
                    pass

            images = []
            m = re.search(r"LeanEcommerce\.PDP_PRODUTO_IMAGENS\s*=\s*JSON\.parse\('(.*?)'\)", r.text)
            if m:
                try:
                    img_data = json.loads(m.group(1).replace("\\'", "'"))
                    if isinstance(img_data, list):
                        images = [i.get("url") or i.get("src") or i.get("image") for i in img_data if isinstance(i, dict)]
                except Exception:
                    pass

            return ProductResult(
                ean=ean, name=name, url=url,
                image1=images[0] if len(images) > 0 else None,
                image2=images[1] if len(images) > 1 else None,
                image3=images[2] if len(images) > 2 else None,
                image4=images[3] if len(images) > 3 else None,
            )
        except Exception:
            return None
