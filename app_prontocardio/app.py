import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Thread

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app_prontocardio.database import (
    ensure_postgres_schema,
    oracle_engine,
    postgres_engine,
    run_postgres_migrations,
)
from app_prontocardio.routers import (
    agendamentos,
    app_glosas,
    autenticacao,
    biq,
    farmacia,
    financeiro,
    institucional,
    livre,
    requisicoes,
    usuarios,
    whatsapp,
)
from app_prontocardio.services.remessas import (
    sincronizar_totais_remessas_financeiras,
)
from app_prontocardio.settings import Settings

settings = Settings()
logger = logging.getLogger(__name__)


def _sincronizar_totais_remessas_em_background() -> None:
    try:
        with (
            Session(postgres_engine) as session_postgres,
            Session(oracle_engine) as session_oracle,
        ):
            sincronizar_totais_remessas_financeiras(
                session_postgres,
                session_oracle,
            )
    except SQLAlchemyError:
        logger.warning(
            'Não foi possível sincronizar os totais das remessas '
            'com a HPC_V_CONTA_ATENDIMENTO durante a inicialização.',
            exc_info=True,
        )


def _iniciar_sincronizacao_totais_remessas() -> None:
    Thread(
        target=_sincronizar_totais_remessas_em_background,
        name='sincronizacao-totais-remessas',
        daemon=True,
    ).start()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if settings.RUN_MIGRATIONS_ON_STARTUP:
        ensure_postgres_schema()
        run_postgres_migrations()
        if postgres_engine is not None:
            _iniciar_sincronizacao_totais_remessas()
    yield


app = FastAPI(title='API Hospital Prontocardio 💙', lifespan=lifespan)

if settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials='*' not in settings.cors_allowed_origins,
        allow_methods=['*'],
        allow_headers=['*'],
    )

app.include_router(autenticacao.router)
app.include_router(livre.router)
app.include_router(usuarios.router)
app.include_router(app_glosas.router)
app.include_router(financeiro.router)
app.include_router(requisicoes.router)
app.include_router(agendamentos.router)
app.include_router(biq.router)
app.include_router(farmacia.router)
app.include_router(whatsapp.router)
app.include_router(institucional.router)
