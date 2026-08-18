from .raiadrogasil import DrogasilAdapter, DrogaRaiaAdapter
from .panvel import PanvelAdapter
from .venancio import VenancioAdapter
from .drogaria_sp import DrogariaSPAdapter
from .drogaria_pacheco import DrogariaPachecoAdapter
from .ultrafarma import UltraFarmaAdapter
from .araujo import AraujoAdapter
from .drogal import DrogalAdapter
from .sara import SaraAdapter

ALL_ADAPTERS = [
    SaraAdapter(),
    DrogasilAdapter(),
    DrogaRaiaAdapter(),
    PanvelAdapter(),
    VenancioAdapter(),
    DrogariaSPAdapter(),
    DrogariaPachecoAdapter(),
    # UltraFarmaAdapter(),  # nao busca por EAN
    AraujoAdapter(),
    DrogalAdapter(),
]
