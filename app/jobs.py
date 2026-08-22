from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from app.errors import NotFoundError

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}

PESO = {
    "na_fila": 0,
    "cmed": 10,
    "abcfarma": 25,
    "iqvia": 40,
    "crawler": 55,
    "claude": 75,
    "formatacao": 88,
    "salvando": 95,
    "concluido": 100,
    "erro": 100,
}


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def criar(*, ean: str, nome_produto: str) -> dict:
    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "ean": ean,
        "nome_produto": nome_produto,
        "status": "na_fila",
        "etapa": "na_fila",
        "mensagem": "Na fila",
        "progresso": 0,
        "iniciado_em": _agora(),
        "atualizado_em": _agora(),
        "resultado": None,
        "erro": None,
    }
    with _LOCK:
        _JOBS[job_id] = job
    return dict(job)


def obter(job_id: str) -> dict:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise NotFoundError(f"Job {job_id} não encontrado.")
        return dict(job)


def atualizar(job_id: str, **campos) -> dict:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise NotFoundError(f"Job {job_id} não encontrado.")
        job.update(campos)
        if "etapa" in campos:
            job["progresso"] = PESO.get(campos["etapa"], job.get("progresso", 0))
        job["atualizado_em"] = _agora()
        return dict(job)
