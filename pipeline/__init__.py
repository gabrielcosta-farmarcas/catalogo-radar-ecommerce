"""Núcleo de enriquecimento extraído dos scripts de lote.

CLI (`enrich_com_crawler.py`, `enrich_produtos.py`) e a API (`app/`)
consomem este pacote. Regras determinísticas, policies e prompts por tipo
vivem aqui; a persistência e as chamadas Anthropic ainda passam pelos
módulos de lote enquanto o strangler termina.
"""
