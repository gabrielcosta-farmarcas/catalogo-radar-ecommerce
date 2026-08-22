from __future__ import annotations

import logging
import threading

import enrich_com_crawler as fluxo
import enrich_produtos as ep

from app import jobs
from app.db import get_conn
from app.ean import validar_ean
from app.errors import AppError, DependencyUnavailable
from app.jsonutil import json_limpo
from app.repos import produtos as produtos_repo
from app.schemas.common import Tokens
from app.schemas.enriquecimento import EnriquecerPedido, EnriquecerResposta, JobStatus
from app.schemas.produto import CadastroProduto
from app.services import produtos as produtos_service

logger = logging.getLogger(__name__)


def _tokens(usage: dict | None) -> Tokens:
    usage = usage or {}
    return Tokens(
        utilizados=int(usage.get("tokens") or 0),
        cache_gravados=int(usage.get("cache_creation") or 0),
        cache_lidos=int(usage.get("cache_read") or 0),
    )


def _status(data: dict | None) -> str:
    if data is None or not data.get("titulo"):
        return ep.STATUS_NOT_FOUND
    return ep.STATUS_OK


def enriquecer(pedido: EnriquecerPedido, on_progress=None) -> EnriquecerResposta:
    pedido = pedido.model_copy(update={"ean": validar_ean(pedido.ean)})

    def progresso(etapa: str, mensagem: str) -> None:
        logger.info("ean=%s etapa=%s %s", pedido.ean, etapa, mensagem)
        if on_progress:
            on_progress(etapa, mensagem)

    try:
        fluxo.garantir_cliente()
    except RuntimeError as exc:
        raise DependencyUnavailable(str(exc)) from exc

    logger.info("enriquecendo ean=%s", pedido.ean)
    try:
        _ean, nome, data, usage = fluxo.enriquecer_ean(
            pedido.ean,
            pedido.nome_produto,
            model=pedido.model,
            verify_images=pedido.verify_images,
            sem_verificar_tarja=pedido.sem_verificar_tarja,
            on_progress=progresso,
        )
    except AppError:
        raise
    except Exception as exc:
        logger.exception("falha no enriquecimento ean=%s", pedido.ean)
        raise DependencyUnavailable(f"Falha no enriquecimento: {exc}") from exc

    salvo = False
    produto = None
    if pedido.salvar:
        progresso("salvando", "Gravando no banco")
        try:
            with get_conn() as conn:
                produtos_repo.upsert(conn, pedido.ean, nome)
                ep.salvar_resultado(conn, pedido.ean, data, usage)
            salvo = True
            produto = produtos_service.obter(pedido.ean)
        except AppError:
            raise
        except Exception as exc:
            logger.exception("enriquecimento ok, persistência falhou ean=%s", pedido.ean)
            raise DependencyUnavailable(
                f"Enriquecimento ok, mas não gravou no Postgres: {exc}"
            ) from exc

    cadastro = None
    if data:
        cadastro = CadastroProduto.model_validate(json_limpo(data))

    return EnriquecerResposta(
        ean=pedido.ean,
        nome_produto=nome,
        status=_status(data),
        origem=None if data is None else data.get("origem_enriquecimento"),
        cadastro=cadastro,
        tokens=_tokens(usage),
        salvo=salvo,
        produto=produto,
    )


def iniciar_job(pedido: EnriquecerPedido) -> JobStatus:
    pedido = pedido.model_copy(update={"ean": validar_ean(pedido.ean)})
    job = jobs.criar(ean=pedido.ean, nome_produto=pedido.nome_produto)
    threading.Thread(
        target=_rodar_job,
        args=(job["job_id"], pedido),
        daemon=True,
        name=f"enriquecer-{pedido.ean}",
    ).start()
    return JobStatus.model_validate(job)


def obter_job(job_id: str) -> JobStatus:
    return JobStatus.model_validate(jobs.obter(job_id))


def _rodar_job(job_id: str, pedido: EnriquecerPedido) -> None:
    jobs.atualizar(job_id, status="rodando", etapa="cmed", mensagem="Iniciando")

    def on_progress(etapa: str, mensagem: str) -> None:
        jobs.atualizar(job_id, status="rodando", etapa=etapa, mensagem=mensagem)

    try:
        resultado = enriquecer(pedido, on_progress=on_progress)
        jobs.atualizar(
            job_id,
            status="concluido",
            etapa="concluido",
            mensagem="Concluído",
            resultado=resultado.model_dump(mode="json"),
        )
    except Exception as exc:
        logger.exception("job %s falhou ean=%s", job_id, pedido.ean)
        jobs.atualizar(
            job_id,
            status="erro",
            etapa="erro",
            mensagem=str(exc),
            erro=str(exc),
        )


def enriquecer_cadastrado(
    ean: str,
    *,
    sem_verificar_tarja: bool = False,
    verify_images: bool = False,
    model: str | None = None,
) -> EnriquecerResposta:
    atual = produtos_service.obter(ean)
    pedido = EnriquecerPedido(
        ean=atual.ean,
        nome_produto=atual.nome_produto,
        salvar=True,
        sem_verificar_tarja=sem_verificar_tarja,
        verify_images=verify_images,
        model=model or fluxo.MODELO_PADRAO,
    )
    return enriquecer(pedido)
