from datetime import date, datetime
from decimal import Decimal


def json_limpo(valor):
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return float(valor)
    if hasattr(valor, "item"):
        try:
            return valor.item()
        except Exception:
            pass
    if isinstance(valor, dict):
        return {chave: json_limpo(item) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [json_limpo(item) for item in valor]
    if isinstance(valor, float) and valor != valor:
        return None
    return valor
