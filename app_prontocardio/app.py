from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app_prontocardio.database import (
    ensure_postgres_schema,
    run_postgres_migrations,
)
from app_prontocardio.routers import (
    app_glosas,
    autenticacao,
    livre,
    usuarios,
)
from app_prontocardio.settings import Settings

settings = Settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if settings.RUN_MIGRATIONS_ON_STARTUP:
        ensure_postgres_schema()
        run_postgres_migrations()
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
