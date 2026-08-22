from __future__ import annotations

from fastapi import APIRouter, status

from app.schemas.enriquecimento import EnriquecerPedido, EnriquecerResposta, JobStatus
from app.services import enriquecimento as enriquecimento_service

router = APIRouter(tags=["enriquecimento"])


@router.post(
    "/enriquecer/jobs",
    response_model=JobStatus,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Dispara o enriquecimento e devolve um job_id",
    description=(
        "Retorna na hora. Acompanhe com GET /api/v1/jobs/{job_id} "
        "(vá atualizando até status=concluido ou erro)."
    ),
)
def iniciar_job(pedido: EnriquecerPedido) -> JobStatus:
    return enriquecimento_service.iniciar_job(pedido)


@router.get("/jobs/{job_id}", response_model=JobStatus)
def consultar_job(job_id: str) -> JobStatus:
    return enriquecimento_service.obter_job(job_id)


@router.post(
    "/enriquecer",
    response_model=EnriquecerResposta,
    summary="Enriquece e espera o resultado (sem progresso)",
    description="Trava até terminar. Preferível usar POST /enriquecer/jobs + GET /jobs/{id}. Timeout: 120s.",
)
def enriquecer(pedido: EnriquecerPedido) -> EnriquecerResposta:
    return enriquecimento_service.enriquecer(pedido)
