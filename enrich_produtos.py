"""
Enriquece produtos pendentes da tabela `produtos` (Postgres, ver db.py)
buscando informações na internet via Claude (Anthropic API), usando os tools
nativos web_search e web_fetch.

Uso básico:
    python enrich_produtos.py

Uso com opções:
    python enrich_produtos.py --eans 7891234567890,7899876543210 --limit 20

Requer a variável de ambiente ANTHROPIC_API_KEY - defina num arquivo .env na
raiz do projeto (carregado automaticamente) ou exporte no shell antes de
rodar. Também funciona com uma sessão autenticada via `ant auth login`.
"""

import argparse
import difflib
import io
import ipaddress
import json
import os
import re
import socket
import sys
import time
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import httpx
import pandas as pd
import psycopg2
from anthropic import Anthropic, APIStatusError, APIConnectionError
from PIL import Image, UnidentifiedImageError

import categorias


def _carregar_dotenv():
    """
    Carrega variáveis do arquivo .env (na raiz do projeto, mesma pasta deste
    script) pro ambiente, se ainda não estiverem definidas - evita ter que
    rodar `source .env` manualmente antes de cada execução. Nunca sobrescreve
    uma variável já exportada no shell (essa sempre tem prioridade).
    """
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(caminho):
        return
    with open(caminho, encoding="utf-8") as arquivo:
        for linha in arquivo:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, _, valor = linha.partition("=")
            valor = valor.strip().strip('"').strip("'")
            os.environ.setdefault(chave.strip(), valor)


_carregar_dotenv()

# API key da Anthropic: defina ANTHROPIC_API_KEY num arquivo .env na raiz do
# projeto (esse arquivo já está no .gitignore - nunca cole a key aqui no
# código-fonte, isso vaza a chave pra quem tiver acesso ao repositório).
os.environ.setdefault("ANTHROPIC_API_KEY", "COLOQUE_SUA_KEY_AQUI")

def load_categorization_tree():
    """
    Lê a árvore oficial de categorização da tabela `categorias` no Postgres
    (ver categorias.carregar_arvore) e retorna (texto, combinacoes,
    arvores_por_ramo):
    - texto: árvore compacta completa (os 2 ramos) - fallback se o tipo
      ainda não for conhecido numa chamada de formatação. O Claude puro
      de busca NÃO recebe essa árvore: categoriza depois, só com o ramo.
    - combinacoes: set de tuplas (tipo_produto, departamento, categoria,
      subcategoria) - usado para validar em código se a combinação que o
      modelo devolveu realmente existe na árvore oficial (ver
      validar_categorizacao).
    - arvores_por_ramo: dict tipo_cadastro -> texto só daquele ramo, para
      as chamadas de formatação em que tipo_cadastro já veio da CMED, do
      crawler ou da busca agentic. Mandar os dois ramos nesses casos só
      infla o prompt.
    Retorna (None, set(), {}) se a tabela estiver vazia/não existir ou o
    Postgres estiver fora do ar (o script ainda funciona, mas sem a
    taxonomia oficial).
    """
    try:
        combinacoes, arvores_por_ramo = categorias.carregar_arvore()
    except psycopg2.OperationalError as exc:
        print(
            f"[aviso] não foi possível conectar ao Postgres para carregar a "
            f"árvore de categorização ({exc}) - departamento/categoria/"
            "subcategoria serão classificados sem uma taxonomia oficial.",
            file=sys.stderr,
        )
        return None, set(), {}

    if not arvores_por_ramo:
        print(
            "[aviso] tabela categorias vazia ou inexistente - "
            "departamento/categoria/subcategoria serão classificados sem "
            "uma taxonomia oficial.",
            file=sys.stderr,
        )
        return None, set(), {}

    texto = "\n".join(arvores_por_ramo.values())
    return texto, combinacoes, arvores_por_ramo


ARVORE_CATEGORIZACAO, COMBINACOES_CATEGORIZACAO_VALIDAS, ARVORES_POR_RAMO = (
    load_categorization_tree()
)

SYSTEM_PROMPT = """Você é um especialista em cadastro de produtos farmacêuticos para e-commerce \
(medicamentos, dermocosméticos, higiene, beleza, suplementos, puericultura, dispositivos médicos).

PROCESSO: web_search (máx. 3) em qualquer fonte confiável (fabricante, ANVISA/Bulário, farmácias \
online); em divergência, priorize fabricante > ANVISA > farmácias. web_fetch (máx. 3) na(s) \
página(s) mais confiável(is). Busque a URL de imagem do produto no HTML (src/data-src/og:image, \
.jpg/.png/.webp) SOMENTE se for Não Medicamento. Se for medicamento (qualquer tarja, inclusive \
Sem Tarja), NUNCA retorne imagem_url (null), mesmo existindo.

REGRA CRÍTICA: nunca invente, deduza, estime ou infira dado algum; nunca use produto ou \
apresentação semelhante. Campo não confirmado na fonte = null (sempre melhor que dado errado).

TÍTULO (campo titulo, sem hífens) - título de e-commerce, curto e direto (até ~70 caracteres - é \
o que o cliente digita/lê na busca, título longo demais não é buscável e corta na listagem), sem \
repetir tudo que já está no campo principios_ativos:
- Medicamento: comece pela marca/nome comercial (referência) ou pelo princípio ativo (genérico \
ou combinação sem marca própria). NUNCA comece pela finalidade terapêutica (ex: "Analgésico", \
"Antiácido Efervescente") - isso atrapalha a busca. Depois: um descritor curto quando existir \
(sabor, forma), público-alvo quando aplicável (Adulto/Infantil), terminando em quantidade + \
forma farmacêutica.
- Não medicamento: [O que o produto é / Categoria] [Marca] [Linha] [Atributo/Especificação] \
[Volume/Quantidade]. "Categoria" é o tipo do objeto em português (Pomada, Fio Dental, \
Curativos, Absorvente, Shampoo, Enxaguante Bucal, Fórmula Infantil, Hastes Flexíveis, \
Fralda, Seringa), NÃO a finalidade terapêutica e NÃO o departamento da árvore. Ordem \
obrigatória: o que o produto É vem PRIMEIRO; marca vem DEPOIS. Pule o slot se não existir \
(sem linha = não invente linha). NUNCA comece pela marca - esse é o erro mais comum neste \
campo para não-medicamento. Errado: "Hipoglós Pomada Creme Assaduras 40g" / certo: "Pomada \
Creme Assaduras Hipoglós 40g". Errado: "Johnson's Baby Shampoo Regular 400ml" / certo: \
"Shampoo Johnson's Baby Regular 400ml". Errado: "Johnson's Reach Essencial Fio Dental \
Menta 100 Metros" / certo: "Fio Dental Johnson's Reach Essencial Menta 100 Metros". \
Errado: "Band Aid Curativos Transparente Respirável 40 Unidades" / certo: "Curativos Band \
Aid Transparente Respirável 40 Unidades". Errado: "Sempre Livre Absorvente Noturno com \
Abas Suave Leve 32 Unidades" / certo: "Absorvente Sempre Livre Noturno com Abas Suave \
Leve 32 Unidades". Errado: "Cotonete Johnson & Johnson Hastes Flexíveis 75 Unidades" / \
certo: "Hastes Flexíveis Cotonete Johnson & Johnson 75 Unidades". Errado: "Periogard \
Enxaguante Bucal Extra Mint Sem Álcool 250ml" / certo: "Enxaguante Bucal Periogard Extra \
Mint Sem Álcool 250ml". Errado: "Aptamil 2 Fórmula Infantil 400g" / certo: "Fórmula \
Infantil Aptamil 2 400g".
Composição no título: com nome comercial reconhecido e 3 ou mais princípios ativos, NUNCA liste a \
composição completa no título, mesmo que a fonte mostre todas as concentrações - use só marca + \
descritor + quantidade/forma; a composição completa já vai inteira no campo principios_ativos, \
não precisa repetir no título. Esse é o erro mais comum nesse campo - antes de responder, confira \
se o título tem 3+ trechos "nome + mg/mcg/g/ml" e, se tiver e existir marca, corte-os. Com 1-2 \
princípios ativos, ou sem nome comercial (genérico/combinação sem marca própria), inclua nome + \
concentração de cada um. NUNCA escreva uma concentração sem o nome do princípio ativo do lado - \
errado: "185mg + 235mg + 178mg"; certo: "Hidróxido de Alumínio 185mg + Hidróxido de Magnésio \
235mg". O nome do princípio ativo no título (incluindo o sal - Cloridrato/Maleato/Besilato/ \
Succinato/Bromidrato/Fumarato/Mesilato/Oxalato etc.) tem que ser EXATAMENTE o que veio confirmado \
na fonte - NUNCA troque por outro sal do mesmo fármaco só porque parece mais comum ou mais familiar \
(ex: não escreva "Cloridrato de Midazolam" se a fonte confirmou "Maleato de Midazolam" - são sais \
diferentes, trocar é erro factual, não estilo). Antes de responder, confira se o sal que você \
escreveu é literalmente o mesmo texto que veio confirmado, não uma variação "mais comum".
Exemplos: "Novalgina 1g Dipirona Adulto 20 Comprimidos"; "Vurtuoso Vortioxetina 20mg 60 \
Comprimidos"; "Paracetamol 750mg EMS Genérico 20 Comprimidos" (genérico); "Fralda Pampers \
Confort Sec XXG 56 Unidades"; "Seringa 3ml Ever Care Com Agulha 1 Unidade"; "Gastrol Pó \
Efervescente Sabor Laranja 6 Envelopes 5g" (nome comercial com 3 princípios ativos - composição \
só no campo principios_ativos); "Diosmina 450mg + Hesperidina 50mg 30 Comprimidos" (sem marca \
própria, inclui composição completa).

CAMPOS (só com base na fonte; null se não confirmado): marca/fabricante = nome oficial. \
tipo_cadastro = "Medicamento" ou "Não Medicamento". registro_ms = só medicamento, número exato da \
apresentação certa (null se não for medicamento). generico = "Sim"/"Não" (null se não for \
medicamento). tarja = "Sem Tarja"/"Tarja Vermelha"/"Tarja Preta"/"Não aplicável" - EXIGE fonte \
oficial explícita (bula/embalagem/ANVISA) confirmando o controle de venda dessa apresentação \
específica; NUNCA marque Tarja Vermelha/Preta por precaução, por ser antiácido/analgésico/etc, ou \
por outro produto da mesma classe terapêutica ser controlado - isso tem implicação legal (venda \
sob prescrição) e um erro aqui é pior que null. principios_ativos = \
todos com concentração, ordem da bula, uma string separada por vírgula. descricao_curta = até 250 \
caracteres (150-250 é o alvo quando a fonte sustenta isso, mas mais curta é o resultado certo se \
não houver informação real o suficiente - nunca invente conteúdo só pra alongar), técnica e \
objetiva, sem termos comerciais/emojis, com nome+marca+finalidade, escrita com suas próprias \
palavras - nunca copie frase da bula/página quase literalmente, mesmo trocando 1-2 palavras; pode \
usar sinônimo, nunca mudar o fato/grau/nuance médica. \
imagem_url = URL real encontrada na página, só para Não Medicamento; null se não achar \
ou se for medicamento (qualquer tarja). pagina_produto_url = URL da fonte principal. preco_pesquisado = preço exatamente como exibido na \
página da fonte principal (ex: "R$ 19,90"), null se a página não mostrar preço ou o preço achado \
não for claramente desta apresentação específica - é só uma referência do que foi visto na busca, \
NUNCA um dado oficial do produto, então nunca infira nem estime a partir de outra apresentação/ \
embalagem. frase_obrigatoria NÃO é campo de saída - \
é composta depois em código a partir de tarja/tipo_cadastro/genérico/fórmula infantil. \
departamento/categoria/subcategoria NÃO são campos de saída desta chamada - a árvore \
oficial é aplicada depois, numa formatação sem busca, só com o ramo do tipo_cadastro.

PADRONIZAÇÃO: unidades mg/mcg/g/kg/ml/L/UI; Comprimidos/Cápsulas/Sachês/Ampolas/Frasco/Bisnaga/ \
Envelope/Aplicador/Spray; nomenclatura padrão ("Preservativo" não "Camisinha"; "Tintura para \
Cabelo" não "Tinta para Cabelo"). Nunca inclua SKU, código interno/ERP/SAP, EAN, siglas internas, \
termos promocionais ou emojis em nenhum campo.

Responda APENAS com JSON válido, sem markdown: {"titulo": str|null, "marca": str|null, \
"fabricante": str|null, "tipo_cadastro": str|null, "registro_ms": str|null, "generico": str|null, \
"tarja": str|null, "principios_ativos": str|null, "descricao_curta": str|null, \
"imagem_url": str|null, "pagina_produto_url": str|null, "preco_pesquisado": str|null}."""

RESULT_COLUMNS = [
    "titulo",
    "marca",
    "fabricante",
    "tipo_cadastro",
    "registro_ms",
    "generico",
    "tarja",
    "precisa_retencao_receita",
    "principios_ativos",
    "descricao_curta",
    "frase_obrigatoria",
    "departamento",
    "categoria",
    "subcategoria",
    "imagem_url",
    "pagina_produto_url",
    "preco_pesquisado",
    "data_pesquisa",
]

STATUS_OK = "OK"
STATUS_NOT_FOUND = "Não localizado"

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


def marcar_validacao_humana(data):
    """
    Medicamento achado só na web (origem claude) precisa de revisão humana
    antes de ir ao ar - fonte não rastreável nenhum campo. Medicamento
    confirmado pela ABCFarma (que não tem coluna de tarja - ver
    ORIGEM_ABCFARMA) ou pela IQVIA como "RX" (que só diz "precisa receita",
    sem distinguir Vermelha de Preta - ver ORIGEM_IQVIA) só entra na fila se
    a tarja continuar sem confirmação depois das tentativas via crawler/
    busca dedicada (ver mapear_abcfarma_para_schema/mapear_iqvia_para_schema
    em enrich_com_crawler.py) - se o bulário (Sara) confirmar a tarja, o
    resto dos campos já vem da fonte e a linha segue o fluxo normal. Tarja
    só de farmácia (não Sara) também entra na fila: o modelo/site já inferiu
    tarja errada antes. Medicamento IQVIA classificado como "MIP"
    (Medicamento Isento de Prescrição - categoria regulatória oficial, não
    inferência de site) tem a tarja confirmada direto (ver
    tarja_confirmada_iqvia_mip) e não entra na fila só por causa disso. CMED
    e crawler com tarja do Sara seguem o fluxo normal. Não-medicamento nunca
    entra na fila, seja qual for a origem.
    """
    if not data:
        return data
    origem = str(data.get("origem_enriquecimento") or "claude").strip().lower()
    so_web = origem.startswith("claude")
    so_cmed = origem.startswith(ORIGEM_ANVISA_CMED)
    so_abcfarma = origem.startswith(ORIGEM_ABCFARMA)
    so_iqvia = origem.startswith(ORIGEM_IQVIA)
    so_crawler = origem.startswith("crawler")
    medicamento = data.get("tipo_cadastro") == "Medicamento"
    tarja_bulario = data.get("tarja_confirmada_bulario") == "Sim"
    tarja_mip_iqvia = data.get("tarja_confirmada_iqvia_mip") == "Sim"
    if medicamento and so_web:
        data[VALIDACAO_HUMANA_COLUMN] = "Sim"
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_CLAUDE_MEDICAMENTO
    elif medicamento and so_cmed and not data.get("tarja"):
        # CMED confirmou o medicamento mas não informou a tarja (campo
        # "- (*)") - ver mapear_cmed_para_schema em enrich_com_crawler.py,
        # que já não tenta cruzar com substancias_controladas nesse caso
        # (sem saber a cor da tarja, não dá pra confiar na retenção também)
        data[VALIDACAO_HUMANA_COLUMN] = "Sim"
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_CMED_TARJA
    elif medicamento and so_abcfarma and not data.get("tarja"):
        data[VALIDACAO_HUMANA_COLUMN] = "Sim"
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_ABCFARMA_TARJA
    elif medicamento and so_iqvia and not data.get("tarja"):
        data[VALIDACAO_HUMANA_COLUMN] = "Sim"
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_IQVIA_TARJA
    elif medicamento and so_abcfarma and not tarja_bulario:
        data[VALIDACAO_HUMANA_COLUMN] = "Sim"
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_CRAWLER_TARJA
    elif medicamento and so_iqvia and not (tarja_bulario or tarja_mip_iqvia):
        data[VALIDACAO_HUMANA_COLUMN] = "Sim"
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_CRAWLER_TARJA
    elif medicamento and so_crawler and (not data.get("tarja") or not tarja_bulario):
        data[VALIDACAO_HUMANA_COLUMN] = "Sim"
        data[MENSAGEM_VALIDACAO_COLUMN] = MENSAGEM_VALIDACAO_CRAWLER_TARJA
    else:
        data[VALIDACAO_HUMANA_COLUMN] = "Não"
        data[MENSAGEM_VALIDACAO_COLUMN] = None
    return data


def system_cached(texto):
    """System prompt em bloco com cache efêmero (5 min). Mesmo texto nas
    chamadas seguintes da janela é cobrado ~90% mais barato na entrada."""
    return [{"type": "text", "text": texto, "cache_control": {"type": "ephemeral"}}]


def nome_para_busca(valor):
    """Nome da planilha sempre em minúsculo para busca em base/internet.
    Não altera título de saída nem nomes oficiais (CMED/ABCFarma/IQVIA)."""
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    texto = str(valor).strip()
    if not texto or texto.lower() == "nan":
        return ""
    return texto.lower()


def build_user_message(ean, nome_produto, pistas_nao_confirmadas=None):
    partes = [f"EAN: {ean}\nNome: {nome_para_busca(nome_produto)}\n"]
    if pistas_nao_confirmadas:
        linhas = "\n".join(
            f"- {chave}: {valor}"
            for chave, valor in pistas_nao_confirmadas.items()
            if valor
        )
        if linhas:
            partes.append(
                "Pistas NÃO CONFIRMADAS de páginas de farmácia (podem ser "
                "outro produto ou outra apresentação. NÃO copie nenhum campo "
                "delas como fato. Só use se confirmar nesta apresentação "
                "específica numa fonte confiável. Se divergir, não confirmar, "
                "ou parecer produto/apresentação semelhante, ignore a pista "
                "por completo.)\n"
                f"{linhas}\n"
            )
    partes.append(
        "Retorne o JSON completo. Se for Não Medicamento, inclua a URL da "
        "imagem do produto se conseguir localizar no conteúdo da página. "
        "Se for Medicamento, imagem_url deve ser null."
    )
    return "\n".join(partes)


def extract_json(text):
    """
    Extrai o objeto JSON de um texto de resposta, mesmo quando o modelo o
    envolve em comentários e/ou cercas de markdown (ex: 'Aqui está o
    resultado:\n```json\n{...}\n```').
    """
    text = text.strip()

    # 1. bloco de código ```json ... ``` em qualquer posição do texto
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        return json.loads(fence_match.group(1))

    # 2. fallback: do primeiro '{' ao último '}' do texto
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])

    # 3. último recurso: assume que o texto já é JSON puro
    return json.loads(text)


def get_final_text(response):
    """
    Retorna apenas o último bloco de texto da resposta - os blocos
    anteriores são comentários do modelo entre chamadas de web_search/
    web_fetch (ex: 'Vou buscar informações...'), não a resposta final.
    """
    text_blocks = [block.text for block in response.content if block.type == "text"]
    return text_blocks[-1].strip() if text_blocks else ""


def response_usage(response):
    """
    Extrai o consumo de uma chamada à API: tokens totais (entrada + saída) e,
    para diagnosticar o prompt caching, quantos tokens foram gravados no
    cache (cache_creation_input_tokens, só na 1a chamada dentro da janela de
    5min) e quantos foram lidos do cache (cache_read_input_tokens, cobrados
    ~90% mais barato - se esse valor for > 0, o cache está funcionando).
    """
    usage = response.usage
    tokens = usage.input_tokens + usage.output_tokens
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return tokens, cache_creation, cache_read


def verify_image(client, model, image_url, ean, nome_produto, titulo):
    """
    Usa a visão do Claude para confirmar se a imagem em `image_url` realmente
    mostra o produto esperado, antes de aceitar essa URL. Retorna
    (valida: bool, tokens_usados: int). Essa chamada não usa o system prompt
    cacheado, então não contribui para cache_creation/cache_read. Em caso de
    erro/dúvida, considera inválida (mais seguro descartar do que manter uma
    imagem possivelmente errada).
    """
    if not image_url:
        return False, 0

    try:
        response = client.messages.create(
            model=model,
            max_tokens=200,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "url", "url": image_url}},
                        {
                            "type": "text",
                            "text": (
                                "Esta imagem deveria mostrar o produto "
                                f"'{titulo or nome_produto}' (EAN {ean}). Observe "
                                "a imagem com atenção e responda APENAS com um JSON, "
                                'sem markdown, no formato: {"valida": true ou false, '
                                '"motivo": "string curta"}. Considere inválida se a '
                                "imagem mostrar outro produto, um ícone genérico, um "
                                "banner/logo do site, ou não carregar/estiver quebrada."
                            ),
                        },
                    ],
                }
            ],
        )
        tokens, _cache_creation, _cache_read = response_usage(response)
        final_text = get_final_text(response)
        data = extract_json(final_text)
        return bool(data.get("valida")), tokens
    except (APIStatusError, APIConnectionError, json.JSONDecodeError, ValueError) as exc:
        print(
            f"  [aviso] não foi possível verificar a imagem de EAN {ean} "
            f"({image_url}): {exc}",
            file=sys.stderr,
        )
        return False, 0


def usage_vazio():
    return {"tokens": 0, "cache_creation": 0, "cache_read": 0}


FORMAT_CAMPOS_SYSTEM = """Você formata cadastro de e-commerce para produtos farmacêuticos a partir \
de fatos JÁ CONFIRMADOS. Você NÃO pesquisa nem inventa nenhum dado - usa exclusivamente o que foi \
fornecido na mensagem. Campo sem base nos fatos = null.

REGRAS DE TÍTULO (sem hífens, até ~70 caracteres - é o que o cliente digita/lê na busca), sem \
repetir tudo que já está em principios_ativos:
- Medicamento COM marca/nome comercial reconhecido: comece pela marca, depois princípio ativo, \
concentração, descritor curto quando existir (sabor, forma), público-alvo quando aplicável \
(Adulto/Infantil), terminando em quantidade + forma farmacêutica por extenso.
- Medicamento genérico (sem marca própria/nome comercial): [Princípio Ativo] [Concentração] \
[Fabricante] Genérico [Quantidade] [Forma Farmacêutica] - use o campo fabricante informado (nome \
curto e reconhecido de mercado, sem sufixo de razão social como "LTDA"/"S/A"/"FARMACÊUTICA"/ \
"INDÚSTRIA" quando esse sufixo não fizer parte do nome comercial usado no mercado - ex: \
"SANOFI MEDLEY FARMACÊUTICA LTDA." -> "Medley"; "UNIÃO QUÍMICA FARMACÊUTICA NACIONAL S/A" -> \
"União Química") como identificador no lugar da marca, sempre seguido da palavra "Genérico". NUNCA \
comece o título pela finalidade terapêutica (ex: "Analgésico", "Antiácido Efervescente") - isso \
atrapalha a busca.
- Não medicamento: [O que o produto é / Categoria] [Marca] [Linha] [Atributo/Especificação] \
[Volume/Quantidade]. "Categoria" é o tipo do objeto em português (Pomada, Fio Dental, \
Curativos, Absorvente, Shampoo, Enxaguante Bucal, Fórmula Infantil, Hastes Flexíveis, \
Fralda, Seringa), NÃO a finalidade terapêutica e NÃO o departamento da árvore. Ordem \
obrigatória: o que o produto É vem PRIMEIRO; marca vem DEPOIS. Pule o slot se não existir \
(sem linha = não invente linha). NUNCA comece pela marca - esse é o erro mais comum neste \
campo para não-medicamento. Errado: "Hipoglós Pomada Creme Assaduras 40g" / certo: "Pomada \
Creme Assaduras Hipoglós 40g". Errado: "Johnson's Baby Shampoo Regular 400ml" / certo: \
"Shampoo Johnson's Baby Regular 400ml". Errado: "Johnson's Reach Essencial Fio Dental \
Menta 100 Metros" / certo: "Fio Dental Johnson's Reach Essencial Menta 100 Metros". \
Errado: "Band Aid Curativos Transparente Respirável 40 Unidades" / certo: "Curativos Band \
Aid Transparente Respirável 40 Unidades". Errado: "Sempre Livre Absorvente Noturno com \
Abas Suave Leve 32 Unidades" / certo: "Absorvente Sempre Livre Noturno com Abas Suave \
Leve 32 Unidades". Errado: "Cotonete Johnson & Johnson Hastes Flexíveis 75 Unidades" / \
certo: "Hastes Flexíveis Cotonete Johnson & Johnson 75 Unidades". Errado: "Periogard \
Enxaguante Bucal Extra Mint Sem Álcool 250ml" / certo: "Enxaguante Bucal Periogard Extra \
Mint Sem Álcool 250ml". Errado: "Aptamil 2 Fórmula Infantil 400g" / certo: "Fórmula \
Infantil Aptamil 2 400g".
Composição no título: com nome comercial reconhecido e 3 ou mais princípios ativos, NUNCA liste a \
composição completa no título - use só marca + descritor + quantidade/forma. Com 1-2 princípios \
ativos, ou sem nome comercial (genérico/combinação sem marca própria), inclua nome + concentração \
de cada um. NUNCA escreva uma concentração sem o nome do princípio ativo do lado. O nome do \
princípio ativo no título (incluindo o sal - Cloridrato/Maleato/Besilato/Succinato/Bromidrato/ \
Fumarato/Mesilato/Oxalato etc.) tem que ser EXATAMENTE o texto que veio no campo \
principios_ativos informado - NUNCA troque por outro sal do mesmo fármaco só porque parece mais \
comum ou mais familiar (ex: não escreva "Cloridrato de Midazolam" se principios_ativos disser \
"Maleato de Midazolam" - são sais diferentes, trocar é erro factual, não estilo). Antes de \
responder, confira se o sal que você escreveu é literalmente o mesmo texto informado, não uma \
variação "mais comum".
Forma farmacêutica: sempre por extenso, nunca abrevie nem copie o código bruto da apresentação da \
ANVISA/CMED. Ex: "COM REV" -> "Comprimidos Revestidos"; "COM ORODISP" -> "Comprimidos \
Orodispersíveis"; "COM MAST" -> "Comprimidos Mastigáveis"; "SUS ORAL" -> "Suspensão Oral"; "XPE" -> \
"Xarope"; "SOL ORAL" -> "Solução Oral"; "POM" -> "Pomada"; "CREM"/"CRE" -> "Creme". Ignore por \
completo os códigos de embalagem que vêm junto na apresentação bruta (CT, BL, AL, PLAS, FR, ENV, \
VD, AMB etc.) - não fazem parte do título nem da forma farmacêutica, são só embalagem/frasco.
Exemplos: "Novalgina 1g Dipirona Adulto 20 Comprimidos"; "Vurtuoso Vortioxetina 20mg 60 \
Comprimidos"; "Paracetamol 750mg EMS Genérico 20 Comprimidos" (genérico); "Cloridrato de \
Amitriptilina 25mg Medley Genérico 30 Comprimidos Revestidos" (genérico, mantém o nome do sal - \
não simplifique "Cloridrato de X" para só "X", é como o mercado nomeia o genérico); "Fralda \
Pampers Confort Sec XXG 56 Unidades"; "Gastrol Pó Efervescente Sabor Laranja 6 Envelopes 5g" (nome \
comercial com 3 princípios ativos - composição só em principios_ativos); "Diosmina 450mg + \
Hesperidina 50mg 30 Comprimidos" (sem marca própria, inclui composição completa).

REGRAS DE DESCRIÇÃO: descricao_curta = até 250 caracteres, técnica e objetiva, sem termos \
comerciais/emojis - remova preço, parcelamento, frete, "compre", "aproveite", "menor preço", nome \
de farmácia, e frases feitas de SEO. Preserve o objetivo/finalidade real do produto como está no \
texto bruto - só remova o que for comercial/promocional/irrelevante, nunca invente uma finalidade \
nova, um benefício, um detalhe técnico ou qualquer outra informação que não esteja literalmente no \
texto bruto, só pra alongar a descrição. PROIBIDO copiar e colar frases do texto bruto quase \
literalmente (troca de 1-2 palavras não conta como reescrita) - reescreva de verdade, com suas \
próprias palavras e estrutura de frase, usando sinônimos; sinônimo é só substituir a palavra por \
outra de mesmo sentido, nunca mudar o fato, o grau ou a nuance médica (ex: "evita infecção" não \
pode virar "trata infecção" - são fatos médicos diferentes). 150-250 caracteres é o alvo QUANDO o \
texto bruto sustenta isso - se sobrar pouco depois de remover o comercial/SEO, descricao_curta \
CURTA (bem menor que 150) é o resultado certo, nunca complete com conteúdo inventado. Se o texto \
bruto estiver ausente ou for só propaganda (nada sobra depois de remover o comercial), \
descricao_curta = null - nunca invente conteúdo pra preencher.

CATEGORIZAÇÃO: raciocine de baixo pra cima - a árvore abaixo é SÓ o ramo do tipo_cadastro já \
confirmado. Primeiro decida a SUBCATEGORIA: é o nível mais específico, o que realmente diz pra que \
serve o produto - procure em TODA a árvore (não se prenda a um departamento que pareça óbvio de \
cara) qual subcategoria descreve melhor a finalidade terapêutica/uso do produto, mesmo que um nome \
parecido apareça em mais de um lugar da árvore (ex: "Dor e Febre" pode existir como categoria num \
departamento e como subcategoria em outro - escolha a mais específica pro produto, não a primeira \
que aparecer). Só depois de decidir a subcategoria, copie departamento e categoria EXATAMENTE da \
MESMA linha da árvore onde essa subcategoria está - nunca escolha departamento ou categoria antes \
ou separadamente da subcategoria; departamento e categoria têm que vir sempre da mesma linha, nunca \
de uma combinação montada à parte. [RAMO ...] não é campo de saída - NUNCA copie Medicamento/Não \
Medicamento em departamento/categoria/subcategoria. Nunca crie, combine ou adapte categorias fora \
da árvore. departamento="..." vai no campo departamento. categoria="..." vai no campo categoria. \
subcategoria é UM item da lista depois de "subcategorias:" (nunca a lista inteira, nunca vazio se a \
categoria foi encontrada). Marcas consagradas de dermocosmético (La Roche-Posay, Vichy, CeraVe, \
Eucerin etc.) vão em Dermocosméticos. Em KITs, classifique pelo 1º produto do título. Se nenhuma \
subcategoria da árvore descrever o produto, use null nos três campos.

ÁRVORE DE CATEGORIZAÇÃO OFICIAL:
{ARVORE_RAMO}

Responda APENAS com JSON válido, sem markdown: {"titulo": str|null, "descricao_curta": str|null, \
"departamento": str|null, "categoria": str|null, "subcategoria": str|null}."""


def _montar_format_system(arvore):
    return FORMAT_CAMPOS_SYSTEM.replace("{ARVORE_RAMO}", arvore or "")


# um bloco cacheado por ramo - tipo_cadastro já é conhecido nessas chamadas,
# então não manda o outro ramo. o Claude puro de busca também não leva a
# árvore: depois da busca, categorizar_apos_busca usa só o ramo do tipo.
FORMAT_SYSTEM_BLOCKS = {
    tipo: system_cached(_montar_format_system(arvore))
    for tipo, arvore in ARVORES_POR_RAMO.items()
}
FORMAT_SYSTEM_BLOCKS[None] = system_cached(
    _montar_format_system(ARVORE_CATEGORIZACAO or "")
)

# só categorização - sem regras de título/descrição. usada depois da busca
# agentic, quando titulo/descricao_curta já vieram da fonte e mandar o
# prompt completo de formatação só inflava token (o modelo ainda gerava
# titulo/descrição que o chamador descartava) e ainda passava o título no
# lugar da quantidade.
CATEGORIZACAO_SYSTEM = """Você classifica produtos farmacêuticos na árvore oficial abaixo. \
Você NÃO pesquisa nem inventa - usa só os fatos da mensagem. [RAMO ...] não é campo de saída.

CATEGORIZAÇÃO: raciocine de baixo pra cima. Primeiro decida a SUBCATEGORIA: é o nível mais \
específico, o que realmente diz pra que serve o produto - procure em TODA a árvore (não se prenda \
a um departamento que pareça óbvio de cara) qual subcategoria descreve melhor a finalidade \
terapêutica/uso do produto, mesmo que um nome parecido apareça em mais de um lugar da árvore (ex: \
"Dor e Febre" pode existir como categoria num departamento e como subcategoria em outro - escolha a \
mais específica pro produto, não a primeira que aparecer). Só depois de decidir a subcategoria, \
copie departamento e categoria EXATAMENTE da MESMA linha da árvore onde essa subcategoria está - \
nunca escolha departamento ou categoria antes ou separadamente da subcategoria; departamento e \
categoria têm que vir sempre da mesma linha, nunca de uma combinação montada à parte. Nunca crie, \
combine ou adapte categorias fora dela. departamento="..." vai no campo departamento. categoria="..." \
vai no campo categoria. subcategoria é UM item da lista depois de "subcategorias:" (nunca a lista \
inteira, nunca vazio se a categoria foi encontrada). Marcas consagradas de dermocosmético \
(La Roche-Posay, Vichy, CeraVe, Eucerin etc.) vão em Dermocosméticos. Em KITs, classifique \
pelo 1º produto do título. Se nenhuma subcategoria da árvore descrever o produto, use null nos três.

ÁRVORE DE CATEGORIZAÇÃO OFICIAL:
{ARVORE_RAMO}

Responda APENAS com JSON válido, sem markdown: {"departamento": str|null, "categoria": str|null, \
"subcategoria": str|null}."""

CATEGORIZACAO_SYSTEM_BLOCKS = {
    tipo: system_cached(CATEGORIZACAO_SYSTEM.replace("{ARVORE_RAMO}", arvore or ""))
    for tipo, arvore in ARVORES_POR_RAMO.items()
}


def _normalizar_para_comparacao(texto):
    return re.sub(r"\s+", " ", (texto or "")).strip().lower()


# a partir daqui a reescrita é considerada "copy-paste com sinônimo pontual"
# em vez de reescrita de verdade - limiar empírico (testado com casos reais
# de crawler onde só 1-2 palavras trocavam, mantendo a mesma estrutura)
LIMIAR_SIMILARIDADE_DESCRICAO = 0.55


def _parecido_demais(bruto, curta):
    """
    Mede se descricao_curta ficou parecida demais com o texto bruto - troca
    de sinônimo pontual mantendo a mesma estrutura de frase NÃO conta como
    reescrita (ex: "evitando cortes" -> "prevenindo cortes" é só sinônimo,
    a frase continua sendo a mesma). Usa razão de similaridade de sequência
    de caracteres (difflib), que pega isso melhor que contar palavras iguais
    isoladas. Retorna True se precisa reescrever de novo.
    """
    if not bruto or not curta:
        return False
    a = _normalizar_para_comparacao(bruto)
    b = _normalizar_para_comparacao(curta)
    return difflib.SequenceMatcher(None, a, b).ratio() >= LIMIAR_SIMILARIDADE_DESCRICAO


def _chamar_formatacao_campos(client, model, system, mensagem, max_tokens=400):
    """
    Chamada crua de formatar_campos_confirmados - isolada pra poder repetir
    com uma mensagem diferente (retry de reescrita) sem duplicar o
    try/except/parse. Retorna (data: dict|None, usage) - data é None se a
    chamada falhou (chamador decide o que fazer).
    """
    usage = usage_vazio()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": mensagem}],
            # temperature 0 (em vez do padrão do modelo) - essa chamada decide
            # titulo/descricao/categoria a partir de fatos já confirmados, é
            # uma tarefa de classificação/formatação, não geração criativa;
            # reduz a inconsistência entre rodadas (mesmo produto caindo em
            # categoria diferente da árvore) sem custo extra de token.
            temperature=0,
        )
        tokens, cache_creation, cache_read = response_usage(response)
        usage["tokens"] = tokens
        usage["cache_creation"] = cache_creation
        usage["cache_read"] = cache_read
        final_text = get_final_text(response)
        if not final_text:
            return None, usage
        return extract_json(final_text), usage
    except (APIStatusError, APIConnectionError, json.JSONDecodeError, ValueError) as exc:
        print(
            f"  [aviso] não foi possível formatar campos confirmados: {exc}",
            file=sys.stderr,
        )
        return None, usage


def formatar_campos_confirmados(
    client,
    model,
    tipo_cadastro,
    marca,
    principios_ativos,
    quantidade,
    nome_bruto,
    categoria_bruta=None,
    descricao_bruta=None,
    fabricante=None,
):
    """
    Uma única chamada de texto (sem busca) para título, categorização e
    descrição a partir de fatos JÁ CONFIRMADOS (CMED ou crawler). A árvore
    enviada é só o ramo do tipo_cadastro já conhecido. validar_categorizacao
    (em apply_safety_checks) continua zerando combinação fora da árvore. Se
    não houver texto bruto, força descricao_curta=null mesmo que o modelo
    tente preencher.

    fabricante: só usado pelo próprio modelo pra montar o título de
    medicamento genérico sem marca própria ("[Fabricante] Genérico" - ver
    FORMAT_CAMPOS_SYSTEM). Sem isso, o modelo não tinha como saber o nome do
    laboratório e o título genérico saía sem fabricante nem a palavra
    "Genérico" (ex: "Amitriptilina 25mg 30 Comprimidos" em vez de "Cloridrato \
    de Amitriptilina 25mg Medley Genérico 30 Comprimidos").

    Se descricao_curta saiu parecida demais com o texto bruto (troca de
    sinônimo pontual, não reescrita de verdade - ver _parecido_demais), só
    avisa pra revisão manual - não faz uma segunda chamada pra tentar
    reescrever de novo (economia de token vale mais aqui; o conteúdo já é
    factualmente correto, só não é uma reescrita ideal).

    Retorna (dict com titulo/descricao_curta/departamento/categoria/
    subcategoria, usage).
    """
    usage = usage_vazio()
    system = FORMAT_SYSTEM_BLOCKS.get(tipo_cadastro) or FORMAT_SYSTEM_BLOCKS.get(None)
    if not system:
        return {}, usage

    mensagem = (
        f"tipo_cadastro: {tipo_cadastro}\nmarca: {marca}\n"
        f"fabricante (use só se marca vier vazia e o produto for genérico - "
        f"nesse caso vai no título como \"[Fabricante] Genérico\", com o "
        f"nome curto de mercado, não a razão social completa): {fabricante}\n"
        f"principios_ativos: {principios_ativos}\n"
        f"quantidade/apresentação: {quantidade}\n"
        f"nome bruto (referência - pode ter finalidade terapêutica ou ordem "
        f"errada, reformate, não copie): {nome_bruto}\n"
        f"categoria bruta do site (referência, pode não bater com nossa "
        f"árvore - não copie): {categoria_bruta}\n"
    )
    if tipo_cadastro == "Não Medicamento":
        mensagem += (
            "\nTítulo de não-medicamento: [O que o produto é] [Marca] [Linha] "
            "[Atributo] [Volume/Qtd]. Comece pelo tipo do objeto (Pomada, Fio "
            "Dental, Curativos, Absorvente, Shampoo, Enxaguante Bucal, "
            "Fórmula Infantil, Hastes Flexíveis), DEPOIS a marca. NUNCA "
            "comece pela marca. Pule slot vazio. Não aplique regra de "
            "título de medicamento.\n"
        )
    if descricao_bruta:
        mensagem += (
            "\ntexto bruto do site (reescreva descricao_curta removendo "
            "linguagem comercial/SEO - com suas próprias palavras/sinônimos, "
            "NUNCA copiando frases quase literalmente; sem inventar nem "
            f"mudar a finalidade real):\n{descricao_bruta[:800]}\n"
        )
    else:
        mensagem += (
            "\ntexto bruto do site: (ausente - descricao_curta DEVE ser null, "
            "não invente)\n"
        )
    mensagem += (
        "\nGere titulo, descricao_curta e departamento/categoria/subcategoria."
    )

    data, usage = _chamar_formatacao_campos(client, model, system, mensagem)
    if data is None:
        return {}, usage

    if not descricao_bruta:
        data["descricao_curta"] = None
    elif _parecido_demais(descricao_bruta, data.get("descricao_curta")):
        # sem segunda chamada de propósito (economia de token) - só avisa
        # pra revisão manual; o conteúdo continua factualmente correto
        print(
            "  [aviso] descricao_curta parecida demais com o texto bruto "
            f"(troca de sinônimo pontual, não reescrita) - revisar "
            f"manualmente: {data.get('descricao_curta')!r}"
        )

    return {
        "titulo": data.get("titulo"),
        "descricao_curta": data.get("descricao_curta"),
        "departamento": data.get("departamento"),
        "categoria": data.get("categoria"),
        "subcategoria": data.get("subcategoria"),
    }, usage


def categorizar_apos_busca(
    client, model, data, nome_produto=None, categoria_bruta=None
):
    """
    Segunda chamada leve (sem busca): preenche departamento/categoria/
    subcategoria só com o ramo do tipo_cadastro já definido na busca
    agentic. Não reescreve titulo/descricao_curta - esses vieram da fonte
    na chamada com web_search/web_fetch.

    Descarta qualquer categoria que o modelo tenha colado na busca (o
    prompt pede para não devolver esses campos, mas não se confia nisso).
    Se tipo_cadastro não bate com um ramo da árvore, zera os três campos
    em vez de mandar a árvore completa. Sem titulo, não gasta a chamada.
    Usa o prompt curto de CATEGORIZACAO_SYSTEM (sem regras de título/
    descrição) - formatar_campos_confirmados gerava esses campos à toa.

    Retorna (data, usage).
    """
    usage = usage_vazio()
    data["departamento"] = None
    data["categoria"] = None
    data["subcategoria"] = None
    data["origem_categorizacao"] = "ia"

    if not data.get("titulo"):
        return data, usage

    tipo = (data.get("tipo_cadastro") or "").strip()
    if tipo not in ARVORES_POR_RAMO:
        return data, usage

    system = CATEGORIZACAO_SYSTEM_BLOCKS.get(tipo)
    if not system:
        return data, usage

    mensagem = (
        f"tipo_cadastro: {tipo}\n"
        f"titulo: {data.get('titulo') or nome_produto}\n"
        f"marca: {data.get('marca')}\n"
        f"principios_ativos: {data.get('principios_ativos')}\n"
        f"categoria bruta do site (referência, pode não bater com nossa "
        f"árvore - não copie): {categoria_bruta}\n\n"
        "Gere só departamento, categoria e subcategoria."
    )
    formatados, usage = _chamar_formatacao_campos(
        client, model, system, mensagem, max_tokens=200
    )
    if formatados:
        data["departamento"] = formatados.get("departamento")
        data["categoria"] = formatados.get("categoria")
        data["subcategoria"] = formatados.get("subcategoria")
    return data, usage


_SUFIXOS_HIDRATACAO = (
    " MONOIDRATADA",
    " MONOIDRATADO",
    " DI-HIDRATADA",
    " DI-HIDRATADO",
    " DIHIDRATADA",
    " DIHIDRATADO",
    " HEMI-HIDRATADO",
    " HEMIHIDRATADO",
    " TRI-HIDRATADO",
    " ANIDRO",
    " ANIDRA",
)
_PALAVRAS_PEQUENAS = {"DE", "DA", "DO", "DAS", "DOS", "E"}
_CONC_CMED_RE = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(MCG|MG|G|ML|L|UI|MUI|%)(?:\s*/\s*(ML|G|L))?\s*(?:\+\s*)?",
    re.IGNORECASE,
)


def _split_substancias_cmed(substancia):
    return [s.strip() for s in (substancia or "").split(";") if s.strip()]


def normalizar_nome_substancia_cmed(bruto):
    """
    Normaliza um nome de substância/produto da CMED pra comparação:
    maiúsculas, espaços colapsados, sem sufixo de estado de hidratação
    (di-hidratado, anidro etc. - ver _SUFIXOS_HIDRATACAO). Público porque
    também é usado por enrich_com_crawler.mapear_cmed_para_schema pra decidir
    se produto e substancia são "o mesmo nome" (genérico sem marca própria)
    mesmo quando só um dos dois traz o estado de hidratação - ex: produto
    "CLORIDRATO DE ONDANSETRONA" e substancia "CLORIDRATO DE ONDANSETRONA
    DI-HIDRATADO" são o mesmo genérico; sem essa normalização, a comparação
    ingênua os tratava como nomes diferentes e preenchia "marca" com o nome
    do sal por engano.
    """
    texto = re.sub(r"\s+", " ", bruto or "").strip().upper()
    for sufixo in _SUFIXOS_HIDRATACAO:
        if texto.endswith(sufixo):
            return texto[: -len(sufixo)].strip()
    return texto


def _nome_principio_cmed(bruto):
    # mantém o nome do sal (ex: "Cloridrato de Amitriptilina") em vez de
    # simplificar pro princípio ativo puro - genérico sem marca no Brasil é
    # comercializado e listado na bula com esse nome completo; removê-lo
    # deixava principios_ativos ("Amitriptilina 25mg") divergente do nome
    # bruto do produto usado no título ("Cloridrato De Amitriptilina"), o
    # que confundia o modelo na formatação e gerava título fora de ordem
    # (ex: "Amitriptilina 25mg Cloridrato 30 Comprimidos").
    texto = normalizar_nome_substancia_cmed(bruto)
    partes = []
    for i, palavra in enumerate(texto.split()):
        if i > 0 and palavra in _PALAVRAS_PEQUENAS:
            partes.append(palavra.lower())
        else:
            partes.append(palavra.capitalize())
    return " ".join(partes)


def _concentracoes_apresentacao_cmed(apresentacao):
    """Concentrações só do prefixo da apresentação CMED (ex: '15 MG COM REV...'
    ou '185 MG + 235 MG + 178 MG PO EFERV...'). Para no primeiro token que
    não é concentração - nunca pega número de quantidade ('X 30')."""
    resto = apresentacao or ""
    achadas = []
    while True:
        match = _CONC_CMED_RE.match(resto)
        if not match:
            break
        valor, unidade, denominador = match.group(1), match.group(2).lower(), match.group(3)
        conc = f"{valor}{unidade}"
        if denominador:
            conc += f"/{denominador.lower()}"
        achadas.append(conc)
        resto = resto[match.end() :]
    return achadas


def parsear_composicao_cmed(substancia, apresentacao):
    """
    Tenta montar principios_ativos sem LLM. Retorna a string se o pareamento
    for seguro; None se a apresentação for ambígua (aí o chamador cai no
    modelo). Sem concentração na apresentação devolve só os nomes - nunca
    inventa mg.
    """
    nomes = [_nome_principio_cmed(s) for s in _split_substancias_cmed(substancia)]
    if not nomes:
        return None
    concs = _concentracoes_apresentacao_cmed(apresentacao)
    if not concs:
        return ", ".join(nomes)
    if len(concs) == len(nomes):
        return ", ".join(f"{nome} {conc}" for nome, conc in zip(nomes, concs))
    return None


CMED_COMPOSICAO_SYSTEM = """Você formata a composição de medicamentos a partir de dados OFICIAIS da \
tabela CMED (ANVISA) - substância e apresentação já confirmadas, você não pesquisa nem inventa nada, \
só reformata os fatos fornecidos na mensagem.

REGRAS: devolva cada princípio ativo seguido da sua concentração (ex: "Vortioxetina 15mg"), na mesma \
ordem em que aparecem na substância (separados por ";" quando há mais de um), separados por vírgula \
no resultado. A apresentação traz a(s) concentração(ões) no início do texto, na mesma ordem das \
substâncias (quando há mais de uma, os valores vêm ligados por "+"). Use seu conhecimento \
farmacêutico para simplificar nome de sal para o nome comum do princípio ativo quando for prática \
padrão de mercado (ex: "Bromidrato de Vortioxetina" -> "Vortioxetina"), mas NUNCA altere a \
concentração nem troque por outra substância. Se não conseguir parear com segurança concentração e \
substância (ex: apresentação sem valores numéricos, solução com muitos componentes tipo nutrição \
parenteral), devolva só os nomes das substâncias em Title Case separados por vírgula, sem \
concentração - nunca invente um valor que não veio na apresentação.

Responda APENAS com JSON válido, sem markdown: {"principios_ativos": str}."""

CMED_COMPOSICAO_SYSTEM_BLOCK = system_cached(CMED_COMPOSICAO_SYSTEM)


def formatar_composicao_cmed(client, model, substancia, apresentacao):
    """
    Combina substância + apresentação da CMED (ANVISA) num principios_ativos
    no nosso formato ("nome concentração, nome concentração"). O caso comum
    (N substâncias / N concentrações no prefixo da apresentação, ou
    apresentação sem concentração) é resolvido em código, sem token. Só cai
    no modelo quando o pareamento é ambíguo - e aí o modelo ainda está
    proibido de inventar mg. Retorna (principios_ativos: str, usage).
    """
    usage = usage_vazio()
    parsed = parsear_composicao_cmed(substancia, apresentacao)
    if parsed is not None:
        return parsed, usage

    mensagem = (
        f"substância: {substancia}\napresentação: {apresentacao}\n\n"
        "Formate a composição."
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=200,
            temperature=0,
            system=CMED_COMPOSICAO_SYSTEM_BLOCK,
            messages=[{"role": "user", "content": mensagem}],
        )
        tokens, cache_creation, cache_read = response_usage(response)
        usage["tokens"] = tokens
        usage["cache_creation"] = cache_creation
        usage["cache_read"] = cache_read
        final_text = get_final_text(response)
        if not final_text:
            return substancia, usage
        data = extract_json(final_text)
        return data.get("principios_ativos") or substancia, usage
    except (APIStatusError, APIConnectionError, json.JSONDecodeError, ValueError) as exc:
        print(
            f"  [aviso] não foi possível formatar composição CMED: {exc}",
            file=sys.stderr,
        )
        return substancia, usage


def formatar_composicao_abcfarma(client, model, principio_ativo, apresentacao):
    """
    Como formatar_composicao_cmed, mas pra base ABCFarma: o campo
    PRINCÍPIO ATIVO separa substâncias por "+" (a CMED usa ";") - normaliza
    pro separador esperado e reusa o mesmo parser/prompt. A apresentação da
    ABCFarma é mais irregular que a da CMED (ex: "cx 30 comp" sem
    concentração nenhuma, ou "2+2+0,25mg cx 11+10 comp rev" com várias
    concentrações coladas antes de uma única unidade) - quando o parser
    determinístico não conseguir parear com segurança, cai pro mesmo
    fallback via modelo, que já é proibido de inventar concentração.
    """
    substancia_normalizada = (principio_ativo or "").replace("+", ";")
    return formatar_composicao_cmed(client, model, substancia_normalizada, apresentacao)


def formatar_composicao_iqvia(client, model, molecula, descricao_longa):
    """
    Como formatar_composicao_cmed, mas pra base IQVIA: o campo MOLECULA
    separa substâncias por "|" (a CMED usa ";") - normaliza pro separador
    esperado. IQVIA não tem uma "apresentação" separada do nome (tudo vem
    junto em DESCRICAO_LONGA, ex: "AC DEXAMETASONA MG CREME 1.0 MG 10.0 G
    X 1.0") - passa o campo inteiro como apresentação; o parser/fallback já
    lida com apresentação irregular (mesmo caso da ABCFarma) e o modelo
    continua proibido de inventar concentração.
    """
    substancia_normalizada = (molecula or "").replace("|", ";")
    return formatar_composicao_cmed(client, model, substancia_normalizada, descricao_longa)


TARJA_VERIFICATION_SYSTEM = """Você é um farmacêutico especialista em regulação de medicamentos no \
Brasil. Sua única tarefa é confirmar 2 campos regulatórios de UM medicamento específico, usando \
fontes oficiais (bulário da ANVISA, bula do fabricante, ou farmácia online confiável) - NUNCA \
infira pela classe terapêutica, princípio ativo ou "senso comum" (ex: nunca marque Tarja Vermelha/ \
Preta só porque outro produto da mesma classe é controlado).

PROCESSO: web_search (máx. 2) e web_fetch (máx. 2) para achar a bula oficial ou o registro no \
bulário da ANVISA dessa apresentação específica (mesma marca, concentração e forma farmacêutica - \
nunca um genérico/similar diferente).

Responda APENAS com JSON válido, sem markdown: {"tarja": "Sem Tarja"|"Tarja Vermelha"|"Tarja \
Preta"|null, "registro_ms": str|null, "confirmado": true|false}. confirmado=true só se você achou \
e leu (via web_fetch) uma fonte oficial confirmando esses dados para essa apresentação específica. \
Se não achar fonte confiável específica o suficiente, responda confirmado=false e os outros campos \
null - nunca invente para preencher."""

TARJA_VERIFICATION_SYSTEM_BLOCK = system_cached(TARJA_VERIFICATION_SYSTEM)


def verify_tarja_registro(client, model, ean, titulo, marca, principios_ativos, max_retries=2):
    """
    Segunda chamada dedicada, com busca própria, só para confirmar tarja e
    registro_ms de medicamento numa fonte oficial. O modelo já errou tarja
    mesmo citando uma fonte na resposta principal (viu isso em produtos reais
    - Estomanol, Tenoretic, Nasonex), o que sugere que às vezes ele usa só o
    snippet do web_search sem de fato ler a página via web_fetch. Uma segunda
    chamada focada, sem o restante do enriquecimento pra distrair, é mais
    confiável do que aceitar a resposta da primeira chamada. Retorna
    (resultado: dict|None, usage: dict). resultado tem "confirmado": bool -
    só usar tarja/registro_ms se confirmado for True.
    """
    mensagem = (
        f"EAN: {ean}\nMedicamento: {titulo}\nMarca: {marca}\n"
        f"Princípios ativos: {principios_ativos}\n\n"
        "Confirme tarja e registro_ms dessa apresentação específica numa fonte oficial."
    )
    tools = _ferramentas_tarja(max_uses=2)
    usage = {"tokens": 0, "cache_creation": 0, "cache_read": 0}
    messages = [{"role": "user", "content": mensagem}]

    for attempt in range(1, max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=800,
                temperature=0,
                system=TARJA_VERIFICATION_SYSTEM_BLOCK,
                tools=tools,
                messages=messages,
            )
            tokens, cache_creation, cache_read = response_usage(response)
            usage["tokens"] += tokens
            usage["cache_creation"] += cache_creation
            usage["cache_read"] += cache_read

            resume_count = 0
            while response.stop_reason == "pause_turn" and resume_count < 3:
                messages.append({"role": "assistant", "content": response.content})
                response = client.messages.create(
                    model=model,
                    max_tokens=800,
                    temperature=0,
                    system=TARJA_VERIFICATION_SYSTEM_BLOCK,
                    tools=tools,
                    messages=messages,
                )
                t, c, r = response_usage(response)
                usage["tokens"] += t
                usage["cache_creation"] += c
                usage["cache_read"] += r
                resume_count += 1

            if response.stop_reason == "refusal":
                return None, usage

            final_text = get_final_text(response)
            if not final_text:
                return None, usage

            resultado = extract_json(final_text)
            return resultado, usage
        except (APIStatusError, APIConnectionError, json.JSONDecodeError, ValueError) as exc:
            if attempt == max_retries:
                print(
                    f"  [aviso] não foi possível verificar tarja/registro_ms de "
                    f"EAN {ean}: {exc}",
                    file=sys.stderr,
                )
                return None, usage
            time.sleep(min(2 ** attempt, 10))

    return None, usage


SYSTEM_BLOCK = [
    {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
]

# teto só no enriquecimento geral (título/descrição/imagem). a verificação
# dedicada de tarja NÃO usa esse limite - truncar bula oficial já fez o
# modelo errar tarja olhando só snippet.
WEB_FETCH_MAX_CONTENT_TOKENS = 4000

# marketplace/rede social não confirmam apresentação farmacêutica e ainda
# incham o contexto do web_fetch. bloqueados na busca geral; a verificação
# de tarja é ainda mais restrita (só fontes oficiais, ver abaixo).
DOMINIOS_BUSCA_BLOQUEADOS = [
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "youtube.com",
    "pinterest.com",
    "mercadolivre.com.br",
    "mercadolivre.com",
    "shopee.com.br",
    "amazon.com.br",
    "amazon.com",
]
DOMINIOS_TARJA_PERMITIDOS = [
    "anvisa.gov.br",
    "sara.com.br",
    "consultaremedios.com.br",
    "bulas.med.br",
]
LOCALIZACAO_BUSCA_BR = {
    "type": "approximate",
    "country": "BR",
    "timezone": "America/Sao_Paulo",
}


def _ferramentas_busca(max_uses=3, max_content_tokens=WEB_FETCH_MAX_CONTENT_TOKENS):
    """web_search/web_fetch da busca geral: bloqueia lixo, localiza no BR."""
    return [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": max_uses,
            "blocked_domains": DOMINIOS_BUSCA_BLOQUEADOS,
            "user_location": LOCALIZACAO_BUSCA_BR,
        },
        {
            "type": "web_fetch_20250910",
            "name": "web_fetch",
            "max_uses": max_uses,
            "max_content_tokens": max_content_tokens,
            "blocked_domains": DOMINIOS_BUSCA_BLOQUEADOS,
        },
    ]


def _ferramentas_tarja(max_uses=2):
    """Tarja/registro_ms só em fonte oficial - evita farmácia/marketplace
    e reduz fetch inútil (cada página entra no contexto da próxima volta)."""
    return [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": max_uses,
            "allowed_domains": DOMINIOS_TARJA_PERMITIDOS,
            "user_location": LOCALIZACAO_BUSCA_BR,
        },
        {
            "type": "web_fetch_20250910",
            "name": "web_fetch",
            "max_uses": max_uses,
            "allowed_domains": DOMINIOS_TARJA_PERMITIDOS,
        },
    ]

ALLOWED_TARJA = {"Sem Tarja", "Tarja Vermelha", "Tarja Preta", "Não aplicável"}

# prefixo de origem_enriquecimento usado pela camada 0 (cmed.py, ver
# enrich_com_crawler.py) - mesmo valor de cmed.ORIGEM_ANVISA_CMED, duplicado
# aqui só como uma string pra não criar dependência deste módulo em cmed.py
ORIGEM_ANVISA_CMED = "anvisa_cmed"
# idem, mas pra segunda fonte oficial (abcfarma.py) - mesmo valor de
# abcfarma.ORIGEM_ABCFARMA. Ao contrário da CMED, a ABCFarma não traz tarja,
# então essa origem só cobre a exceção de "fonte sem URL" pros campos que ela
# de fato confirma (registro_ms/generico/principios_ativos/departamento/
# categoria) - tarja continua null e vai pra fila de validação humana (ver
# marcar_validacao_humana).
ORIGEM_ABCFARMA = "abcfarma"
# idem, mas pra terceira fonte de referência (iqvia.py) - mesmo valor de
# iqvia.ORIGEM_IQVIA. Cobre não-medicamento também (ao contrário de CMED/
# ABCFarma) e, pra medicamento, já diz "precisa receita" (RX) vs "isento de
# prescrição" (MIP) - MIP é confirmado direto (ver
# tarja_confirmada_iqvia_mip); RX não distingue Vermelha de Preta, então
# segue a mesma régua de tarja não confirmada por fonte oficial da ABCFarma.
ORIGEM_IQVIA = "iqvia"

FRASE_VENDA_PRESCRICAO = "VENDA SOB PRESCRIÇÃO MÉDICA."
# usada no lugar de FRASE_VENDA_PRESCRICAO quando precisa_retencao_receita
# for "Sim" - texto oficial da Portaria 344 (ex: adendo 2 da Lista A1)
FRASE_VENDA_PRESCRICAO_RETENCAO = (
    "VENDA SOB PRESCRIÇÃO MÉDICA - SÓ PODE SER VENDIDO COM RETENÇÃO DA "
    "RECEITA."
)
FRASE_MEDICAMENTO_GERAL = (
    "ESTE É UM MEDICAMENTO. SEU USO PODE TRAZER RISCOS. PROCURE O MÉDICO E O "
    "FARMACÊUTICO. LEIA A BULA. SE PERSISTIREM OS SINTOMAS, O MÉDICO DEVERÁ SER "
    "CONSULTADO."
)
FRASE_GENERICO = "Medicamento Genérico Lei nº 9.787, de 1999."
FRASE_SUPLEMENTO = "Isento de registro conforme RDC nº 240/18."
FRASE_LEITE = (
    "O MINISTÉRIO DA SAÚDE INFORMA: O ALEITAMENTO MATERNO EVITA INFECÇÕES E "
    "ALERGIAS E É RECOMENDADO ATÉ OS DOIS ANOS DE IDADE OU MAIS."
)

# fórmula infantil não tem departamento próprio na árvore - detecta pelo
# texto do produto (o modelo não gera mais frase_obrigatoria)
FORMULA_INFANTIL_RE = re.compile(
    r"f[oó]rmula(?:s)? infantil|"
    r"leite(?:s)? (?:infantil|de in[ií]cio|de seguimento|de crescimento)|"
    r"aleitamento materno evita",
    re.IGNORECASE,
)

# "185mg", "2,31 g", "0,6mcg" etc - usado para flagar título que provavelmente
# colou a composição completa por engano (ver checagem de titulo abaixo)
CONCENTRACAO_RE = re.compile(r"\d+[.,]?\d*\s*(?:mg|mcg|g|ml|l|ui)\b", re.IGNORECASE)
TITULO_MAX_RECOMENDADO = 90

# sais de fármaco reconhecidos - usado só pra comparar título x
# principios_ativos (ver _corrigir_sal_titulo), não pra decidir se é o
# princípio ativo em si (por isso inclui SULFATO/FOSFATO aqui, ao contrário
# de _PREFIXOS_SAL em _nome_principio_cmed)
_SAIS_CONHECIDOS = (
    "CLORIDRATO", "BROMIDRATO", "BROMIDRETO", "MALEATO", "BESILATO",
    "SUCCINATO", "HEMIFUMARATO", "FUMARATO", "MESILATO", "OXALATO",
    "HEMITARTARATO", "TARTARATO", "CITRATO", "FOSFATO", "SULFATO",
)


def _sal_no_texto(texto):
    texto_upper = (texto or "").upper()
    for sal in _SAIS_CONHECIDOS:
        if re.search(rf"\b{sal}\b", texto_upper):
            return sal
    return None


def _corrigir_sal_titulo(data, ean):
    """
    Corrige em código o título que troca o sal do princípio ativo por outro
    mais "familiar" pro modelo (ex: escreve "Cloridrato de Midazolam" quando
    principios_ativos - calculado deterministicamente a partir da fonte
    oficial, ver parsear_composicao_cmed - diz "Maleato de Midazolam"). Já
    tentamos resolver só com instrução no prompt (nunca trocar o sal) e o
    modelo ainda errou 4 de 4 vezes num teste, inclusive escrevendo uma nota
    dizendo que sabia da regra mas achava que o sal "real" era outro - exatas
    características do problema que o resto deste arquivo já resolve em
    código (tarja, categorização) em vez de confiar só no prompt.

    Só corrige de forma automática o caso simples (principios_ativos com 1
    princípio ativo só, sem "+"/",") - com múltiplos princípios ativos cada
    um pode ter seu próprio sal (ex: "Cloridrato de Nafazolina + Sulfato de
    Zinco"), e trocar automaticamente arriscaria acertar o sal errado no
    princípio ativo errado. Nesse caso só avisa pra revisão manual.
    """
    titulo = data.get("titulo") or ""
    principios = data.get("principios_ativos") or ""
    sal_titulo = _sal_no_texto(titulo)
    if not sal_titulo:
        return

    if "+" in principios or "," in principios:
        print(
            f"  [aviso] título de EAN {ean} tem sal ({sal_titulo!r}) e "
            f"principios_ativos tem múltiplos componentes - confira "
            f"manualmente se bate: titulo={titulo!r} | "
            f"principios_ativos={principios!r}"
        )
        return

    sal_principio = _sal_no_texto(principios)
    if sal_principio and sal_principio != sal_titulo:
        # usa a grafia original de principios_ativos (ex: "Maleato", não o
        # "MALEATO" canônico de _SAIS_CONHECIDOS) pra manter a capitalização
        # consistente com o resto do título
        match_original = re.search(rf"\b{sal_principio}\b", principios, re.IGNORECASE)
        substituto = match_original.group(0) if match_original else sal_principio.capitalize()
        titulo_corrigido = re.sub(
            rf"\b{sal_titulo}\b", substituto, titulo, flags=re.IGNORECASE
        )
        print(
            f"  [aviso] título de EAN {ean} trocou o sal do princípio ativo "
            f"(tinha {sal_titulo!r}, principios_ativos confirma "
            f"{sal_principio!r}) - corrigido: {titulo!r} -> {titulo_corrigido!r}"
        )
        data["titulo"] = titulo_corrigido

# indica que o texto que o próprio modelo escreveu contradiz tipo_cadastro =
# "Medicamento" (ex: descricao_curta dizendo "suplemento alimentar")
SUPLEMENTO_CONTRADICAO_RE = re.compile(
    r"suplemento alimentar|isento de registro|não é (?:um )?medicamento",
    re.IGNORECASE,
)

# campos que só deveriam vir preenchidos com base numa fonte confirmada
CAMPOS_DEPENDENTES_DE_FONTE = (
    "registro_ms",
    "principios_ativos",
    "departamento",
    "categoria",
    "tarja",
    "preco_pesquisado",
)

# dimensão mínima (em pixels) para aceitar imagem_url - abaixo disso é
# provavelmente ícone/logo/thumbnail, não foto de produto de verdade
IMAGEM_LARGURA_MINIMA = 300
IMAGEM_ALTURA_MINIMA = 300

# teto de download - image_url vem da resposta do modelo, não é uma URL em
# que confiamos cegamente; sem isso, uma URL apontando pra um arquivo enorme
# (ou infinito) ia inteira pra memória antes de qualquer checagem
IMAGEM_MAX_BYTES = 15 * 1024 * 1024  # 15MB
ESQUEMAS_URL_PERMITIDOS = {"http", "https"}
IMAGEM_REDIRECTS_MAX = 3
HOSTS_IMAGEM_BLOQUEADOS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _host_imagem_publico(hostname):
    """Recusa loopback/rede privada - image_url vem do modelo e o download
    não deve alcançar serviços internos (SSRF)."""
    if not hostname or hostname.lower().strip(".") in HOSTS_IMAGEM_BLOQUEADOS:
        return False
    host = hostname.lower().rstrip(".")
    if host.endswith(".local") or host.endswith(".internal") or host.endswith(".localhost"):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def check_imagem_tamanho_minimo(
    image_url,
    largura_minima=IMAGEM_LARGURA_MINIMA,
    altura_minima=IMAGEM_ALTURA_MINIMA,
    max_bytes=IMAGEM_MAX_BYTES,
    timeout=10,
):
    """
    Baixa a imagem e verifica se as dimensões batem com o mínimo exigido.
    Não consome tokens de LLM - é só download + leitura local do cabeçalho da
    imagem. image_url vem da resposta do modelo, não de uma fonte confiável,
    então valida o esquema e o host (nunca file://, nunca loopback/rede
    privada) e baixa em streaming com teto de bytes, sem seguir redirect
    cego. Retorna (ok: bool, motivo: str) para logging.
    """
    url_atual = image_url
    try:
        for _ in range(IMAGEM_REDIRECTS_MAX + 1):
            parsed = urlparse(url_atual)
            esquema = parsed.scheme.lower()
            if esquema not in ESQUEMAS_URL_PERMITIDOS:
                return False, f"esquema de URL não permitido ({esquema!r})"
            if not _host_imagem_publico(parsed.hostname):
                return False, f"host de imagem não permitido ({parsed.hostname!r})"

            with httpx.stream(
                "GET", url_atual, timeout=timeout, follow_redirects=False
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        return False, "redirect sem Location"
                    url_atual = str(response.url.join(location))
                    continue
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    return False, f"imagem maior que o teto de {max_bytes} bytes"

                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > max_bytes:
                        return False, f"imagem maior que o teto de {max_bytes} bytes"

            with Image.open(io.BytesIO(chunks)) as img:
                largura, altura = img.size
            if largura < largura_minima or altura < altura_minima:
                return False, (
                    f"{largura}x{altura}px (mínimo exigido: "
                    f"{largura_minima}x{altura_minima}px)"
                )
            return True, f"{largura}x{altura}px"
        return False, f"mais de {IMAGEM_REDIRECTS_MAX} redirects"
    except (httpx.HTTPError, UnidentifiedImageError, OSError, ValueError) as exc:
        return False, f"não foi possível validar a imagem ({exc})"


def validar_categorizacao(data):
    """
    Confere se (departamento, categoria, subcategoria) devolvidos pelo modelo
    realmente existem na árvore oficial (tabela `categorias` no Postgres),
    em vez de confiar que o modelo seguiu a instrução do prompt. Sem a árvore
    carregada (COMBINACOES_CATEGORIZACAO_VALIDAS vazio), não valida - não dá
    para diferenciar "categoria inventada" de "taxonomia indisponível".
    Retorna (ok: bool, motivo: str|None).
    """
    departamento = data.get("departamento")
    categoria = data.get("categoria")
    subcategoria = data.get("subcategoria")

    if not departamento and not categoria and not subcategoria:
        return True, None  # produto legitimamente fora da árvore

    if not COMBINACOES_CATEGORIZACAO_VALIDAS:
        return True, None

    tipo = data.get("tipo_cadastro")
    combinacao = (tipo, departamento, categoria, subcategoria)
    if combinacao in COMBINACOES_CATEGORIZACAO_VALIDAS:
        return True, None
    return False, (
        f"tipo_cadastro={tipo!r} departamento={departamento!r} "
        f"categoria={categoria!r} subcategoria={subcategoria!r} não existe "
        "na árvore oficial"
    )


def compor_frase_obrigatoria(data, tarja, is_medicamento):
    """
    Recompõe frase_obrigatoria de forma determinística a partir dos campos já
    validados, em vez de confiar na composição livre do modelo (que já colou
    "venda sob prescrição" num produto sem tarja e já truncou o texto
    canônico de medicamento em geral). Idempotente - pode ser chamada de novo
    depois que a tarja for atualizada por verify_tarja_registro, sem duplicar
    nem perder a frase de fórmula infantil.

    precisa_retencao_receita (já em data, calculado antes desta função) troca
    a frase de prescrição pela variante com retenção quando for "Sim" - Tarja
    Preta sempre entra aqui (retenção é sempre exigida), e Tarja Vermelha
    entra quando a substância bate na tabela substancias_controladas (ver
    enrich_com_crawler.mapear_cmed_para_schema).
    """
    partes = []
    if data.get("precisa_retencao_receita") == "Sim":
        partes.append(FRASE_VENDA_PRESCRICAO_RETENCAO)
    elif tarja in ("Tarja Vermelha", "Tarja Preta"):
        partes.append(FRASE_VENDA_PRESCRICAO)
    if is_medicamento:
        partes.append(FRASE_MEDICAMENTO_GERAL)
    if is_medicamento and data.get("generico") == "Sim":
        partes.append(FRASE_GENERICO)
    if data.get("departamento") == "Suplementos Alimentares" and not (
        data.get("categoria") == "Sistema Digestivo"
        and data.get("subcategoria") in ("Enzimas", "Probióticos")
    ):
        partes.append(FRASE_SUPLEMENTO)

    texto_produto = " ".join(
        str(data.get(campo) or "")
        for campo in (
            "titulo",
            "descricao_curta",
            "categoria",
            "subcategoria",
            "departamento",
            "frase_obrigatoria",
        )
    )
    if FORMULA_INFANTIL_RE.search(texto_produto):
        partes.append(FRASE_LEITE)

    return " ".join(partes) if partes else None


def apply_safety_checks(data, ean):
    """
    Travas de segurança pós-hoc para regras com implicação legal/regulatória -
    nunca confiar só no prompt, o modelo já errou nelas antes (ex: marcou
    "Tarja Preta" num antiácido de venda livre, por inferência da classe
    terapêutica, e truncou/alterou a frase_obrigatoria). Corrige/zera o que dá
    para validar deterministicamente a partir dos próprios campos
    estruturados e loga cada ajuste feito, para dar visibilidade do que o
    modelo errou.
    """
    is_medicamento = data.get("tipo_cadastro") == "Medicamento"

    # não-medicamento nunca tem tarja nem retenção de receita - carimba os
    # dois campos aqui em vez de deixar null/ambíguo (a fonte não confirma
    # isso porque a pergunta não se aplica, não porque falhou em confirmar).
    if not is_medicamento:
        data["tarja"] = "Não aplicável"
        data["precisa_retencao_receita"] = "Não"

    # tarja fora do vocabulário fechado = alucinação, zera.
    tarja = data.get("tarja")
    if tarja is not None and tarja not in ALLOWED_TARJA:
        print(f"  [aviso] tarja inválida para EAN {ean} ({tarja!r}) - zerada.")
        data["tarja"] = tarja = None

    # fonte é uma tabela oficial (CMED, ABCFarma ou IQVIA), não uma página
    # web - não tem pagina_produto_url por natureza, mas isso não significa
    # fonte não confirmada, então a checagem de "campos dependentes de
    # fonte" mais abaixo não se aplica a nenhuma das três. A exceção de
    # tarja logo a seguir é mais restrita: só a CMED confirma tarja de fato
    # sempre, e a IQVIA só quando é MIP (Medicamento Isento de Prescrição -
    # categoria regulatória oficial, não inferência de site - ver
    # tarja_confirmada_iqvia_mip) - um valor de tarja vindo de origem
    # abcfarma, ou de origem iqvia sem ser essa exceção (ex: RX, que só diz
    # "precisa receita" sem distinguir Vermelha de Preta), não é uma fonte
    # confirmada e deve continuar sendo zerado por essa regra.
    origem_enriquecimento_str = str(data.get("origem_enriquecimento") or "")
    origem_e_cmed = origem_enriquecimento_str.startswith(ORIGEM_ANVISA_CMED)
    tarja_iqvia_mip_confirmada = data.get("tarja_confirmada_iqvia_mip") == "Sim"
    origem_confiavel_sem_url = (
        origem_e_cmed
        or origem_enriquecimento_str.startswith(ORIGEM_ABCFARMA)
        or origem_enriquecimento_str.startswith(ORIGEM_IQVIA)
    )

    # tarja sem fonte confirmada: já vimos o modelo alucinar tarja mais de uma
    # vez exatamente quando não tem pagina_produto_url (ex: "Sem Tarja" num
    # remédio que é Tarja Vermelha) - tarja tem risco legal maior que os
    # outros campos, então aqui zera automaticamente em vez de só avisar.
    if (
        is_medicamento
        and not data.get("pagina_produto_url")
        and tarja is not None
        and not origem_e_cmed
        and not tarja_iqvia_mip_confirmada
    ):
        print(
            f"  [aviso] tarja zerada automaticamente para EAN {ean} - "
            f"medicamento sem pagina_produto_url (fonte não confirmada), "
            f"valor descartado: {tarja!r}"
        )
        data["tarja"] = tarja = None

    # retenção de receita - carimbada em código (não pelo modelo), mesma
    # lógica determinística de frase_obrigatoria/imagem. Para origem CMED,
    # mapear_cmed_para_schema já decidiu esse campo com a regra combinada
    # com o time de negócio (Tarja Preta / Sem Tarja / "- (*)" / Tarja
    # Vermelha cruzada com substancias_controladas) - não sobrescreve.
    if not origem_e_cmed:
        data["precisa_retencao_receita"] = "Sim" if tarja == "Tarja Preta" else "Não"

    # medicamento nunca leva imagem no e-commerce (regra de negócio) -
    # qualquer tarja, inclusive Sem Tarja / não confirmada. Não-medicamento
    # segue com a URL, depois filtrada por tamanho mínimo.
    imagem_bloqueada = is_medicamento
    if imagem_bloqueada and data.get("imagem_url"):
        print(
            f"  [info] imagem removida para EAN {ean} (medicamento): "
            f"{data['imagem_url']}"
        )
        data["imagem_url"] = None

    # imagem pequena demais (ícone/logo/thumbnail) não serve como foto de
    # produto - descarta sem gastar token, é só download + leitura local
    if not imagem_bloqueada and data.get("imagem_url"):
        ok, motivo = check_imagem_tamanho_minimo(data["imagem_url"])
        if not ok:
            print(
                f"  [aviso] imagem descartada para EAN {ean} ({motivo}): "
                f"{data['imagem_url']}"
            )
            data["imagem_url"] = None

    # registro_ms e generico só fazem sentido para medicamento
    if not is_medicamento:
        if data.get("registro_ms"):
            print(
                f"  [info] registro_ms removido para EAN {ean} (produto não "
                f"é medicamento): {data['registro_ms']}"
            )
            data["registro_ms"] = None
        if data.get("generico"):
            data["generico"] = None

    # data_pesquisa: carimbada em código (não pelo modelo, que não tem como
    # saber a data real da chamada) - só faz sentido quando um preço foi de
    # fato encontrado, senão fica sem sentido registrar "quando" de um dado
    # que não existe.
    data["data_pesquisa"] = date.today().isoformat() if data.get("preco_pesquisado") else None

    # departamento/categoria/subcategoria: confere contra a árvore oficial em
    # vez de confiar que o modelo seguiu a instrução do prompt - o modelo já
    # inventou uma combinação inexistente antes (categoria certa existia na
    # árvore, mas ele escolheu outra errada, e ainda deixou subcategoria
    # vazia com categoria preenchida, o que o próprio prompt proíbe).
    categorizacao_ok, motivo_categorizacao = validar_categorizacao(data)
    if not categorizacao_ok:
        print(f"  [aviso] categorização inválida para EAN {ean} ({motivo_categorizacao}) - zerada.")
        data["departamento"] = None
        data["categoria"] = None
        data["subcategoria"] = None

    # frase_obrigatoria: recompõe de forma determinística a partir dos campos
    # já validados acima em vez de confiar na composição livre do modelo, que
    # já colou "venda sob prescrição" num produto sem tarja e já truncou o
    # texto canônico de medicamento em geral.
    frase_final = compor_frase_obrigatoria(data, tarja, is_medicamento)
    if frase_final != data.get("frase_obrigatoria"):
        print(
            f"  [info] frase_obrigatoria recomposta para EAN {ean}: "
            f"{data.get('frase_obrigatoria')!r} -> {frase_final!r}"
        )
    data["frase_obrigatoria"] = frase_final

    # título com sal trocado (ex: "Cloridrato de Midazolam" quando a fonte
    # confirmou "Maleato de Midazolam") - corrige em código, ver
    # _corrigir_sal_titulo. Roda antes das checagens abaixo pra elas já
    # olharem o título corrigido.
    if is_medicamento:
        _corrigir_sal_titulo(data, ean)

    # título: não dá para reescrever com segurança em código (precisaria
    # saber a forma farmacêutica certa), só avisa para revisão manual quando
    # parece ter colado a composição completa (marca + 3 concentrações) ou
    # ficou longo demais para busca/listagem de e-commerce.
    titulo = data.get("titulo") or ""
    n_concentracoes = len(CONCENTRACAO_RE.findall(titulo))
    if data.get("marca") and n_concentracoes >= 3:
        print(
            f"  [aviso] título de EAN {ean} pode estar listando composição "
            f"completa indevidamente (marca + {n_concentracoes} concentrações) "
            f"- revisar manualmente: {titulo!r}"
        )
    elif len(titulo) > TITULO_MAX_RECOMENDADO:
        print(
            f"  [aviso] título de EAN {ean} tem {len(titulo)} caracteres "
            f"(> {TITULO_MAX_RECOMENDADO}) - revisar se está buscável: "
            f"{titulo!r}"
        )

    # título de não-medicamento começando pela marca: o prompt manda
    # "[O que o produto é] [Marca] [Linha] [Atributo] [Volume/Qtd]"
    # (tipo do objeto primeiro), mas já vimos o modelo devolver marca
    # primeiro mesmo assim - não dá pra reordenar com segurança em código
    # sem saber qual palavra é a categoria do produto, só avisa.
    marca_atual = data.get("marca")
    if not is_medicamento and marca_atual and titulo:
        if titulo.strip().lower().startswith(marca_atual.strip().lower()):
            print(
                f"  [aviso] título de EAN {ean} começa pela marca ({marca_atual!r}), "
                f"mas o padrão de não-medicamento manda categoria do produto "
                f"primeiro - revisar manualmente: {titulo!r}"
            )

    # fabricante igual à marca: sinal de que o fabricante não foi
    # confirmado de verdade (já vimos o modelo devolver o próprio nome da
    # marca como fabricante quando a busca não achou o fabricante real,
    # ex: marca "Nestonutri" e fabricante "Nestonutri" ao invés de "Nestlé")
    # - não dá pra saber o fabricante certo em código, só avisa.
    fabricante_atual = data.get("fabricante")
    if (
        marca_atual
        and fabricante_atual
        and marca_atual.strip().lower() == fabricante_atual.strip().lower()
    ):
        print(
            f"  [aviso] EAN {ean} tem fabricante igual à marca ({fabricante_atual!r}) "
            f"- provável fabricante não confirmado de verdade, revisar manualmente."
        )

    # título sem o princípio ativo: com marca + 1-2 princípios ativos, o
    # prompt exige nome + concentração de cada um no título (mesmo exemplo
    # usado no próprio prompt) - se nenhum princípio aparece, o modelo não
    # seguiu essa regra.
    principios = data.get("principios_ativos")
    if is_medicamento and data.get("marca") and principios:
        itens = [p.strip() for p in principios.split(",") if p.strip()]
        if 1 <= len(itens) <= 2:
            nomes = []
            for item in itens:
                match_nome = re.match(r"([^\d]+)", item)
                if match_nome:
                    nomes.append(match_nome.group(1).strip())
            titulo_lower = titulo.lower()
            # compara palavra a palavra (não a frase toda) - "Colágeno Tipo
            # II" no título já conta como citar "Colágeno Tipo II Não
            # Hidrolisado", não precisa bater o qualificador inteiro
            palavras_relevantes = [
                palavra
                for nome in nomes
                for palavra in nome.split()
                if len(palavra) > 3
            ]
            if palavras_relevantes and not any(
                palavra.lower() in titulo_lower for palavra in palavras_relevantes
            ):
                print(
                    f"  [aviso] título de EAN {ean} tem marca + 1-2 "
                    f"princípios ativos mas não cita nenhum deles - revisar: "
                    f"{titulo!r} (princípios: {principios!r})"
                )

    # contradição interna: tipo_cadastro="Medicamento" mas o próprio texto
    # gerado (descricao_curta/titulo) indica que é suplemento/isento de
    # registro - sinal de que o modelo classificou tipo_cadastro errado (viu
    # isso acontecer: Medicamento com descricao_curta dizendo "suplemento
    # alimentar sem prescrição médica").
    texto_gerado = f"{titulo} {data.get('descricao_curta') or ''}"
    if is_medicamento and SUPLEMENTO_CONTRADICAO_RE.search(texto_gerado):
        print(
            f"  [aviso] EAN {ean} classificado como Medicamento, mas o texto "
            f"gerado sugere suplemento/isento de registro - revisar "
            f"tipo_cadastro: {texto_gerado[:200]!r}"
        )

    # sem fonte rastreável: pagina_produto_url vazio mas campos que só fazem
    # sentido com base numa fonte confirmada vieram preenchidos - contraria a
    # REGRA CRÍTICA do prompt ("campo não confirmado na fonte = null"). Só
    # avisa (não zera automaticamente) porque às vezes a fonte real existe
    # mas o modelo esqueceu de citar a URL.
    if (
        not data.get("pagina_produto_url")
        and any(data.get(c) for c in CAMPOS_DEPENDENTES_DE_FONTE)
        and not origem_confiavel_sem_url
    ):
        print(
            f"  [aviso] EAN {ean} sem pagina_produto_url mas com campos "
            f"dependentes de fonte preenchidos - revisar se os dados foram "
            f"confirmados de verdade ou inferidos sem base."
        )

    return data


def call_model(
    client,
    model,
    ean,
    nome_produto,
    max_retries=3,
    verify_images=False,
    verify_tarja=True,
    pistas_nao_confirmadas=None,
):
    """
    Chama a API com os tools web_search/web_fetch e retorna
    (data, usage). data é o JSON parseado ou None se não foi possível
    localizar/parsear informação. usage é um dict {"tokens", "cache_creation",
    "cache_read"} somado de todas as chamadas feitas para esta linha
    (tentativas, resumes, se verify_images=True a checagem de imagem com
    visão - desativada por padrão para economizar tokens -, e se
    verify_tarja=True a verificação dedicada de tarja/registro_ms para
    medicamento - ligada por padrão, o risco legal de tarja errada compensa
    o custo extra). cache_read > 0
    confirma que o system prompt cacheado foi reaproveitado (~90% mais
    barato); cache_creation > 0 indica que esta chamada gravou o cache.

    pistas_nao_confirmadas: dados parciais do crawler quando o match não foi
    confiável o bastante para fechar o cadastro. O prompt trata como pista,
    nunca como fato - tarja/registro_ms não entram aqui de propósito.
    web_fetch do enriquecimento geral tem teto de conteúdo; a verificação
    de tarja não tem (página oficial precisa caber inteira).

    departamento/categoria/subcategoria NÃO vêm da busca: depois do JSON
    de fatos, categorizar_apos_busca faz uma chamada leve só com o ramo
    do tipo_cadastro, sem a árvore completa no prompt agentic.

    nome_produto da planilha entra sempre em minúsculo (ver nome_para_busca).
    """
    nome_produto = nome_para_busca(nome_produto)
    user_message = build_user_message(ean, nome_produto, pistas_nao_confirmadas)
    messages = [{"role": "user", "content": user_message}]
    tools = _ferramentas_busca(max_uses=3)
    usage = {"tokens": 0, "cache_creation": 0, "cache_read": 0}

    def track(response):
        tokens, cache_creation, cache_read = response_usage(response)
        usage["tokens"] += tokens
        usage["cache_creation"] += cache_creation
        usage["cache_read"] += cache_read

    for attempt in range(1, max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=1200,
                temperature=0,
                system=SYSTEM_BLOCK,
                tools=tools,
                messages=messages,
            )
            track(response)

            # server-side tool loop pausou (limite de iterações) - reenvia
            # o turno para o servidor continuar de onde parou.
            resume_count = 0
            while response.stop_reason == "pause_turn" and resume_count < 3:
                messages.append({"role": "assistant", "content": response.content})
                response = client.messages.create(
                    model=model,
                    max_tokens=1200,
                    temperature=0,
                    system=SYSTEM_BLOCK,
                    tools=tools,
                    messages=messages,
                )
                track(response)
                resume_count += 1

            if response.stop_reason == "refusal":
                return None, usage

            final_text = get_final_text(response)
            if not final_text:
                return None, usage

            try:
                data = extract_json(final_text)
            except json.JSONDecodeError:
                # JSON malformado pode ser um erro pontual do modelo, não só
                # falha de API - antes desistia na hora (sem retry nenhum);
                # agora tenta de novo como as outras falhas transitórias,
                # só desistindo de vez se esgotar max_retries.
                print(
                    f"  [aviso] resposta não é JSON válido para EAN {ean} "
                    f"(tentativa {attempt}/{max_retries}): {final_text[:200]!r}",
                    file=sys.stderr,
                )
                if attempt == max_retries:
                    return None, usage
                messages = [{"role": "user", "content": user_message}]
                time.sleep(min(2 ** attempt, 30))
                continue

            categoria_bruta = None
            if pistas_nao_confirmadas:
                categoria_bruta = pistas_nao_confirmadas.get("categoria_do_site")
            data, usage_cat = categorizar_apos_busca(
                client, model, data, nome_produto, categoria_bruta
            )
            usage["tokens"] += usage_cat["tokens"]
            usage["cache_creation"] += usage_cat["cache_creation"]
            usage["cache_read"] += usage_cat["cache_read"]

            data = apply_safety_checks(data, ean)
            data["model"] = model

            if verify_tarja and data.get("tipo_cadastro") == "Medicamento":
                resultado_verif, usage_verif = verify_tarja_registro(
                    client,
                    model,
                    ean,
                    data.get("titulo"),
                    data.get("marca"),
                    data.get("principios_ativos"),
                )
                usage["tokens"] += usage_verif["tokens"]
                usage["cache_creation"] += usage_verif["cache_creation"]
                usage["cache_read"] += usage_verif["cache_read"]

                if resultado_verif is None:
                    print(
                        f"  [aviso] verificação dedicada de tarja/registro_ms "
                        f"falhou para EAN {ean} - mantendo valor original sem "
                        f"essa confirmação extra."
                    )
                elif resultado_verif.get("confirmado"):
                    tarja_verificada = resultado_verif.get("tarja")
                    registro_verificado = resultado_verif.get("registro_ms")
                    # a verificação dedicada também é o modelo respondendo -
                    # revalida contra o mesmo vocabulário fechado que
                    # apply_safety_checks já exigiu da resposta principal,
                    # em vez de confiar cegamente na segunda chamada
                    if tarja_verificada is not None and tarja_verificada not in ALLOWED_TARJA:
                        print(
                            f"  [aviso] verificação dedicada devolveu tarja "
                            f"fora do vocabulário para EAN {ean} "
                            f"({tarja_verificada!r}) - zerada."
                        )
                        tarja_verificada = None
                    if tarja_verificada != data.get("tarja"):
                        print(
                            f"  [info] tarja corrigida por verificação "
                            f"dedicada para EAN {ean}: "
                            f"{data.get('tarja')!r} -> {tarja_verificada!r}"
                        )
                    if registro_verificado != data.get("registro_ms"):
                        print(
                            f"  [info] registro_ms corrigido por verificação "
                            f"dedicada para EAN {ean}: "
                            f"{data.get('registro_ms')!r} -> {registro_verificado!r}"
                        )
                    data["tarja"] = tarja_verificada
                    data["registro_ms"] = registro_verificado
                else:
                    print(
                        f"  [aviso] verificação dedicada não confirmou "
                        f"tarja/registro_ms para EAN {ean} - zerando por "
                        f"segurança (era: tarja={data.get('tarja')!r}, "
                        f"registro_ms={data.get('registro_ms')!r})."
                    )
                    data["tarja"] = None
                    data["registro_ms"] = None

                # frase_obrigatoria depende da tarja - recompõe com o valor
                # atualizado pela verificação dedicada
                data["frase_obrigatoria"] = compor_frase_obrigatoria(
                    data, data.get("tarja"), True
                )

                # precisa_retencao_receita também depende da tarja - mesma
                # lógica determinística de apply_safety_checks, recalculada
                # aqui com o valor final pós-verificação dedicada
                data["precisa_retencao_receita"] = (
                    "Sim" if data.get("tarja") == "Tarja Preta" else "Não"
                )

                # medicamento nunca leva imagem - reforço depois da
                # verificação de tarja, caso algum caminho ainda tenha
                # preenchido imagem_url.
                if (
                    data.get("tipo_cadastro") == "Medicamento"
                    and data.get("imagem_url")
                ):
                    print(
                        f"  [info] imagem removida para EAN {ean} "
                        f"(medicamento): {data['imagem_url']}"
                    )
                    data["imagem_url"] = None

            if verify_images and data.get("imagem_url"):
                ok, verify_tokens = verify_image(
                    client,
                    model,
                    data["imagem_url"],
                    ean,
                    nome_produto,
                    data.get("titulo"),
                )
                usage["tokens"] += verify_tokens
                if not ok:
                    print(
                        f"  [info] imagem descartada para EAN {ean} "
                        f"(não corresponde ao produto): {data['imagem_url']}"
                    )
                    data["imagem_url"] = None

            return data, usage

        except (APIStatusError, APIConnectionError) as exc:
            wait = min(2 ** attempt, 30)
            print(
                f"  [erro] tentativa {attempt}/{max_retries} falhou para "
                f"EAN {ean}: {exc}. Retentando em {wait}s...",
                file=sys.stderr,
            )
            time.sleep(wait)

    return None, usage


DB_CONFIG = {
    "host": os.environ.get("PG_HOST", "localhost"),
    "port": os.environ.get("PG_PORT", "5433"),
    "user": os.environ.get("PG_USER", "cadastro"),
    "password": os.environ.get("PG_PASSWORD", "cadastro"),
    "dbname": os.environ.get("PG_DB", "cadastro_produtos"),
}

FASES_TERMINAIS = ("concluido", "nao_localizado")

# colunas gravadas em produtos além de RESULT_COLUMNS/VALIDACAO_COLUMNS - vêm
# de fontes oficiais (CMED/ABCFarma/IQVIA/crawler) em enrich_com_crawler.py,
# não da resposta do Claude puro
COLUNAS_ORIGEM = ["origem_enriquecimento", "confirmado_anvisa_cmed", "origem_categorizacao", "model"]


def conectar():
    return psycopg2.connect(**DB_CONFIG)


def buscar_pendentes(conn, eans=None, limit=None):
    """
    Lista (ean, nome_produto) pendentes de enriquecimento na tabela
    produtos. Sem `eans`, pega qualquer linha fora das fases terminais
    (concluido/nao_localizado), na ordem do EAN. Com `eans`, ignora a fase e
    busca só esses EANs específicos - útil pra reprocessar um caso pontual.
    """
    with conn.cursor() as cur:
        if eans:
            cur.execute(
                "SELECT ean, nome_produto FROM produtos WHERE ean = ANY(%s) ORDER BY ean",
                (list(eans),),
            )
        else:
            query = (
                "SELECT ean, nome_produto FROM produtos "
                "WHERE fase_atual NOT IN %s ORDER BY ean"
            )
            params = [FASES_TERMINAIS]
            if limit is not None:
                query += " LIMIT %s"
                params.append(limit)
            cur.execute(query, params)
        return cur.fetchall()


def salvar_resultado(conn, ean, data, usage=None):
    """
    Grava o resultado de um EAN na tabela produtos e comita na hora - uma
    transação por linha, então se o processo cair no meio, no máximo essa
    linha se perde, nunca as já concluídas antes dela.
    """
    usage = usage or {"tokens": 0, "cache_creation": 0, "cache_read": 0}

    if data is None or not data.get("titulo"):
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE produtos
                SET fase_atual = 'nao_localizado',
                    tokens_utilizados = %s,
                    tokens_cache_gravados = %s,
                    tokens_cache_lidos = %s,
                    atualizado_em = now()
                WHERE ean = %s
                RETURNING id
                """,
                (usage["tokens"], usage["cache_creation"], usage["cache_read"], ean),
            )
            produto_id = cur.fetchone()[0]
            registrar_versao_historico(cur, produto_id, ean, "nao_localizado", {}, usage)
        conn.commit()
        return

    data = marcar_validacao_humana(data)
    colunas = RESULT_COLUMNS + VALIDACAO_COLUMNS + COLUNAS_ORIGEM
    set_clause = ", ".join(f"{col} = %s" for col in colunas)
    valores = [data.get(col) for col in colunas]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE produtos
            SET {set_clause},
                fase_atual = 'concluido',
                tokens_utilizados = %s,
                tokens_cache_gravados = %s,
                tokens_cache_lidos = %s,
                atualizado_em = now()
            WHERE ean = %s
            RETURNING id
            """,
            valores + [usage["tokens"], usage["cache_creation"], usage["cache_read"], ean],
        )
        produto_id = cur.fetchone()[0]
        registrar_versao_historico(cur, produto_id, ean, "concluido", data, usage)
    conn.commit()


def registrar_versao_historico(cur, produto_id, ean, fase_resultado, data, usage):
    """
    Grava a versão que acabou de ser calculada em produtos_historico - não a
    anterior, a nova. Toda chamada de salvar_resultado grava uma linha aqui,
    inclusive a primeira vez que um EAN é enriquecido - assim a tabela
    sozinha já é a timeline completa do produto (mais recente primeiro),
    sem precisar combinar com o estado atual de produtos pra montar uma
    tela. Sem acumulação em lugar nenhum: tokens_* aqui, e em produtos, são
    sempre o gasto desta chamada específica - cada linha (e o estado atual
    de produtos) é uma foto de uma versão, nunca um total histórico.
    """
    colunas = RESULT_COLUMNS + VALIDACAO_COLUMNS + COLUNAS_ORIGEM
    valores = [data.get(col) for col in colunas]
    cur.execute(
        f"""
        INSERT INTO produtos_historico
            (produto_id, ean, fase_resultado, {', '.join(colunas)},
             tokens_utilizados, tokens_cache_gravados, tokens_cache_lidos)
        VALUES (%s, %s, %s, {', '.join(['%s'] * len(colunas))}, %s, %s, %s)
        """,
        [produto_id, ean, fase_resultado] + valores
        + [usage["tokens"], usage["cache_creation"], usage["cache_read"]],
    )


def buscar_ja_ok_nao_cmed(conn, eans=None, limit=None):
    """
    Produtos já concluídos mas não confirmados pela CMED - usado pelo modo
    --reconciliar-cmed de enrich_com_crawler.py pra revisitar só esses.
    """
    with conn.cursor() as cur:
        if eans:
            cur.execute(
                """
                SELECT ean, nome_produto FROM produtos
                WHERE ean = ANY(%s) AND fase_atual = 'concluido'
                  AND confirmado_anvisa_cmed IS DISTINCT FROM 'Sim'
                ORDER BY ean
                """,
                (list(eans),),
            )
        else:
            query = (
                "SELECT ean, nome_produto FROM produtos "
                "WHERE fase_atual = 'concluido' "
                "AND confirmado_anvisa_cmed IS DISTINCT FROM 'Sim' ORDER BY ean"
            )
            params = []
            if limit is not None:
                query += " LIMIT %s"
                params.append(limit)
            cur.execute(query, params)
        return cur.fetchall()


def promover_cmed(conn, ean, data, usage=None):
    """
    Promove um produto pra fonte oficial CMED sem tocar fase_atual (já está
    'concluido') - só usado pelo modo --reconciliar-cmed.
    """
    usage = usage or {"tokens": 0, "cache_creation": 0, "cache_read": 0}
    colunas = RESULT_COLUMNS + VALIDACAO_COLUMNS + COLUNAS_ORIGEM
    set_clause = ", ".join(f"{col} = %s" for col in colunas)
    valores = [data.get(col) for col in colunas]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE produtos
            SET {set_clause}, tokens_utilizados = %s, atualizado_em = now()
            WHERE ean = %s
            """,
            valores + [usage["tokens"], ean],
        )
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default="claude-haiku-4-5-20251001",
        help="Model ID a usar (padrão: claude-haiku-4-5-20251001)",
    )
    parser.add_argument(
        "--eans",
        default=None,
        help="Lista de EANs específicos a (re)processar, separados por vírgula (ignora a fase atual)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Número máximo de linhas pendentes a processar"
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Segundos de espera antes de cada chamada, por worker (padrão: 1.0)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help=(
            "Número de chamadas simultâneas à API (padrão: 5). Se você "
            "começar a ver muitos erros de rate limit (429) nos logs, "
            "reduza esse valor; se não vir nenhum erro, pode aumentar."
        ),
    )
    parser.add_argument(
        "--verify-images",
        action="store_true",
        help=(
            "Verifica com visão (chamada extra à API, gasta mais tokens) "
            "se a imagem_url sugerida realmente mostra o produto, "
            "descartando-a caso contrário. Desativado por padrão."
        ),
    )
    parser.add_argument(
        "--sem-verificar-tarja",
        action="store_true",
        help=(
            "Desativa a verificação dedicada de tarja/registro_ms de "
            "medicamento (chamada extra à API com busca própria, gasta mais "
            "tokens só nas linhas de Medicamento). Ativada por padrão - o "
            "modelo já errou tarja mesmo citando fonte na resposta principal "
            "(ex: Estomanol, Tenoretic, Nasonex), e tarja errada tem risco "
            "legal maior que o custo extra de token."
        ),
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or "COLOQUE_SUA_KEY_AQUI" in api_key:
        sys.exit(
            "Erro: defina uma API key válida da Anthropic no início do "
            "arquivo enrich_produtos.py."
        )

    eans_filtro = None
    if args.eans:
        eans_filtro = [e.strip() for e in args.eans.split(",") if e.strip()]

    client = Anthropic()
    conn = conectar()
    try:
        pendentes = buscar_pendentes(conn, eans=eans_filtro, limit=args.limit)
        total = len(pendentes)
        print(f"{total} produto(s) pendente(s) na tabela produtos.")

        def worker(ean, nome_produto):
            nome_busca = nome_para_busca(nome_produto)
            time.sleep(args.sleep)  # espaça o início de cada chamada dentro do worker
            data, usage = call_model(
                client,
                args.model,
                ean,
                nome_busca,
                verify_images=args.verify_images,
                verify_tarja=not args.sem_verificar_tarja,
            )
            return ean, nome_busca, data, usage

        processed = 0
        total_tokens_geral = 0
        total_cache_read = 0
        total_cache_creation = 0
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futures = {
                pool.submit(worker, ean, nome_produto): ean for ean, nome_produto in pendentes
            }

            # salvar_resultado só acontece aqui na thread principal - seguro
            for future in as_completed(futures):
                ean, nome_produto, data, usage = future.result()
                salvar_resultado(conn, ean, data, usage)
                processed += 1
                total_tokens_geral += usage["tokens"]
                total_cache_read += usage["cache_read"]
                total_cache_creation += usage["cache_creation"]

                status = STATUS_OK if data and data.get("titulo") else STATUS_NOT_FOUND
                cache_info = (
                    f"cache: +{usage['cache_read']} lidos, "
                    f"+{usage['cache_creation']} gravados"
                )
                revisao = ""
                if data and data.get(VALIDACAO_HUMANA_COLUMN) == "Sim":
                    revisao = " | REVISÃO HUMANA"
                print(
                    f"[{processed}/{total}] EAN {ean} - {nome_produto} -> {status} "
                    f"({usage['tokens']} tokens | {cache_info}{revisao})"
                )

        print(
            f"Concluído. {total_tokens_geral} tokens usados nesta execução "
            f"({total_cache_read} lidos do cache, {total_cache_creation} gravados no "
            f"cache)."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
