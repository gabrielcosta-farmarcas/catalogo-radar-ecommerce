"""
Enriquece produtos pendentes da tabela `produtos` (Postgres, ver db.py) em 5
camadas, da mais barata pra mais cara:
0. Tabela `anvisa_medicamentos` (base oficial da ANVISA/CMED, carregada por
   carregar_cmed.py) - se o EAN está lá, o produto É medicamento com certeza
   (fonte oficial, sem ambiguidade) e os dados vêm direto da tabela; só
   formatação leve (sem busca, sem segunda verificação de tarja/registro_ms).
0.5. Tabela `abcfarma_medicamentos` (base ABCFarma, carregada por
   carregar_abcfarma.py) - segunda fonte oficial de certeza de medicamento,
   só consultada se a CMED não achou o EAN. Diferente da CMED, não confirma
   tarja - essa fica null e vai pra fila de validação humana (ver
   abcfarma.py e ep.marcar_validacao_humana).
0.7. Tabela `iqvia_produtos` (catálogo de um parceiro, carregado por
   carregar_iqvia.py) - terceira fonte de referência, só consultada se CMED
   e ABCFarma não acharam o EAN. Ao contrário das duas, cobre não-
   medicamento também (cosmético, alimento etc. - ver iqvia.py). Pra
   medicamento, já diz "precisa receita" (RX) vs "isento de prescrição"
   (MIP) - MIP confirma tarja "Sem Tarja" direto (categoria regulatória
   oficial); RX não distingue Vermelha de Preta, mesma régua de validação
   humana da ABCFarma.
1. crawler (pacote local `crawler/` - raspa sites de farmácias concorrentes
   e o portal de bulário sara.com.br por EAN, de graça, sem token nenhum).
2. Claude puro (enrich_produtos.py) com busca agentic completa - só quando
   as camadas acima não encontram nada usável.

Cada linha grava de onde veio o dado, na coluna `origem_enriquecimento`
("anvisa_cmed", "abcfarma", "iqvia", "crawler+claude" ou "claude").
Medicamento encontrado só via Claude (busca na internet) recebe
`precisa_validacao_humana=Sim` e uma mensagem para revisão humana antes de
ir ao e-commerce; medicamento confirmado só pela ABCFarma, ou pela IQVIA
como RX, entra na mesma fila se a tarja não veio do bulário (Sara). Crawler
com tarja só de farmácia (não Sara) também entra nessa fila. CMED, IQVIA
como MIP e crawler com tarja do Sara não entram.

Lê e grava os produtos pendentes direto na tabela `produtos` do Postgres
(ver db.py) - não usa mais planilha como banco de trabalho.

Uso básico:
    python enrich_com_crawler.py

Uso com opções:
    python enrich_com_crawler.py --eans 7891234567890,7899876543210 --limit 20

Requer ANTHROPIC_API_KEY configurada (já tratado por enrich_produtos.py).
"""

import argparse
import difflib
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from crawler.adapters.araujo import AraujoAdapter
from crawler.adapters.drogal import DrogalAdapter
from crawler.adapters.drogaria_pacheco import DrogariaPachecoAdapter
from crawler.adapters.drogaria_sp import DrogariaSPAdapter
from crawler.adapters.panvel import PanvelAdapter
from crawler.adapters.raiadrogasil import DrogaRaiaAdapter, DrogasilAdapter
from crawler.adapters.sara import SaraAdapter
from crawler.adapters.venancio import VenancioAdapter

import abcfarma
import cmed
import iqvia
import substancias_controladas
import enrich_produtos as ep

CLIENT = None
MODELO_PADRAO = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")


def garantir_cliente():
    """Cria o cliente Anthropic uma vez. Usado pelo CLI e pela API HTTP."""
    global CLIENT
    if CLIENT is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or "COLOQUE_SUA_KEY_AQUI" in api_key:
            raise RuntimeError("Defina uma API key válida da Anthropic (ANTHROPIC_API_KEY).")
        CLIENT = ep.Anthropic()
    return CLIENT


def _args_enriquecimento(
    model=MODELO_PADRAO,
    verify_images=False,
    sem_verificar_tarja=False,
    sleep=1.0,
):
    return argparse.Namespace(
        model=model,
        verify_images=verify_images,
        sem_verificar_tarja=sem_verificar_tarja,
        sleep=sleep,
    )


def enriquecer_ean(
    ean,
    nome_produto,
    model=MODELO_PADRAO,
    verify_images=False,
    sem_verificar_tarja=False,
    sleep=0.0,
    on_progress=None,
):
    """
    Roda o mesmo fluxo do CLI para UM EAN e devolve
    (ean, nome_produto, data, usage). Não grava no banco - o chamador decide.
    """
    garantir_cliente()
    nome = ep.nome_para_busca(nome_produto)
    args = _args_enriquecimento(
        model=model,
        verify_images=verify_images,
        sem_verificar_tarja=sem_verificar_tarja,
        sleep=sleep,
    )
    args.on_progress = on_progress
    return worker(str(ean).strip(), nome, args)

# mesma ordem de prioridade do scraper.consolidate - sites mais confiáveis/
# completos primeiro (ultrafarma fica de fora, não busca por EAN). sara vem
# primeiro de todos: não é farmácia (sem preço/venda), é um portal de
# bulário - os campos regulatórios que ele preenche (registro_ms, tarja,
# active_ingredient) vêm direto do bulário oficial, mais confiáveis que o
# texto livre das páginas de varejo dos sites abaixo. só cobre medicamento
# com registro na ANVISA - não retorna nada pra cosmético/fralda/etc.
ADAPTERS_EM_ORDEM = [
    SaraAdapter(),
    DrogasilAdapter(),
    DrogaRaiaAdapter(),
    DrogariaPachecoAdapter(),
    DrogariaSPAdapter(),
    PanvelAdapter(),
    VenancioAdapter(),
    DrogalAdapter(),
    AraujoAdapter(),
]

# campos que, se vierem de pelo menos um site, indicam um match confiável o
# bastante pra não precisar do Claude - sem eles (ex: só nome + imagem, caso
# de araujo/ultrafarma) não dá pra confiar no produto certo foi encontrado
CAMPOS_CONFIANCA = ("ms_register", "active_ingredient")

# "ms_register" às vezes vem preenchido com um texto de placeholder em vez
# de um registro de verdade (produto não-medicamento, isento de registro na
# ANVISA) - sem filtrar isso, o valor era tratado como "achou registro_ms" e
# classificava sabonete/esmalte/meia como Medicamento por engano, o que
# também derrubava a imagem (zerada pra medicamento).
REGISTRO_MS_PLACEHOLDERS = {
    "ISENTO", "N/A", "NA", "NAO SE APLICA", "NÃO SE APLICA",
    "NAO POSSUI", "NÃO POSSUI", "-", "0", "",
}


def _registro_ms_valido(valor):
    if not valor:
        return None
    if str(valor).strip().upper() in REGISTRO_MS_PLACEHOLDERS:
        return None
    return valor


def _consolidar_resultados(ean, brutos):
    """Aplica adapters na ORDEM de prioridade (não na ordem de chegada)."""
    resultado = {
        "ean": ean,
        "_nomes": [],
        "_fontes_ean_conferido": [],
        "_tarja_fonte": None,
    }
    fontes = []
    for adapter in ADAPTERS_EM_ORDEM:
        r = brutos.get(adapter.name)
        if r is None:
            continue
        achou_campo = False
        for campo, valor in vars(r).items():
            if campo in ("ean", "ean_conferido") or not valor:
                continue
            # sites às vezes devolvem link/asset de mockup/placeholder
            # (ex: sara_mockup.webp) em vez de um dado real do produto -
            # nunca gravar isso na base, seja qual for o campo ou adapter
            if isinstance(valor, str) and "mockup" in valor.lower():
                continue
            achou_campo = True
            if campo == "name":
                if not resultado.get("name"):
                    resultado["name"] = valor
                continue
            if campo == "tarja" and not resultado.get("tarja"):
                resultado["_tarja_fonte"] = adapter.name
            if not resultado.get(campo):
                resultado[campo] = valor
        if getattr(r, "ean_conferido", False):
            resultado["_fontes_ean_conferido"].append(adapter.name)
            if r.name:
                resultado["_nomes"].append(r.name)
        if achou_campo:
            fontes.append(adapter.name)
    resultado["ms_register"] = _registro_ms_valido(resultado.get("ms_register"))
    return resultado, fontes


def _rodar_adapters(ean, adapters, max_workers):
    brutos = {}
    if not adapters:
        return brutos
    workers = max(1, min(max_workers, len(adapters)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(a.search, ean): a.name for a in adapters}
        for future in as_completed(futures):
            nome_site = futures[future]
            try:
                r = future.result()
            except Exception:
                r = None
            if r is not None:
                brutos[nome_site] = r
    return brutos


def buscar_no_crawler(
    ean,
    max_workers=len(ADAPTERS_EM_ORDEM),
    parar_quando=None,
):
    """
    Consulta sites por EAN e consolida por prioridade (primeiro valor
    não-vazio de cada campo, na ordem de ADAPTERS_EM_ORDEM).

    Roda em duas ondas: Sara (bulário, mais confiável e só medicamento)
    primeiro; o restante em paralelo só se parar_quando não fechar o
    cadastro. Sem isso, todo EAN batia em 9 farmácias mesmo quando o
    bulário já tinha registro_ms/tarja. parar_quando(resultado, fontes)
    default é eh_confiavel.

    Retorna (resultado: dict, fontes: list[str]) - resultado nunca é None,
    mas fica vazio se ninguém encontrar.
    """
    if parar_quando is None:
        parar_quando = eh_confiavel

    sara, *demais = ADAPTERS_EM_ORDEM
    brutos = _rodar_adapters(ean, [sara], max_workers=1)
    resultado, fontes = _consolidar_resultados(ean, brutos)
    if parar_quando(resultado, fontes):
        if resultado.get("description") or resultado.get("short_description"):
            return resultado, fontes
        # bulário fecha o cadastro regulatório, mas não traz texto de
        # e-commerce - 2 farmácias bastam pra descrição, sem varrer as 9
        so_descricao = [a for a in demais if a.name in ("drogasil", "drogaraia")]
        brutos.update(_rodar_adapters(ean, so_descricao, max_workers))
        return _consolidar_resultados(ean, brutos)

    brutos.update(_rodar_adapters(ean, demais, max_workers))
    return _consolidar_resultados(ean, brutos)


# nº mínimo de sites concordando no nome pra aceitar produto sem
# registro_ms/princípio_ativo (não-medicamento nunca tem esses campos) -
# concordância entre fontes independentes é o sinal de confiança nesse caso
FONTES_MINIMAS_SEM_CAMPOS_REGULATORIOS = 2


def _nomes_concordam(nomes):
    """True se pelo menos 2 nomes batem (um contém o outro ou similaridade
    de sequência >= 0.55). Nomes demais divergentes = outro produto."""
    chaves = []
    for nome in nomes:
        chave = re.sub(r"\s+", " ", (nome or "")).strip().upper()
        if chave:
            chaves.append(chave)
    if len(chaves) < 2:
        return False
    base = chaves[0]
    for outro in chaves[1:]:
        if base in outro or outro in base:
            continue
        if difflib.SequenceMatcher(None, base, outro).ratio() < 0.55:
            return False
    return True


def eh_confiavel(resultado, fontes):
    """
    Medicamento: exige registro_ms ou princípio_ativo (campos que só site com
    ficha técnica farmacêutica de verdade expõe). Não-medicamento nunca tem
    esses campos, então usa como sinal de confiança 2+ sites com EAN
    conferido E nomes parecidos - se fosse medicamento de verdade, pelo
    menos um deles teria mostrado registro_ms. Sem conferir EAN/nome, dois
    "primeiros resultados" de busca (Araujo/Panvel) fechavam o cadastro
    errado.
    """
    if any(resultado.get(c) for c in CAMPOS_CONFIANCA):
        return True
    conferidos = resultado.get("_fontes_ean_conferido") or []
    if len(conferidos) < FONTES_MINIMAS_SEM_CAMPOS_REGULATORIOS:
        return False
    return _nomes_concordam(resultado.get("_nomes") or [])


# categoria/breadcrumb do site (campo "category" do crawler) indicando que o
# produto está na seção de remédios da farmácia - sinal de tipo_cadastro mais
# confiável que vasculhar a descrição livre: description menciona a palavra
# "medicamento" até em produto que NÃO é medicamento (ex: seringa descrita
# como "para aplicação de medicamentos", espaçador de bombinha, hastes
# flexíveis "para aplicação de medicamentos e remoção de maquiagem" - visto
# em produtos reais), então usar isso derrubaria a precisão em vez de
# melhorar. category já é o próprio site dizendo em que prateleira o produto
# está, não uma menção incidental.
_CATEGORIA_INDICA_MEDICAMENTO_RE = re.compile(r"rem[eé]dio|medicamento", re.IGNORECASE)


def _indica_medicamento(resultado):
    """
    True se o crawler achou algum sinal de que o produto é medicamento, mesmo
    quando ms_register não veio exposto no HTML (acontece bastante - visto em
    Sigmaliv/Desloratadina, Paracetamol e Fluibron/Ambroxol, todos raspados
    com sucesso mas sem ms_register, e por isso classificados como "Não
    Medicamento" antes desta checagem existir). active_ingredient e tarja já
    eram o mesmo sinal de confiança usado em eh_confiavel (CAMPOS_CONFIANCA).
    prescricao_detalhe (exigência de receita) e category (breadcrumb do
    site, ex: "Remédios") só vêm preenchidos pelo site quando o produto está
    cadastrado como medicamento na loja - nenhum dos dois era usado até
    agora, apesar de já vir raspado pelos adapters.
    """
    if any(
        resultado.get(c)
        for c in ("ms_register", "active_ingredient", "tarja", "prescricao_detalhe")
    ):
        return True
    return bool(_CATEGORIA_INDICA_MEDICAMENTO_RE.search(resultado.get("category") or ""))


def mapear_para_schema(resultado, fontes, client, model):
    """
    Traduz o resultado consolidado do crawler (campos em inglês, modelo
    ProductResult) para o schema da planilha (campos em português).

    Título bruto do site, categoria (a do site não bate com nossa árvore) e
    descrição comercial passam por UMA chamada de texto puro (sem busca)
    usando só os fatos já confirmados pelo crawler. Retorna (data, usage).
    """
    tipo_cadastro = "Medicamento" if _indica_medicamento(resultado) else "Não Medicamento"
    # description (texto completo do produto) tem informação real de verdade;
    # short_description costuma ser só meta-tag de SEO/propaganda ("Compre...
    # Entrega Rápida... Aproveite!"), que é 100% removida pela regra "sem
    # termos comerciais" - usar ela primeiro deixava a descrição final quase
    # vazia mesmo quando o site tinha um texto completo bem melhor
    descricao_bruta = resultado.get("description") or resultado.get("short_description")

    formatados, usage = ep.formatar_campos_confirmados(
        client,
        model,
        tipo_cadastro,
        resultado.get("brand"),
        resultado.get("active_ingredient"),
        resultado.get("quantity") or resultado.get("dosage"),
        resultado.get("name"),
        categoria_bruta=resultado.get("category"),
        descricao_bruta=descricao_bruta,
        fabricante=resultado.get("manufacturer") or resultado.get("brand"),
    )

    data = {
        "titulo": formatados.get("titulo") or resultado.get("name"),
        "marca": resultado.get("brand"),
        "fabricante": resultado.get("manufacturer") or resultado.get("brand"),
        "tipo_cadastro": tipo_cadastro,
        "registro_ms": resultado.get("ms_register"),
        "generico": resultado.get("generico"),
        "tarja": resultado.get("tarja"),
        "principios_ativos": resultado.get("active_ingredient"),
        "descricao_curta": formatados.get("descricao_curta"),
        "frase_obrigatoria": None,
        "departamento": formatados.get("departamento"),
        "categoria": formatados.get("categoria"),
        "subcategoria": formatados.get("subcategoria"),
        "origem_categorizacao": "ia",  # crawler não tem de-para ainda
        "imagem_url": None if tipo_cadastro == "Medicamento" else resultado.get("image1"),
        "pagina_produto_url": resultado.get("url"),
        # mesmo quando o crawler acha o produto, título/categoria/descrição
        # ainda passam por uma chamada ao Claude (sem busca) - o rótulo deixa
        # isso explícito, em vez de sugerir que nenhum token de LLM foi gasto
        "origem_enriquecimento": f"crawler+claude ({','.join(fontes)})",
        "confirmado_anvisa_cmed": "Não",
        "tarja_confirmada_bulario": (
            "Sim" if resultado.get("_tarja_fonte") == "sara" else "Não"
        ),
    }

    # validar_categorizacao (dentro de apply_safety_checks) zera departamento/
    # categoria/subcategoria se a combinação não existir na árvore oficial -
    # rede de segurança contra a classificação acima ter errado
    data = ep.apply_safety_checks(data, resultado.get("ean"))
    data["model"] = model
    return ep.marcar_validacao_humana(data), usage


def _normalizado(texto):
    return re.sub(r"\s+", " ", (texto or "")).strip().upper()


# BRAND da IQVIA sempre vem como "NOME (CODIGO)" - o código entre parênteses
# é um identificador interno do parceiro (fabricante/corporação), não faz
# parte do nome comercial (ex: "AAS PROTECT (H1Y)" -> "Aas Protect")
_SUFIXO_CODIGO_IQVIA_RE = re.compile(r"\s*\([^()]*\)\s*$")


def _limpar_marca_iqvia(brand):
    sem_sufixo = _SUFIXO_CODIGO_IQVIA_RE.sub("", brand or "").strip()
    return sem_sufixo.title() or None


def _resolver_tarja_e_retencao_cmed(tarja_bruta, principios_ativos):
    """
    Decide tarja final e precisa_retencao_receita a partir do valor BRUTO de
    tarja da CMED (antes de cmed.TARJA_CMED_PARA_SCHEMA) - regra combinada
    com o time de negócio:
    - "Tarja Preta" -> retenção "Sim" (sozinha já basta, sempre exige).
    - "Sem Tarja" -> retenção "Não", sem consultar substancias_controladas -
      venda livre de verdade não precisa dessa checagem extra.
    - Valor não reconhecido pela CMED (ex: "- (*)", tarja não informada) ->
      tarja e retenção ficam None - só isso já joga a linha pra validação
      humana (ver ep.marcar_validacao_humana), sem tentar adivinhar nada.
    - Qualquer outro valor reconhecido ("Tarja Vermelha", e "Tarja Vermelha
      sob restrição" que TARJA_CMED_PARA_SCHEMA já normaliza pra Vermelha)
      -> cruza o princípio ativo com substancias_controladas (Portaria 344 /
      IN 360-RDC 471 - antimicrobianos e GLP-1 exigem retenção mesmo em
      Tarja Vermelha comum): achou vira retenção "Sim", não achou vira
      retenção "Não" - a tarja final é Tarja Vermelha nos dois casos.

    Retorna (tarja, precisa_retencao_receita).
    """
    tarja_normalizada = cmed.TARJA_CMED_PARA_SCHEMA.get(tarja_bruta)
    if tarja_normalizada == "Tarja Preta":
        return "Tarja Preta", "Sim"
    if tarja_normalizada == "Sem Tarja":
        return "Sem Tarja", "Não"
    if tarja_normalizada is None:
        return None, None
    # só sobra "Tarja Vermelha" (a normalização da CMED não tem outro valor)
    achou = substancias_controladas.substancia_esta_controlada(principios_ativos)
    return "Tarja Vermelha", ("Sim" if achou else "Não")


def mapear_cmed_para_schema(medicamento, ean, client, model):
    """
    Traduz o resultado da tabela oficial da ANVISA (CMED) pro schema da
    planilha. tipo_cadastro é sempre "Medicamento" (é o próprio motivo de
    existir na CMED) e tarja/registro_ms/fabricante vêm direto da fonte
    oficial, sem precisar da segunda verificação dedicada que o resto do
    fluxo faz (ver verify_tarja_registro em enrich_produtos.py) - já são
    dados confirmados pela ANVISA, não uma página de terceiro.

    titulo/departamento-categoria-subcategoria (e descricao_curta, se algum
    site trouxer texto bruto) passam por UMA chamada leve ao Claude, sem
    busca. principios_ativos no caso comum sai do parser da CMED, sem token.
    Os campos regulatórios (tarja/registro_ms/fabricante) já vêm confirmados
    pela ANVISA, então o crawler aqui não precisa varrer as 9 farmácias como
    no fluxo crawler+claude - só Sara e, se preciso, drogasil/drogaraia
    (mesmo corte usado em mapear_abcfarma_para_schema pra descrição, ver
    buscar_no_crawler). Sem texto bruto de nenhum site, descricao_curta
    continua null (nunca inferida a partir da indicação terapêutica).

    Retorna (data, usage).
    """
    substancia = medicamento["substancia"]
    produto = medicamento["produto"]
    apresentacao = medicamento["apresentacao"]

    # tipo_produto da CMED já diz com certeza se é genérico - é a mesma
    # coluna que db.py usa pra decidir o campo `generico` (ver
    # verificar_cmed) - então não precisa adivinhar por comparação de nome.
    # Isso importa porque a comparação normalizada abaixo falha em
    # combinações: substância "AMOXICILINA TRIHIDRATADA;CLAVULANATO DE
    # POTÁSSIO" (separador ";", com "TRIHIDRATADA") vs produto "AMOXICILINA
    # + CLAVULANATO DE POTÁSSIO" (separador "+", sem "TRIHIDRATADA") são o
    # mesmo genérico, mas nomes/separadores diferentes fazem a comparação
    # concluir "tem marca própria", preenchendo marca com o nome do
    # princípio ativo e quebrando o formato "[Fabricante] Genérico" no
    # título (ver FORMAT_CAMPOS_SYSTEM em enrich_produtos.py).
    if medicamento["tipo_produto"] == "Genérico":
        marca = None
    else:
        # normaliza sem sufixo de hidratação antes de comparar - produto às
        # vezes vem sem o estado de hidratação que a substancia traz (ex:
        # "CLORIDRATO DE ONDANSETRONA" vs "CLORIDRATO DE ONDANSETRONA
        # DI-HIDRATADO"), e uma comparação ingênua tratava isso como nomes
        # diferentes, preenchendo marca com o nome do sal por engano
        marca = (
            produto.title()
            if ep.normalizar_nome_substancia_cmed(produto)
            != ep.normalizar_nome_substancia_cmed(substancia)
            else None
        )

    principios_ativos, usage = ep.formatar_composicao_cmed(
        client, model, substancia, apresentacao
    )

    tarja_final, retencao_final = _resolver_tarja_e_retencao_cmed(
        medicamento["tarja"], principios_ativos
    )

    # classe_terapeutica sozinha (ex: "M2A - ANTIRREUMÁTICOS E ANALGÉSICOS
    # TÓPICOS") levava o modelo a categorizar fitoterápicos (ex: Acheflan/
    # Cordia Verbenacea) ora em "Dor e Febre", ora em "Fitoterápicos e
    # naturais" - instável mesmo rodando o mesmo produto várias vezes,
    # porque faltava dizer que a CMED já classifica esse produto como
    # Fitoterápico (tipo_produto), não só a classe terapêutica. Passar essa
    # dica junto estabilizou 100% num teste com 6 repetições.
    categoria_bruta = medicamento["classe_terapeutica"]
    if medicamento["tipo_produto"] == "Fitoterápico":
        categoria_bruta = f"{categoria_bruta} (tipo_produto CMED: Fitoterápico)"

    # de-para revisado por humano (ver mapear_categorias_cmed.py) tem
    # prioridade sobre a categorização da IA - mesma lógica do caminho IQVIA
    categoria_mapeada = cmed.buscar_categoria_mapeada(categoria_bruta)

    # só pra texto comercial (descricao_curta) - tarja/registro_ms já são
    # verdade absoluta pela CMED, então não precisa do critério de
    # confiança (eh_confiavel) nem da varredura completa das 9 farmácias:
    # para tão logo ache uma descrição (Sara, ou drogasil/drogaraia se o
    # bulário não trouxer texto de e-commerce).
    resultado_crawler, _fontes_crawler = buscar_no_crawler(
        ean, parar_quando=lambda _r, _f: True
    )
    descricao_bruta = (
        resultado_crawler.get("description") or resultado_crawler.get("short_description")
    )

    formatados, usage_fmt = ep.formatar_campos_confirmados(
        client,
        model,
        "Medicamento",
        marca,
        principios_ativos,
        apresentacao,
        produto,
        categoria_bruta=categoria_bruta,
        descricao_bruta=descricao_bruta,
        fabricante=medicamento["laboratorio"],
    )
    usage["tokens"] += usage_fmt["tokens"]
    usage["cache_creation"] += usage_fmt["cache_creation"]
    usage["cache_read"] += usage_fmt["cache_read"]

    data = {
        "titulo": formatados.get("titulo") or produto.title(),
        "marca": marca,
        "fabricante": medicamento["laboratorio"],
        "tipo_cadastro": "Medicamento",
        "registro_ms": medicamento["registro"],
        "generico": "Sim" if medicamento["tipo_produto"] == "Genérico" else "Não",
        "tarja": tarja_final,
        "precisa_retencao_receita": retencao_final,
        "principios_ativos": principios_ativos,
        "descricao_curta": formatados.get("descricao_curta"),
        "frase_obrigatoria": None,
        "departamento": categoria_mapeada["departamento"] if categoria_mapeada else formatados.get("departamento"),
        "categoria": categoria_mapeada["categoria"] if categoria_mapeada else formatados.get("categoria"),
        "subcategoria": categoria_mapeada["subcategoria"] if categoria_mapeada else formatados.get("subcategoria"),
        "origem_categorizacao": "mapeamento_cmed" if categoria_mapeada else "ia",
        # medicamento nunca leva imagem (apply_safety_checks reforça).
        "imagem_url": None,
        "pagina_produto_url": None,
        "origem_enriquecimento": f"{cmed.ORIGEM_ANVISA_CMED} (GGREM {medicamento['codigo_ggrem']})",
        "confirmado_anvisa_cmed": "Sim",
    }

    # validar_categorizacao (dentro de apply_safety_checks) zera departamento/
    # categoria/subcategoria se a combinação não existir na árvore oficial -
    # rede de segurança contra a classificação acima ter errado
    data = ep.apply_safety_checks(data, ean)
    data["model"] = model
    return ep.marcar_validacao_humana(data), usage


def mapear_abcfarma_para_schema(medicamento, ean, client, model, verify_tarja=True):
    """
    Traduz o resultado da base ABCFarma pro schema da planilha. Só é chamada
    quando a CMED não achou o EAN (ver worker) - segunda fonte de certeza de
    que o produto É medicamento (ver abcfarma.py), mas com uma lacuna: essa
    base não tem coluna de tarja (a CMED tem). Os outros campos regulatórios
    (registro_ms, generico, princípio ativo, fabricante) são confirmados
    normalmente, direto da tabela.

    Pra tarja, tenta em camadas, da mais barata pra mais cara (mesma lógica
    do resto do projeto):
    1. Crawler (grátis) - Sara primeiro; se o bulário já trouxer tarja,
       não consulta as outras farmácias. Se algum site trouxer tarja pra
       esse EAN, usa direto. Já temos registro_ms/princípio_ativo
       confirmados pela ABCFarma, então o crawler aqui parte de um sinal
       de confiança pelo menos tão forte quanto o exigido em
       eh_confiavel/CAMPOS_CONFIANCA pro caminho crawler+claude - mesma
       régua, não uma exceção nova.
    2. Se o crawler não achar, verificação dedicada via busca na internet
       (ep.verify_tarja_registro) - só quando verify_tarja=True. Mesma
       função e mesmo critério (só aceita se confirmado=True numa fonte
       oficial) já usados no fluxo "Claude puro".
    Se as duas falharem, tarja fica null e a linha vai pra fila de
    validação humana (ver ep.marcar_validacao_humana).

    titulo/departamento-categoria-subcategoria (e descricao_curta se o
    crawler trouxer texto bruto) passam por UMA chamada leve ao Claude, sem
    busca, igual à CMED. principio_ativo separa substâncias por "+" (não
    ";" como a CMED) - ver ep.formatar_composicao_abcfarma.

    Retorna (data, usage).
    """
    principio_ativo = medicamento["principio_ativo"]
    descricao_produto = medicamento["descricao_produto"]
    apresentacao = medicamento["apresentacao"]

    marca = (
        descricao_produto.title()
        if _normalizado(descricao_produto) != _normalizado(principio_ativo)
        else None
    )

    principios_ativos, usage = ep.formatar_composicao_abcfarma(
        client, model, principio_ativo, apresentacao
    )

    resultado_crawler, _fontes_crawler = buscar_no_crawler(
        ean, parar_quando=lambda r, _f: bool(r.get("tarja"))
    )
    # description (texto completo) primeiro - ver mapear_para_schema acima
    descricao_bruta = (
        resultado_crawler.get("description") or resultado_crawler.get("short_description")
    )
    # tarja do crawler já vem com URL própria (resultado_crawler["url"]) -
    # guarda os dois juntos: usar a tarja sem a página que a sustenta violaria
    # a mesma regra que apply_safety_checks aplica pro resto do fluxo (tarja
    # sem pagina_produto_url é zerada automaticamente - ver lá).
    tarja_crawler = resultado_crawler.get("tarja")
    pagina_produto_url = resultado_crawler.get("url") if tarja_crawler else None
    tarja_confirmada_bulario = (
        "Sim" if resultado_crawler.get("_tarja_fonte") == "sara" else "Não"
    )

    formatados, usage_fmt = ep.formatar_campos_confirmados(
        client,
        model,
        "Medicamento",
        marca,
        principios_ativos,
        apresentacao,
        descricao_produto,
        categoria_bruta=None,
        descricao_bruta=descricao_bruta,
        fabricante=medicamento["laboratorio"],
    )
    usage["tokens"] += usage_fmt["tokens"]
    usage["cache_creation"] += usage_fmt["cache_creation"]
    usage["cache_read"] += usage_fmt["cache_read"]

    data = {
        "titulo": formatados.get("titulo") or descricao_produto.title(),
        "marca": marca,
        "fabricante": medicamento["laboratorio"],
        "tipo_cadastro": "Medicamento",
        "registro_ms": medicamento["registro_anvisa"],
        "generico": "Sim" if medicamento["tipo_medicamento"] == "GENERICO" else "Não",
        "tarja": tarja_crawler,
        "principios_ativos": principios_ativos,
        "descricao_curta": formatados.get("descricao_curta"),
        "frase_obrigatoria": None,
        "departamento": formatados.get("departamento"),
        "categoria": formatados.get("categoria"),
        "subcategoria": formatados.get("subcategoria"),
        "origem_categorizacao": "ia",  # ABCFarma não tem de-para ainda
        # medicamento nunca leva imagem (apply_safety_checks reforça).
        "imagem_url": None,
        "pagina_produto_url": pagina_produto_url,
        "origem_enriquecimento": (
            f"{abcfarma.ORIGEM_ABCFARMA} (produto {medicamento['codigo_produto']})"
        ),
        "confirmado_anvisa_cmed": "Não",
        "tarja_confirmada_bulario": tarja_confirmada_bulario,
    }

    # validar_categorizacao (dentro de apply_safety_checks) zera departamento/
    # categoria/subcategoria se a combinação não existir na árvore oficial -
    # rede de segurança contra a classificação acima ter errado. Se
    # tarja_crawler veio com tarja fora do vocabulário fechado, também é
    # zerada aqui (ALLOWED_TARJA).
    data = ep.apply_safety_checks(data, ean)
    data["model"] = model

    # tarja ainda não confirmada (nem CMED, nem ABCFarma, nem crawler) -
    # última tentativa via busca dedicada na internet, mesma função e mesmo
    # critério do fluxo "Claude puro". Roda DEPOIS de apply_safety_checks de
    # propósito (mesma ordem de ep.call_model): senão a regra "tarja sem
    # pagina_produto_url é zerada" descartaria o valor recém-confirmado antes
    # mesmo dele ser usado, já que essa verificação dedicada não retorna URL.
    if verify_tarja and not data.get("tarja"):
        resultado_verif, usage_verif = ep.verify_tarja_registro(
            client, model, ean, data.get("titulo"), data.get("marca"), principios_ativos
        )
        usage["tokens"] += usage_verif["tokens"]
        usage["cache_creation"] += usage_verif["cache_creation"]
        usage["cache_read"] += usage_verif["cache_read"]

        if resultado_verif and resultado_verif.get("confirmado"):
            tarja_verificada = resultado_verif.get("tarja")
            if tarja_verificada is not None and tarja_verificada not in ep.ALLOWED_TARJA:
                print(
                    f"  [aviso] verificação dedicada devolveu tarja fora do "
                    f"vocabulário para EAN {ean} ({tarja_verificada!r}) - zerada."
                )
                tarja_verificada = None
            data["tarja"] = tarja_verificada
            # frase_obrigatoria depende da tarja - recompõe com o valor
            # atualizado, mesma função usada em todo o resto do fluxo
            data["frase_obrigatoria"] = ep.compor_frase_obrigatoria(data, tarja_verificada, True)
    return ep.marcar_validacao_humana(data), usage


def mapear_iqvia_para_schema(produto, ean, client, model, verify_tarja=True):
    """
    Traduz o resultado do catálogo IQVIA (iqvia.py) pro schema da planilha -
    terceira camada de referência, depois de CMED e ABCFarma (ver worker()).
    Ao contrário das duas, cobre não-medicamento também
    (SETOR_NEC_ABERTO NAO_MEDICAMENTO_* - ver iqvia.eh_medicamento): nesse
    caso segue direto pro ramo "Não Medicamento" da formatação, sem
    preocupação nenhuma de tarja/registro_ms.

    Pra medicamento, SETOR_NEC_ABERTO já diz "precisa receita" (RX_*) vs
    "isento de prescrição" (MIP_*) - MIP é uma categoria regulatória oficial
    (não uma inferência de site), então tarja="Sem Tarja" é confirmada
    direto, sem gastar crawler nem token (ver tarja_confirmada_iqvia_mip,
    usado por ep.apply_safety_checks e ep.marcar_validacao_humana pra
    dispensar essa linha da fila de validação). RX não distingue Tarja
    Vermelha de Preta - tenta em camadas, igual ao caminho ABCFarma: crawler
    (Sara primeiro, varre as 9 farmácias só se ninguém confirmar tarja) e,
    se não achar, verificação dedicada via busca (ep.verify_tarja_registro).
    Se as duas falharem, tarja fica null e a linha vai pra fila de validação
    humana. IQVIA não tem coluna de registro_ms - fica null a menos que o
    crawler ou a verificação dedicada tragam um (dado ausente na fonte, não
    incerto).

    titulo/departamento-categoria-subcategoria (e descricao_curta, se algum
    site trouxer texto bruto) passam por UMA chamada leve ao Claude, sem
    busca, igual à CMED - usa a taxonomia própria da IQVIA
    (AREA_FARMACIA/SUB_CATx) como dica de categoria. principios_ativos vem
    de MOLECULA (separado por "|"), reaproveitando o parser da CMED (ver
    ep.formatar_composicao_iqvia).

    Retorna (data, usage).
    """
    setor = produto["setor_nec_aberto"]
    eh_medicamento = iqvia.eh_medicamento(setor)
    tipo_cadastro = "Medicamento" if eh_medicamento else "Não Medicamento"
    eh_generico = iqvia.eh_generico(setor)
    precisa_receita = iqvia.precisa_receita(setor)  # True=RX, False=MIP, None=não medicamento

    # generico não tem marca própria - mesma convenção de mapear_cmed_para_schema
    marca = None if eh_generico else _limpar_marca_iqvia(produto["brand"])
    fabricante = produto["descricao_fabricante"] or produto["descricao_corporacao"]
    descricao_longa = produto["descricao_longa"]

    usage = ep.usage_vazio()
    principios_ativos = None
    # molecula vem preenchida na IQVIA mesmo para não-medicamento (ex:
    # "shampoo para bebes", ingredientes de cosmético/protetor solar) - só
    # é de fato "princípio ativo" (campo regulatório) quando o produto é
    # medicamento; sem essa checagem o campo saía preenchido indevidamente
    # em não-medicamento.
    if eh_medicamento and produto["molecula"]:
        principios_ativos, usage = ep.formatar_composicao_iqvia(
            client, model, produto["molecula"], descricao_longa
        )

    tarja = None
    tarja_confirmada_iqvia_mip = "Não"
    tarja_confirmada_bulario = "Não"
    pagina_produto_url = None
    registro_ms = None

    if precisa_receita is False:
        # MIP = Medicamento Isento de Prescrição - categoria regulatória
        # oficial do IQVIA, não inferência de site - confirma tarja direto
        tarja = "Sem Tarja"
        tarja_confirmada_iqvia_mip = "Sim"
        resultado_crawler, _fontes_crawler = buscar_no_crawler(
            ean, parar_quando=lambda _r, _f: True
        )
    elif precisa_receita is True:
        # RX não distingue Vermelha de Preta - mesma lógica de tarja do
        # caminho ABCFarma (Sara primeiro; varre as 9 só se não confirmar)
        resultado_crawler, _fontes_crawler = buscar_no_crawler(
            ean, parar_quando=lambda r, _f: bool(r.get("tarja"))
        )
        tarja_crawler = resultado_crawler.get("tarja")
        if tarja_crawler:
            tarja = tarja_crawler
            pagina_produto_url = resultado_crawler.get("url")
            tarja_confirmada_bulario = (
                "Sim" if resultado_crawler.get("_tarja_fonte") == "sara" else "Não"
            )
        registro_ms = resultado_crawler.get("ms_register")
    else:
        # não-medicamento - crawler só pra texto de descrição, mesmo corte
        # leve da CMED (nunca a varredura completa das 9 farmácias)
        resultado_crawler, _fontes_crawler = buscar_no_crawler(
            ean, parar_quando=lambda _r, _f: True
        )

    descricao_bruta = (
        resultado_crawler.get("description") or resultado_crawler.get("short_description")
    )

    partes_categoria = [
        p
        for p in (
            produto["area_farmacia"],
            produto["sub_cat1"],
            produto["sub_cat2"],
            produto["sub_cat3"],
            produto["sub_cat4"],
        )
        if p
    ]
    categoria_bruta = " > ".join(partes_categoria) or None

    # de-para revisado por humano (ver mapear_categorias_iqvia.py) tem
    # prioridade sobre a categorização da IA - elimina a inconsistência de
    # rodada pra rodada pras combinações já mapeadas. Se não achar (ou ainda
    # não tiver sido revisada), cai no fluxo normal abaixo.
    categoria_mapeada = iqvia.buscar_categoria_mapeada(
        tipo_cadastro,
        produto["area_farmacia"],
        produto["sub_cat1"],
        produto["sub_cat2"],
        produto["sub_cat3"],
        produto["sub_cat4"],
    )

    formatados, usage_fmt = ep.formatar_campos_confirmados(
        client,
        model,
        tipo_cadastro,
        marca,
        principios_ativos,
        descricao_longa,
        descricao_longa,
        categoria_bruta=categoria_bruta,
        descricao_bruta=descricao_bruta,
        fabricante=fabricante,
    )
    usage["tokens"] += usage_fmt["tokens"]
    usage["cache_creation"] += usage_fmt["cache_creation"]
    usage["cache_read"] += usage_fmt["cache_read"]

    data = {
        "titulo": formatados.get("titulo") or descricao_longa.title(),
        "marca": marca,
        "fabricante": fabricante,
        "tipo_cadastro": tipo_cadastro,
        "registro_ms": registro_ms,
        "generico": ("Sim" if eh_generico else "Não") if eh_medicamento else None,
        "tarja": tarja,
        "principios_ativos": principios_ativos,
        "descricao_curta": formatados.get("descricao_curta"),
        "frase_obrigatoria": None,
        "departamento": categoria_mapeada["departamento"] if categoria_mapeada else formatados.get("departamento"),
        "categoria": categoria_mapeada["categoria"] if categoria_mapeada else formatados.get("categoria"),
        "subcategoria": categoria_mapeada["subcategoria"] if categoria_mapeada else formatados.get("subcategoria"),
        "origem_categorizacao": "mapeamento_iqvia" if categoria_mapeada else "ia",
        # IQVIA não tem coluna de imagem - crawler só preenche foto de
        # não-medicamento. Medicamento sai sem imagem (regra de negócio).
        "imagem_url": None if eh_medicamento else resultado_crawler.get("image1"),
        "pagina_produto_url": pagina_produto_url,
        "origem_enriquecimento": f"{iqvia.ORIGEM_IQVIA} (FCC {produto['fcc']})",
        "confirmado_anvisa_cmed": "Não",
        "tarja_confirmada_bulario": tarja_confirmada_bulario,
        "tarja_confirmada_iqvia_mip": tarja_confirmada_iqvia_mip,
    }

    # validar_categorizacao (dentro de apply_safety_checks) zera departamento/
    # categoria/subcategoria se a combinação não existir na árvore oficial -
    # rede de segurança contra a classificação acima ter errado
    data = ep.apply_safety_checks(data, ean)
    data["model"] = model

    # tarja de medicamento RX ainda não confirmada (nem IQVIA/MIP, nem
    # crawler) - última tentativa via busca dedicada, mesma função e mesmo
    # critério do fluxo ABCFarma/Claude puro. Roda DEPOIS de
    # apply_safety_checks de propósito (mesma ordem do caminho ABCFarma).
    if precisa_receita is True and verify_tarja and not data.get("tarja"):
        resultado_verif, usage_verif = ep.verify_tarja_registro(
            client, model, ean, data.get("titulo"), data.get("marca"), principios_ativos
        )
        usage["tokens"] += usage_verif["tokens"]
        usage["cache_creation"] += usage_verif["cache_creation"]
        usage["cache_read"] += usage_verif["cache_read"]

        if resultado_verif and resultado_verif.get("confirmado"):
            tarja_verificada = resultado_verif.get("tarja")
            if tarja_verificada is not None and tarja_verificada not in ep.ALLOWED_TARJA:
                print(
                    f"  [aviso] verificação dedicada devolveu tarja fora do "
                    f"vocabulário para EAN {ean} ({tarja_verificada!r}) - zerada."
                )
                tarja_verificada = None
            data["tarja"] = tarja_verificada
            if not data.get("registro_ms"):
                data["registro_ms"] = resultado_verif.get("registro_ms")
            data["frase_obrigatoria"] = ep.compor_frase_obrigatoria(data, tarja_verificada, True)
    return ep.marcar_validacao_humana(data), usage


def montar_pistas_nao_confirmadas(resultado, fontes):
    """
    Dados parciais do crawler para o Claude puro quando o match não fechou
    cadastro. Não inclui tarja nem registro_ms de propósito - são campos
    regulatórios e não podem ancorar o modelo a dado de farmácia não
    confiável. O prompt trata o resto como pista, nunca como fato.
    """
    if not resultado:
        return None
    campos = {
        "nome": resultado.get("name"),
        "marca": resultado.get("brand"),
        "fabricante": resultado.get("manufacturer"),
        "url": resultado.get("url"),
        "categoria_do_site": resultado.get("category"),
    }
    pistas = {chave: valor for chave, valor in campos.items() if valor}
    if not pistas:
        return None
    if fontes:
        pistas["sites"] = ", ".join(fontes)
    return pistas


def _avisar(args, etapa, mensagem):
    callback = getattr(args, "on_progress", None)
    if callback:
        callback(etapa, mensagem)


def worker(ean, nome_produto, args):
    usage_total = {"tokens": 0, "cache_creation": 0, "cache_read": 0}

    _avisar(args, "cmed", "Consultando CMED/ANVISA")
    medicamento_cmed = cmed.buscar_medicamento_anvisa(ean)
    if medicamento_cmed is not None:
        _avisar(args, "formatacao", "Formatando dados da CMED")
        data, usage = mapear_cmed_para_schema(medicamento_cmed, ean, CLIENT, args.model)
        return ean, nome_produto, data, usage

    _avisar(args, "abcfarma", "Consultando ABCFarma")
    medicamento_abcfarma = abcfarma.buscar_medicamento_abcfarma(ean)
    if medicamento_abcfarma is not None:
        _avisar(args, "formatacao", "Formatando dados da ABCFarma")
        data, usage = mapear_abcfarma_para_schema(
            medicamento_abcfarma,
            ean,
            CLIENT,
            args.model,
            verify_tarja=not args.sem_verificar_tarja,
        )
        return ean, nome_produto, data, usage

    _avisar(args, "iqvia", "Consultando IQVIA")
    produto_iqvia = iqvia.buscar_produto_iqvia(ean)
    if produto_iqvia is not None:
        _avisar(args, "formatacao", "Formatando dados da IQVIA")
        data, usage = mapear_iqvia_para_schema(
            produto_iqvia,
            ean,
            CLIENT,
            args.model,
            verify_tarja=not args.sem_verificar_tarja,
        )
        return ean, nome_produto, data, usage

    _avisar(args, "crawler", "Buscando em farmácias")
    resultado, fontes = buscar_no_crawler(ean)

    if eh_confiavel(resultado, fontes):
        _avisar(args, "formatacao", "Formatando dados do crawler")
        data, usage = mapear_para_schema(resultado, fontes, CLIENT, args.model)
        return ean, nome_produto, data, usage

    _avisar(args, "claude", "Buscando com Claude (pode demorar)")
    time.sleep(args.sleep)
    data, usage_claude = ep.call_model(
        CLIENT,
        args.model,
        ean,
        nome_produto,
        verify_images=args.verify_images,
        verify_tarja=not args.sem_verificar_tarja,
        pistas_nao_confirmadas=montar_pistas_nao_confirmadas(resultado, fontes),
    )
    for chave in usage_total:
        usage_total[chave] += usage_claude[chave]
    if data is not None:
        data["origem_enriquecimento"] = "claude"
        data["confirmado_anvisa_cmed"] = "Não"
        data = ep.marcar_validacao_humana(data)
    return ean, nome_produto, data, usage_total


def worker_reconciliar(ean, nome_produto, args):
    """
    Usado só por --reconciliar-cmed: revisita um produto JÁ concluído (vindo
    de crawler+claude ou claude puro, de uma execução anterior) e verifica
    se o EAN já está na tabela oficial da ANVISA hoje - a CMED é atualizada
    com o tempo, então um EAN que não estava lá numa execução passada pode
    estar agora. Só faz a consulta à CMED (grátis, sem token) - nunca cai
    pro crawler nem pro Claude, porque o objetivo aqui é só promover pra
    fonte oficial quando possível, não reprocessar do zero. Se a CMED ainda
    não tiver o EAN, retorna data=None - o chamador deve deixar o produto
    exatamente como está (nunca marcar "nao_localizado" num produto que já
    tinha um resultado válido de outra fonte).
    """
    medicamento_cmed = cmed.buscar_medicamento_anvisa(ean)
    if medicamento_cmed is None:
        return ean, nome_produto, None, {"tokens": 0, "cache_creation": 0, "cache_read": 0}

    data, usage = mapear_cmed_para_schema(medicamento_cmed, ean, CLIENT, args.model)
    return ean, nome_produto, data, usage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MODELO_PADRAO)
    parser.add_argument(
        "--eans",
        default=None,
        help="Lista de EANs específicos a (re)processar, separados por vírgula (ignora a fase atual)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Número máximo de produtos pendentes a processar")
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--verify-images", action="store_true")
    parser.add_argument("--sem-verificar-tarja", action="store_true")
    parser.add_argument(
        "--reconciliar-cmed",
        action="store_true",
        help=(
            "Em vez do fluxo normal, revisita produtos já concluídos que não "
            "vieram da anvisa_cmed (de execuções passadas, via crawler ou "
            "Claude) e verifica se o EAN já está na tabela oficial hoje - "
            "promove pra anvisa_cmed sem gastar token de crawler/Claude se "
            "achar; se não achar, deixa o produto exatamente como está."
        ),
    )
    args = parser.parse_args()

    try:
        garantir_cliente()
    except RuntimeError as exc:
        sys.exit(f"Erro: {exc}")

    eans_filtro = None
    if args.eans:
        eans_filtro = [e.strip() for e in args.eans.split(",") if e.strip()]

    conn = ep.conectar()
    try:
        if args.reconciliar_cmed:
            pendentes = ep.buscar_ja_ok_nao_cmed(conn, eans=eans_filtro, limit=args.limit)
            total = len(pendentes)
            print(f"{total} produto(s) já concluído(s) (não vindos da CMED) pra revisitar.")

            processed = 0
            promovidas = 0
            tokens_gastos = 0
            with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
                futures = {
                    pool.submit(worker_reconciliar, ean, nome_produto, args): ean
                    for ean, nome_produto in pendentes
                }
                for future in as_completed(futures):
                    ean, nome_produto, data, usage = future.result()
                    processed += 1
                    tokens_gastos += usage["tokens"]

                    if data is None:
                        print(f"[{processed}/{total}] EAN {ean} - {nome_produto} -> sem atualização (CMED ainda não tem)")
                    else:
                        ep.promover_cmed(conn, ean, data, usage)
                        promovidas += 1
                        print(f"[{processed}/{total}] EAN {ean} - {nome_produto} -> promovida pra anvisa_cmed "
                              f"({usage['tokens']} tokens)")

            print(
                f"Concluído. {promovidas} produto(s) promovido(s) pra anvisa_cmed "
                f"({tokens_gastos} tokens gastos, só nos promovidos)."
            )
            return

        pendentes = ep.buscar_pendentes(conn, eans=eans_filtro, limit=args.limit)
        total = len(pendentes)
        print(f"{total} produto(s) pendente(s) na tabela produtos.")

        processed = 0
        origem_counts = {"anvisa_cmed": 0, "abcfarma": 0, "crawler+claude": 0, "claude": 0}
        tokens_por_origem = {"anvisa_cmed": 0, "abcfarma": 0, "crawler+claude": 0, "claude": 0}
        revisao_humana = 0
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futures = {
                pool.submit(worker, ean, nome_produto, args): ean
                for ean, nome_produto in pendentes
            }
            for future in as_completed(futures):
                ean, nome_produto, data, usage = future.result()
                ep.salvar_resultado(conn, ean, data, usage)

                origem = "claude"
                status = ep.STATUS_NOT_FOUND
                if data is not None and data.get("titulo"):
                    status = ep.STATUS_OK
                    origem_bruta = str(data.get("origem_enriquecimento", ""))
                    if origem_bruta.startswith(cmed.ORIGEM_ANVISA_CMED):
                        origem = "anvisa_cmed"
                    elif origem_bruta.startswith(abcfarma.ORIGEM_ABCFARMA):
                        origem = "abcfarma"
                    elif origem_bruta.startswith("crawler"):
                        origem = "crawler+claude"
                    else:
                        origem = "claude"
                    origem_counts[origem] += 1
                    if data.get(ep.VALIDACAO_HUMANA_COLUMN) == "Sim":
                        revisao_humana += 1

                tokens_por_origem[origem] += usage["tokens"]

                processed += 1
                revisao = ""
                if data and data.get(ep.VALIDACAO_HUMANA_COLUMN) == "Sim":
                    revisao = " | REVISÃO HUMANA"
                print(f"[{processed}/{total}] EAN {ean} - {nome_produto} -> {status} "
                      f"({data.get('origem_enriquecimento') if data else '-'} | {usage['tokens']} tokens{revisao})")

        print(
            f"Concluído. {origem_counts['anvisa_cmed']} via anvisa_cmed "
            f"({tokens_por_origem['anvisa_cmed']} tokens, tabela oficial ANVISA - sem busca, sem dupla verificação de tarja), "
            f"{origem_counts['abcfarma']} via abcfarma "
            f"({tokens_por_origem['abcfarma']} tokens, tabela ABCFarma - sem busca, tarja pendente de validação humana), "
            f"{origem_counts['crawler+claude']} via crawler+claude "
            f"({tokens_por_origem['crawler+claude']} tokens, só título/categoria/descrição - sem busca), "
            f"{origem_counts['claude']} via Claude puro "
            f"({tokens_por_origem['claude']} tokens, com busca agentic completa, "
            f"{revisao_humana} medicamento(s) na fila de validação humana)."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
