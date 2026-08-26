"""
Vocabulário fechado do cadastro.

O banco persiste códigos (medicamento, vermelha), booleanos e FKs — nunca
rótulo de tela ("Tarja Vermelha") nem "Sim"/"Não". Rótulos existem nas
tabelas de referência (`tipos_produto`, `tarjas`) e aqui, para prompts do
Claude, planilhas e texto de farmácia.

Aceita também o formato antigo na entrada (parse_*), pra migração e pra
resposta do modelo, que continua pedindo os nomes em português.
"""

TIPO_MEDICAMENTO = "medicamento"
TIPO_NAO_MEDICAMENTO = "nao_medicamento"

TIPOS_PRODUTO = {
    TIPO_MEDICAMENTO: "Medicamento",
    TIPO_NAO_MEDICAMENTO: "Não Medicamento",
}

TARJA_SEM = "sem_tarja"
TARJA_VERMELHA = "vermelha"
TARJA_PRETA = "preta"
TARJA_NAO_APLICAVEL = "nao_aplicavel"

TARJAS = {
    TARJA_SEM: "Sem Tarja",
    TARJA_VERMELHA: "Tarja Vermelha",
    TARJA_PRETA: "Tarja Preta",
    TARJA_NAO_APLICAVEL: "Não aplicável",
}

FASE_PENDENTE = "pendente"
FASE_CONCLUIDO = "concluido"
FASE_NAO_LOCALIZADO = "nao_localizado"

ORIGEM_CAT_IQVIA = "mapeamento_iqvia"
ORIGEM_CAT_CMED = "mapeamento_cmed"
ORIGEM_CAT_IA = "ia"

_BOOL_TRUE = {"sim", "true", "1", "yes", "s"}
_BOOL_FALSE = {"não", "nao", "false", "0", "no", "n"}

_CAMPOS_BOOL = (
    "generico",
    "precisa_retencao_receita",
    "confirmado_anvisa_cmed",
    "precisa_validacao_humana",
    "tarja_confirmada_bulario",
    "tarja_confirmada_iqvia_mip",
)


def _norm(valor):
    if valor is None:
        return ""
    texto = str(valor).strip().lower()
    return (
        texto.replace("á", "a")
        .replace("à", "a")
        .replace("ã", "a")
        .replace("â", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("õ", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )


def parse_tipo_produto(valor):
    """Código `medicamento`/`nao_medicamento`, ou None se vazio/desconhecido."""
    if valor is None or valor == "":
        return None
    if valor in TIPOS_PRODUTO:
        return valor
    n = _norm(valor)
    compacto = n.replace(" ", "_").replace("-", "_")
    if compacto == TIPO_MEDICAMENTO:
        return TIPO_MEDICAMENTO
    if compacto in (TIPO_NAO_MEDICAMENTO, "nao_medicamento", "naomedicamento"):
        return TIPO_NAO_MEDICAMENTO
    if n == "medicamento":
        return TIPO_MEDICAMENTO
    if "nao medicamento" in n:
        return TIPO_NAO_MEDICAMENTO
    return None


def nome_tipo_produto(codigo):
    return TIPOS_PRODUTO.get(codigo)


def parse_tarja(valor):
    """Código de tarja, ou None se vazio/não reconhecido."""
    if valor is None or valor == "":
        return None
    if valor in TARJAS:
        return valor
    n = _norm(valor)
    if "nao aplicavel" in n or n in ("nao_aplicavel", "na"):
        return TARJA_NAO_APLICAVEL
    if "preta" in n or "preto" in n:
        return TARJA_PRETA
    if "vermelh" in n:
        return TARJA_VERMELHA
    if "sem tarja" in n or n in ("sem_tarja", "isento", "venda livre"):
        return TARJA_SEM
    return None


def nome_tarja(codigo):
    return TARJAS.get(codigo)


def parse_bool(valor):
    """True/False a partir de bool, Sim/Não ou equivalente; None se vazio."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, bool):
        return valor
    n = _norm(valor)
    if n in _BOOL_TRUE:
        return True
    if n in _BOOL_FALSE:
        return False
    return None


def eh_medicamento(valor):
    if isinstance(valor, dict):
        valor = valor.get("tipo_produto") or valor.get("tipo_cadastro")
    return parse_tipo_produto(valor) == TIPO_MEDICAMENTO


def eh_verdadeiro(valor):
    return parse_bool(valor) is True


def normalizar_cadastro(data):
    """
    Converte rótulos/legado para o vocabulário persistido, in-place.
    tipo_cadastro → tipo_produto; model → modelo; Sim/Não → bool.
    """
    if not data:
        return data
    if "tipo_produto" in data or "tipo_cadastro" in data:
        bruto = data["tipo_produto"] if "tipo_produto" in data else data.get("tipo_cadastro")
        data["tipo_produto"] = parse_tipo_produto(bruto)
        data.pop("tipo_cadastro", None)
    if "tarja" in data:
        data["tarja"] = parse_tarja(data.get("tarja"))
    for chave in _CAMPOS_BOOL:
        if chave in data:
            data[chave] = parse_bool(data.get(chave))
    if "modelo" not in data and "model" in data:
        data["modelo"] = data.pop("model")
    else:
        data.pop("model", None)
    return data
