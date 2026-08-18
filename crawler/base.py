import csv
import os
import re
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from html import unescape

import requests

from .models import ProductResult


def normalizar_ean(valor):
    """Mesma regra de cmed.normalizar_ean, local pra o crawler não depender
    do banco. '0' não é EAN válido."""
    digitos = re.sub(r"\D", "", str(valor or ""))
    if not digitos:
        return ""
    normalizado = str(int(digitos))
    return "" if normalizado == "0" else normalizado


def ean_igual(encontrado, pedido):
    a, b = normalizar_ean(encontrado), normalizar_ean(pedido)
    return bool(a) and a == b


def mapear_tarja_texto(valor):
    """Normaliza texto livre de farmácia pro vocabulário fechado. None se
    não reconhecer - melhor que passar 'Classificação' cru e o safety check
    zerar depois, ou inferir tarja vermelha por precaução."""
    if not valor:
        return None
    texto = str(valor).strip().upper()
    if not texto:
        return None
    if "PRETA" in texto or "PRETO" in texto:
        return "Tarja Preta"
    if "VERMELH" in texto:
        return "Tarja Vermelha"
    if (
        "SEM TARJA" in texto
        or "ISENTO" in texto
        or "VENDA LIVRE" in texto
        or "TARJA SEM TARJA" in texto
    ):
        return "Sem Tarja"
    return None


def html_to_text(html):
    if not html:
        return None
    text = unescape(html)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,pt;q=0.8",
    "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
}


class SiteAdapter(ABC):
    name = ""

    def work(self, rows):
        """Grava incrementalmente em data/<site>.csv e retoma de onde parou."""
        os.makedirs("data", exist_ok=True)
        path = f"data/{self.name}.csv"
        campos = list(asdict(ProductResult()).keys())

        feitos = set()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f, delimiter=";"):
                    if r.get("ean"):
                        feitos.add(r["ean"].strip())

        pendentes = [r for r in rows if str(r.get("ean", "")).strip() not in feitos]
        novo = not os.path.exists(path) or os.path.getsize(path) == 0

        with open(path, "a", encoding="utf-8", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=campos, delimiter=";")
            if novo:
                writer.writeheader()
                out.flush()
            with ThreadPoolExecutor(max_workers=5) as ex:
                for i, r in enumerate(ex.map(self._search_one, pendentes), 1):
                    if r is not None:
                        writer.writerow(r)
                    if i % 50 == 0:
                        out.flush()
            out.flush()

    def _search_one(self, row):
        ean = str(row.get("ean", "")).strip()
        if not ean:
            return None
        try:
            r = self.search(ean) or ProductResult(ean=ean)
        except Exception:  # noqa: BLE001 - falha de rede/parse nao derruba o site inteiro
            r = ProductResult(ean=ean)
        return asdict(r)

    @abstractmethod
    def search(self, ean):
        ...

    def _session(self):
        if not hasattr(self, "_sess"):
            s = requests.Session()
            s.headers.update(HEADERS)
            self._sess = s
        return self._sess

    def _session_with_tokens(self):
        s = self._session()
        if not s.cookies.get("carttoken"):
            token = str(uuid.uuid4())
            s.cookies.set("carttoken", token)
            s.cookies.set("guesttoken", token)
        return s
