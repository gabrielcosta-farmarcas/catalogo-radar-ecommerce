from __future__ import annotations

from app.repos import categorias as repo
from app.schemas.referencias import (
    ArvoreCategorias,
    CategoriaNo,
    DepartamentoNo,
    RamoCategorias,
    SubcategoriaNo,
)
from dominios import nome_tipo_produto


def arvore() -> ArvoreCategorias:
    linhas = repo.listar_linhas()
    ramos: dict[str, dict] = {}
    for cid, tipo, depto, cat, sub in linhas:
        deptos = ramos.setdefault(tipo, {})
        cats = deptos.setdefault(depto, {})
        cats.setdefault(cat, [])
        if sub and all(folha.nome != sub for folha in cats[cat]):
            cats[cat].append(SubcategoriaNo(id=cid, nome=sub))

    return ArvoreCategorias(
        ramos=[
            RamoCategorias(
                tipo_produto=tipo,
                nome=nome_tipo_produto(tipo) or tipo,
                departamentos=[
                    DepartamentoNo(
                        nome=depto,
                        categorias=[
                            CategoriaNo(nome=cat, subcategorias=subs)
                            for cat, subs in cats.items()
                        ],
                    )
                    for depto, cats in deptos.items()
                ],
            )
            for tipo, deptos in ramos.items()
        ]
    )
