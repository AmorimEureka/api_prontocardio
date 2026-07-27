"""amplia descrição dos procedimentos da solicitação de nota

Revision ID: 20260726_034
Revises: 20260726_033
Create Date: 2026-07-26 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app_prontocardio.settings import Settings

revision: str = '20260726_034'
down_revision: str | Sequence[str] | None = '20260726_033'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_NAME = Settings().POSTGRES_SCHEMA


def upgrade() -> None:
    op.alter_column(
        'solicitacao_nota',
        'procedimento',
        existing_type=sa.String(length=500),
        type_=sa.Text(),
        existing_nullable=False,
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.alter_column(
        'solicitacao_nota',
        'procedimento',
        existing_type=sa.Text(),
        type_=sa.String(length=500),
        existing_nullable=False,
        schema=SCHEMA_NAME,
    )
