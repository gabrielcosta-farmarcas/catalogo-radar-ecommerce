from dataclasses import dataclass


@dataclass
class ProductResult:
    ean: str = None
    name: str = None
    description: str = None
    short_description: str = None
    brand: str = None
    manufacturer: str = None
    category: str = None
    active_ingredient: str = None
    dosage: str = None
    quantity: str = None
    ms_register: str = None
    generico: str = None
    tarja: str = None
    prescricao_detalhe: str = None
    leaflet_url: str = None
    url: str = None
    image1: str = None
    image2: str = None
    image3: str = None
    image4: str = None
    # True só quando o HTML/JSON do site expôs o EAN pedido. Sem isso, o
    # adapter pode ter devolvido o 1º resultado da busca (outro produto).
    ean_conferido: bool = False
