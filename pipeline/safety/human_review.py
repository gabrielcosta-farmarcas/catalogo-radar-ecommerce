"""Fila de validação humana — regras por origem, só medicamento (salvo ambiguidade)."""

from __future__ import annotations

from dominios import (
    ORIGEM_ABCFARMA,
    ORIGEM_ANVISA_CMED,
    ORIGEM_CLAUDE,
    ORIGEM_CRAWLER,
    ORIGEM_IQVIA,
    ORIGEM_TARJADOS,
    eh_medicamento,
    eh_verdadeiro,
    origem_codigo,
)

VALIDACAO_HUMANA_COLUMN = "precisa_validacao_humana"
MENSAGEM_VALIDACAO_COLUMN = "mensagem_validacao_humana"
VALIDACAO_COLUMNS = [VALIDACAO_HUMANA_COLUMN, MENSAGEM_VALIDACAO_COLUMN]

MENSAGEM_VALIDACAO_CLAUDE_MEDICAMENTO = (
    "VALIDAÇÃO HUMANA OBRIGATÓRIA: este medicamento foi encontrado apenas "
    "na busca na internet (Claude), não na tabela oficial da ANVISA/CMED "
    "nem em site confiável (bulário/farmácia com ficha técnica). Não "
    "publicar no e-commerce antes de um responsável conferir tarja, "
    "registro MS, princípio ativo e se é de fato esta apresentação."
)

MENSAGEM_VALIDACAO_CMED_TARJA = (
    "VALIDAÇÃO HUMANA OBRIGATÓRIA: a CMED/ANVISA não informou a tarja deste "
    "medicamento (campo tarja vazio na base oficial - "
    "confirmado_anvisa_cmed continua Sim para os outros campos, que "
    "seguem confiáveis). Não publicar no e-commerce antes de um "
    "responsável confirmar a tarja em fonte oficial (bula/ANVISA)."
)

MENSAGEM_VALIDACAO_ABCFARMA_TARJA = (
    "VALIDAÇÃO HUMANA OBRIGATÓRIA: este medicamento foi confirmado pela "
    "base ABCFarma (registro MS/princípio ativo/fabricante confiáveis), mas "
    "essa base não traz a informação de tarja. Não publicar no e-commerce "
    "antes de um responsável confirmar a tarja em fonte oficial (bula/ "
    "ANVISA)."
)

MENSAGEM_VALIDACAO_CRAWLER_TARJA = (
    "VALIDAÇÃO HUMANA OBRIGATÓRIA: este medicamento foi encontrado em "
    "farmácia online, mas a tarja não veio do bulário oficial (Sara/ANVISA). "
    "Não publicar no e-commerce antes de um responsável confirmar a tarja "
    "em fonte oficial (bula/ANVISA)."
)

MENSAGEM_VALIDACAO_IQVIA_TARJA = (
    "VALIDAÇÃO HUMANA OBRIGATÓRIA: este medicamento foi confirmado pela "
    "base IQVIA como \"requer receita\" (RX), mas essa classificação não "
    "distingue Tarja Vermelha de Tarja Preta, e a tarja não foi confirmada "
    "nem pelo bulário nem por verificação dedicada. Não publicar no "
    "e-commerce antes de um responsável confirmar a tarja em fonte oficial "
    "(bula/ANVISA)."
)

MENSAGEM_VALIDACAO_TARJADOS_TARJA = (
    "VALIDAÇÃO HUMANA OBRIGATÓRIA: este medicamento foi confirmado pela "
    "base de Tarjados do time (RX/CONTROLADO/NÃO INFORMADO), mas essa base "
    "não distingue Tarja Vermelha de Tarja Preta, e a tarja não foi "
    "confirmada nem pelo bulário nem por verificação dedicada. Não "
    "publicar no e-commerce antes de um responsável confirmar a tarja "
    "em fonte oficial (bula/ANVISA)."
)

MENSAGEM_VALIDACAO_TARJADOS_TIPO_AMBIGUO = (
    "VALIDAÇÃO HUMANA OBRIGATÓRIA: este produto veio da base de Tarjados "
    "com TIPO DE PRODUTO de medicamento (RX/CONTROLADO), mas a categoria "
    "já revisada pelo time mapeia para o ramo Não Medicamento da árvore "
    "oficial - confirmar se o tipo_produto está correto antes de publicar "
    "no e-commerce."
)


def marcar_validacao_humana(data):
    if not data:
        return data
    origem = origem_codigo(data) or ORIGEM_CLAUDE
    so_web = origem == ORIGEM_CLAUDE
    so_cmed = origem == ORIGEM_ANVISA_CMED
    so_abcfarma = origem == ORIGEM_ABCFARMA
    so_tarjados = origem == ORIGEM_TARJADOS
    so_iqvia = origem == ORIGEM_IQVIA
    so_crawler = origem == ORIGEM_CRAWLER
    medicamento = eh_medicamento(data)
    motivo_categoria_invalida = data.get("_categorizacao_invalida")
    suspeita_suplemento_abcfarma = data.get("_suspeita_suplemento_abcfarma", False)
    tarja_bulario = eh_verdadeiro(data.get("tarja_confirmada_bulario"))
    tarja_mip_iqvia = eh_verdadeiro(data.get("tarja_confirmada_iqvia_mip"))
    if medicamento and so_web:
        data[VALIDACAO_HUMANA_COLUMN] = True
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_CLAUDE_MEDICAMENTO
    elif medicamento and so_cmed and not data.get("tarja"):
        data[VALIDACAO_HUMANA_COLUMN] = True
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_CMED_TARJA
    elif medicamento and so_abcfarma and not data.get("tarja"):
        data[VALIDACAO_HUMANA_COLUMN] = True
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_ABCFARMA_TARJA
    elif medicamento and so_tarjados and not data.get("tarja"):
        data[VALIDACAO_HUMANA_COLUMN] = True
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_TARJADOS_TARJA
    elif medicamento and so_iqvia and not data.get("tarja"):
        data[VALIDACAO_HUMANA_COLUMN] = True
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_IQVIA_TARJA
    elif medicamento and so_abcfarma and not tarja_bulario:
        data[VALIDACAO_HUMANA_COLUMN] = True
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_CRAWLER_TARJA
    elif medicamento and so_tarjados and not tarja_bulario:
        data[VALIDACAO_HUMANA_COLUMN] = True
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_CRAWLER_TARJA
    elif medicamento and so_iqvia and not (tarja_bulario or tarja_mip_iqvia):
        data[VALIDACAO_HUMANA_COLUMN] = True
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_CRAWLER_TARJA
    elif medicamento and so_crawler and (not data.get("tarja") or not tarja_bulario):
        data[VALIDACAO_HUMANA_COLUMN] = True
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_CRAWLER_TARJA
    else:
        data[VALIDACAO_HUMANA_COLUMN] = False
        data[MENSAGEM_VALIDACAO_COLUMN] = None

    if medicamento and motivo_categoria_invalida:
        mensagem_categoria = (
            "VALIDAÇÃO HUMANA OBRIGATÓRIA: categorização não confere com a "
            f"árvore oficial ({motivo_categoria_invalida}) - revisar "
            "departamento/categoria/subcategoria antes de publicar no "
            "e-commerce."
        )
        mensagem_atual = data.get(MENSAGEM_VALIDACAO_COLUMN)
        data[VALIDACAO_HUMANA_COLUMN] = True
        data[MENSAGEM_VALIDACAO_COLUMN] = (
            f"{mensagem_atual} | {mensagem_categoria}" if mensagem_atual else mensagem_categoria
        )

    if medicamento and suspeita_suplemento_abcfarma:
        mensagem_suplemento = (
            "VALIDAÇÃO HUMANA OBRIGATÓRIA: fonte ABCFarma classifica este "
            "item como tipo_medicamento=\"OUTROS\" sem registro_ms de "
            "medicamento (vazio ou citação de RDC de suplemento alimentar) - "
            "confirmar se é medicamento de verdade ou suplemento alimentar "
            "antes de publicar no e-commerce."
        )
        mensagem_atual = data.get(MENSAGEM_VALIDACAO_COLUMN)
        data[VALIDACAO_HUMANA_COLUMN] = True
        data[MENSAGEM_VALIDACAO_COLUMN] = (
            f"{mensagem_atual} | {mensagem_suplemento}" if mensagem_atual else mensagem_suplemento
        )
    return data
