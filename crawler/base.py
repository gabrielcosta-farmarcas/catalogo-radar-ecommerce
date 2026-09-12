import csv
import os
import re
import threading
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from html import unescape

import requests

try:
    # alguns sites (ex: Época Cosméticos) exigem um handshake TLS que o
    # ssl nativo deste Python não faz (aqui, LibreSSL 2.8.3 do venv - bem
    # mais antigo que o exigido, dá SSLError: TLSV1_ALERT_PROTOCOL_VERSION
    # mesmo com o site no ar e respondendo normal pra outros clientes,
    # como visto testando o mesmo endpoint com curl). Troca a implementação
    # TLS do urllib3 pela do pyOpenSSL (mais completa) quando disponível;
    # sem o pacote instalado, segue com o ssl nativo - não quebra nada dos
    # outros adapters, que já funcionam sem isso.
    import urllib3.contrib.pyopenssl

    urllib3.contrib.pyopenssl.inject_into_urllib3()
except ImportError:
    pass

from .models import ProductResult
from dominios import parse_tarja

# intervalo mínimo (segundos) entre duas requisições HTTP ao MESMO site -
# cada adapter é um singleton reusado em ADAPTERS_EM_ORDEM (ver
# enrich_com_crawler.py), com sessão e lock compartilhados entre as threads
# que processam EANs em paralelo, então isso serializa e espaça as chamadas
# por site mesmo sob concorrência alta. Existe pra evitar bloqueio/anti-bot
# por rajada de requisições (ver histórico: Drogasil/DrogaRaia retornando
# vazio quando batidos em paralelo, funcionando isolado). Sobrescrevível via
# env var pra debug/ajuste sem editar código.
INTERVALO_MINIMO_ENTRE_CHAMADAS = float(os.environ.get("CRAWLER_SLEEP_SEGUNDOS", "1.5"))


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
    """Normaliza texto livre de farmácia pro código persistido (sem_tarja,
    vermelha, preta). None se não reconhecer."""
    return parse_tarja(valor)


def mapear_generico_texto(tipo_medicamento):
    """True/False a partir de 'Tipo de Medicamento' de farmácia; None se vazio."""
    if not tipo_medicamento:
        return None
    return "gener" in tipo_medicamento.strip().lower().replace("é", "e")


def html_to_text(html):
    if not html:
        return None
    text = unescape(html)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# Sem User-Agent de Chrome nem Client Hints (sec-ch-ua / sec-fetch-*).
# Drogasil/Droga Raia (Akamai Bot Manager) devolvem 403 Access Denied quando
# o pedido afirma ser Chrome e o TLS é Python/urllib3. Sem fingir navegador,
# o python-requests padrão passa; Pacheco/Panvel não dependem desses headers.
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
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
            self._sess_lock = threading.Lock()
            self._ultima_chamada = 0.0
            s.request = self._request_com_intervalo(s.request)
            self._sess = s
        return self._sess

    def _request_com_intervalo(self, request_original):
        """Envolve Session.request pra respeitar INTERVALO_MINIMO_ENTRE_CHAMADAS
        entre chamadas consecutivas a este site - .get()/.post() do requests
        chamam .request() por baixo, então isso cobre todo adapter sem
        precisar mexer em cada um. O lock fica preso durante a requisição
        inteira (não só o sleep), o que serializa as chamadas a este site
        entre as threads que processam EANs em paralelo."""

        def request_envolvida(*args, **kwargs):
            with self._sess_lock:
                espera = INTERVALO_MINIMO_ENTRE_CHAMADAS - (time.monotonic() - self._ultima_chamada)
                if espera > 0:
                    time.sleep(espera)
                try:
                    return request_original(*args, **kwargs)
                finally:
                    self._ultima_chamada = time.monotonic()

        return request_envolvida

    def _session_with_tokens(self):
        s = self._session()
        if not s.cookies.get("carttoken"):
            token = str(uuid.uuid4())
            s.cookies.set("carttoken", token)
            s.cookies.set("guesttoken", token)
        return s
