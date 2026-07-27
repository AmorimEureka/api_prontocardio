from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app_prontocardio.settings import Settings

settings = Settings()

oracle_thick_mode = (
    {'lib_dir': settings.ORACLE_CLIENT_LIB_DIR}
    if settings.ORACLE_CLIENT_LIB_DIR
    else settings.ORACLE_THICK_MODE
)

oracle_engine = create_engine(
    settings.ORACLE_DATABASE_URL,
    thick_mode=oracle_thick_mode,
    pool_pre_ping=True,
    pool_recycle=300,
)

postgres_engine = (
    create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
    )
    if settings.DATABASE_URL
    else None
)


def ensure_postgres_schema() -> None:
    if postgres_engine is None:
        return

    with postgres_engine.begin() as conn:
        conn.execute(
            text(f'CREATE SCHEMA IF NOT EXISTS "{settings.POSTGRES_SCHEMA}"')
        )


def run_postgres_migrations() -> None:
    if postgres_engine is None:
        return

    alembic_cfg = Config('alembic.ini')
    command.upgrade(alembic_cfg, 'head')


def get_session_oracle():
    with Session(oracle_engine) as session_oracle:
        yield session_oracle


def get_session_postgres():
    if postgres_engine is None:
        raise RuntimeError('DATABASE_URL não configurada.')

    with Session(postgres_engine) as session_postgres:
        yield session_postgres
