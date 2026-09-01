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
import psycopg2.extras
from anthropic import Anthropic, APIStatusError, APIConnectionError
from PIL import Image, UnidentifiedImageError

import categorias
import dominios
import substancias_controladas
from dominios import (
    ORIGEM_ABCFARMA,
    ORIGEM_ANVISA_CMED,
    ORIGEM_CLAUDE,
    ORIGEM_CRAWLER,
    ORIGEM_IQVIA,
    ORIGEM_TARJADOS,
    TARJA_NAO_APLICAVEL,
    TARJA_PRETA,
    TARJA_SEM,
    TARJA_VERMELHA,
    TIPO_NAO_MEDICAMENTO,
    eh_medicamento,
    eh_verdadeiro,
    nome_tipo_produto,
    origem_codigo,
)


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
    "tipo_produto",
    "registro_ms",
    "generico",
    "tarja",
    "precisa_retencao_receita",
    "principios_ativos",
    "descricao_curta",
    "frase_obrigatoria",
    "categoria_id",
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

MENSAGEM_VALIDACAO_TARJADOS_TARJA = (
    "VALIDAÇÃO HUMANA OBRIGATÓRIA: este medicamento foi confirmado pela "
    "base de Tarjados do time (RX/CONTROLADO/NÃO INFORMADO), mas essa base "
    "não distingue Tarja Vermelha de Tarja Preta, e a tarja não foi "
    "confirmada nem pelo bulário nem por verificação dedicada. Não "
    "publicar no e-commerce antes de um responsável confirmar a tarja em "
    "fonte oficial (bula/ANVISA)."
)

MENSAGEM_VALIDACAO_TARJADOS_TIPO_AMBIGUO = (
    "VALIDAÇÃO HUMANA OBRIGATÓRIA: este produto veio da base de Tarjados "
    "com TIPO DE PRODUTO de medicamento (RX/CONTROLADO), mas a categoria "
    "já revisada pelo time mapeia para o ramo Não Medicamento da árvore "
    "oficial - confirmar se o tipo_produto está correto antes de publicar "
    "no e-commerce."
)


def marcar_validacao_humana(data):
    from pipeline.safety.human_review import marcar_validacao_humana as _marcar
    return _marcar(data)


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
    resultado:\\n```json\\n{...}\\n```'). Converte rótulos do modelo
    (Medicamento, Tarja Vermelha, Sim/Não) pro vocabulário persistido.
    """
    text = text.strip()

    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        data = json.loads(fence_match.group(1))
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            data = json.loads(text[start : end + 1])
        else:
            data = json.loads(text)
    if isinstance(data, dict):
        dominios.normalizar_cadastro(data)
    return data


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


def _montar_format_system(arvore, tipo=None):
    if tipo is None:
        template = FORMAT_CAMPOS_SYSTEM
    else:
        from pipeline.policies.base import policy_for
        template = policy_for(tipo).format_system_template()
    return template.replace("{ARVORE_RAMO}", arvore or "")


# um bloco cacheado por ramo - tipo_cadastro já é conhecido nessas chamadas,
# então não manda o outro ramo. o Claude puro de busca também não leva a
# árvore: depois da busca, categorizar_apos_busca usa só o ramo do tipo.
# MED/NMED usam o pack do tipo; tipo None (legado) mantém o prompt misto.
FORMAT_SYSTEM_BLOCKS = {
    tipo: system_cached(_montar_format_system(arvore, tipo))
    for tipo, arvore in ARVORES_POR_RAMO.items()
}
FORMAT_SYSTEM_BLOCKS[None] = system_cached(
    _montar_format_system(ARVORE_CATEGORIZACAO or "", None)
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
        f"tipo_cadastro: {nome_tipo_produto(tipo_cadastro) or tipo_cadastro}\nmarca: {marca}\n"
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
    if tipo_cadastro == TIPO_NAO_MEDICAMENTO:
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

    tipo = data.get("tipo_produto")
    if tipo not in ARVORES_POR_RAMO:
        return data, usage

    system = CATEGORIZACAO_SYSTEM_BLOCKS.get(tipo)
    if not system:
        return data, usage

    mensagem = (
        f"tipo_cadastro: {nome_tipo_produto(tipo) or tipo}\n"
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


from pipeline.composition import (
    parsear_composicao_cmed,
    normalizar_nome_substancia_cmed,
    split_substancias_cmed as _split_substancias_cmed,
    nome_principio_cmed as _nome_principio_cmed,
    concentracoes_apresentacao_cmed as _concentracoes_apresentacao_cmed,
)


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


def formatar_composicao_tarjados(client, model, molecula, produto):
    """
    Como formatar_composicao_cmed, mas pra base de Tarjados: o campo
    MOLECULA já separa substâncias por ";", igual à CMED - não precisa
    normalizar separador, só reusa o mesmo parser/prompt. A base não tem uma
    "apresentação" separada do nome (tudo vem junto em PRODUTO, mesmo caso
    da IQVIA) - passa o campo inteiro como apresentação.
    """
    return formatar_composicao_cmed(client, model, molecula, produto)


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

ALLOWED_TARJA = set(dominios.TARJAS)

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


def _corrigir_sal_titulo(data, ean):
    from pipeline.safety.titulo import corrigir_sal_titulo
    return corrigir_sal_titulo(data, ean)


from pipeline.safety.titulo import sal_no_texto as _sal_no_texto


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

# fotos aceitas no enriquecimento — um JPEG por EAN, na raiz do projeto
DIRETORIO_IMAGENS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "imagens")


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


def _baixar_imagem(image_url, max_bytes=IMAGEM_MAX_BYTES, timeout=10):
    """
    Baixa a imagem com as mesmas travas de SSRF da validação de tamanho
    (http/https, host público, teto de bytes, redirect revalidado).
    Retorna (bytes, None) ou (None, motivo).
    """
    url_atual = image_url
    try:
        for _ in range(IMAGEM_REDIRECTS_MAX + 1):
            parsed = urlparse(url_atual)
            esquema = parsed.scheme.lower()
            if esquema not in ESQUEMAS_URL_PERMITIDOS:
                return None, f"esquema de URL não permitido ({esquema!r})"
            if not _host_imagem_publico(parsed.hostname):
                return None, f"host de imagem não permitido ({parsed.hostname!r})"

            with httpx.stream(
                "GET", url_atual, timeout=timeout, follow_redirects=False
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        return None, "redirect sem Location"
                    url_atual = str(response.url.join(location))
                    continue
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    return None, f"imagem maior que o teto de {max_bytes} bytes"

                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > max_bytes:
                        return None, f"imagem maior que o teto de {max_bytes} bytes"
            return bytes(chunks), None
        return None, f"mais de {IMAGEM_REDIRECTS_MAX} redirects"
    except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError) as exc:
        # InvalidURL (ex: caractere não-imprimível tipo '\t' vindo sujo do
        # site raspado) não herda de HTTPError - sem essa checagem em separado
        # ela derrubava o worker inteiro em vez de só descartar essa imagem.
        return None, f"não foi possível baixar a imagem ({exc})"


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
    cego. Retorna (ok: bool, motivo: str, conteudo: bytes|None).
    """
    conteudo, motivo = _baixar_imagem(image_url, max_bytes=max_bytes, timeout=timeout)
    if conteudo is None:
        return False, motivo, None
    try:
        with Image.open(io.BytesIO(conteudo)) as img:
            largura, altura = img.size
        if largura < largura_minima or altura < altura_minima:
            return (
                False,
                (
                    f"{largura}x{altura}px (mínimo exigido: "
                    f"{largura_minima}x{altura_minima}px)"
                ),
                None,
            )
        return True, f"{largura}x{altura}px", conteudo
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return False, f"não foi possível validar a imagem ({exc})", None


def salvar_imagem_local(ean, image_url, conteudo=None):
    """
    Grava a foto do produto em imagens/{ean}.jpg. Converte para JPEG mesmo
    se a origem for png/webp. Falha de download não derruba o cadastro.
    Retorna o caminho gravado ou None.
    """
    ean_arquivo = re.sub(r"\D", "", str(ean or ""))
    if not ean_arquivo:
        return None
    if conteudo is None:
        if not image_url:
            return None
        conteudo, motivo = _baixar_imagem(image_url)
        if conteudo is None:
            print(f"  [aviso] não gravou imagem local para EAN {ean_arquivo} ({motivo})")
            return None
    try:
        os.makedirs(DIRETORIO_IMAGENS, exist_ok=True)
        destino = os.path.join(DIRETORIO_IMAGENS, f"{ean_arquivo}.jpg")
        with Image.open(io.BytesIO(conteudo)) as img:
            if img.mode in ("RGBA", "LA", "P"):
                fundo = Image.new("RGB", img.size, (255, 255, 255))
                rgba = img.convert("RGBA")
                fundo.paste(rgba, mask=rgba.split()[-1])
                img = fundo
            elif img.mode != "RGB":
                img = img.convert("RGB")
            img.save(destino, "JPEG", quality=90)
        print(f"  [info] imagem salva em {destino}")
        return destino
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        print(f"  [aviso] não gravou imagem local para EAN {ean_arquivo} ({exc})")
        return None


from pipeline.safety.checks import apply_safety_checks, validar_categorizacao
from pipeline.safety.frases import resolver_retencao, compor_frase_obrigatoria


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
            data["modelo"] = model

            if verify_tarja and eh_medicamento(data):
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

                # retenção e frase dependem da tarja final - retenção
                # primeiro, senão a frase usa o valor antigo
                data["precisa_retencao_receita"] = resolver_retencao(
                    data.get("tarja"), data.get("principios_ativos")
                )
                data["frase_obrigatoria"] = compor_frase_obrigatoria(
                    data, data.get("tarja"), True
                )

                # medicamento nunca leva imagem - reforço depois da
                # verificação de tarja, caso algum caminho ainda tenha
                # preenchido imagem_url.
                if (
                    eh_medicamento(data)
                    and data.get("imagem_url")
                ):
                    print(
                        f"  [info] imagem removida para EAN {ean} "
                        f"(medicamento): {data['imagem_url']}"
                    )
                    data["imagem_url"] = None
                    data.pop("_imagem_bytes", None)

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
                    data.pop("_imagem_bytes", None)

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


from pipeline.db import dsn as _dsn

DB_CONFIG = _dsn()

FASES_TERMINAIS = ("concluido", "nao_localizado")

# colunas gravadas em produtos além de RESULT_COLUMNS/VALIDACAO_COLUMNS - vêm
# de fontes oficiais (CMED/ABCFarma/IQVIA/crawler) em enrich_com_crawler.py,
# não da resposta do Claude puro
COLUNAS_ORIGEM = [
    "origem_enriquecimento",
    "origem_referencia",
    "confirmado_anvisa_cmed",
    "origem_categorizacao",
    "modelo",
]


def conectar():
    return psycopg2.connect(**_dsn())


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

    data = marcar_validacao_humana(dominios.normalizar_cadastro(data))
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
    if data.get("imagem_url"):
        salvar_imagem_local(
            ean,
            data["imagem_url"],
            conteudo=data.pop("_imagem_bytes", None),
        )


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

    O snapshot inteiro vai pra `dados` (JSONB) - só produto_id/ean continuam
    colunas reais, usadas pra filtrar (ver listar_historico em
    app/repos/produtos.py). fase_resultado e tokens_* entram dentro de
    `dados` junto com o resto, não são consultados soltos em lugar nenhum.
    """
    colunas = RESULT_COLUMNS + VALIDACAO_COLUMNS + COLUNAS_ORIGEM
    dados = {col: data.get(col) for col in colunas}
    # nomes oficiais no momento da gravação (ponto no tempo); a tabela
    # produtos só guarda categoria_id, mas o histórico precisa dos textos
    # pra a ficha antiga continuar legível se a folha for renomeada depois.
    dados["departamento"] = data.get("departamento")
    dados["categoria"] = data.get("categoria")
    dados["subcategoria"] = data.get("subcategoria")
    dados["fase_resultado"] = fase_resultado
    dados["tokens_utilizados"] = usage["tokens"]
    dados["tokens_cache_gravados"] = usage["cache_creation"]
    dados["tokens_cache_lidos"] = usage["cache_read"]
    cur.execute(
        "INSERT INTO produtos_historico (produto_id, ean, dados) VALUES (%s, %s, %s)",
        [produto_id, ean, psycopg2.extras.Json(dados)],
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
                  AND confirmado_anvisa_cmed IS DISTINCT FROM true
                ORDER BY ean
                """,
                (list(eans),),
            )
        else:
            query = (
                "SELECT ean, nome_produto FROM produtos "
                "WHERE fase_atual = 'concluido' "
                "AND confirmado_anvisa_cmed IS DISTINCT FROM true ORDER BY ean"
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
                if data and eh_verdadeiro(data.get(VALIDACAO_HUMANA_COLUMN)):
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
