"""adiciona permissões de telas aos usuários

Revision ID: 20260726_035
Revises: 20260726_034
Create Date: 2026-07-26 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app_prontocardio.permissions import TELAS_PADRAO_JSON
from app_prontocardio.settings import Settings

revision: str = '20260726_035'
down_revision: str | Sequence[str] | None = '20260726_034'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_NAME = Settings().POSTGRES_SCHEMA


def upgrade() -> None:
    op.add_column(
        'usuarios_api',
        sa.Column(
            'telas_permitidas',
            sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{TELAS_PADRAO_JSON}'::json"),
        ),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_column(
        'usuarios_api',
        'telas_permitidas',
        schema=SCHEMA_NAME,
    )
