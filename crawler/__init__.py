"""crawler — busca produto por EAN em sites de farmácia concorrentes.

Cada site vira um adapter em `adapters/` (um `search(ean)` que devolve um
`ProductResult` ou None). `enrich_com_crawler.py` roda todos em paralelo e
consolida por prioridade - ver `base.py` e `models.py`.

Copiado de e-delivery-cli/edelivery/scraper pra este projeto não depender
mais de um caminho externo (`../e-delivery-cli`) - ver histórico do projeto.
"""
