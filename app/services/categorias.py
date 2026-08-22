from __future__ import annotations

from app.repos import categorias as repo
from app.schemas.referencias import (
    ArvoreCategorias,
    CategoriaNo,
    DepartamentoNo,
    RamoCategorias,
)


def arvore() -> ArvoreCategorias:
    linhas = repo.listar_linhas()
    ramos: dict[str, dict] = {}
    for tipo, depto, cat, sub in linhas:
        deptos = ramos.setdefault(tipo, {})
        cats = deptos.setdefault(depto, {})
        cats.setdefault(cat, [])
        if sub and sub not in cats[cat]:
            cats[cat].append(sub)

    return ArvoreCategorias(
        ramos=[
            RamoCategorias(
                tipo_produto=tipo,
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
