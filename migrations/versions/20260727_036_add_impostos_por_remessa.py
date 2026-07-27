"""adiciona controle do total das retenções por vínculo de remessa

Revision ID: 20260727_036
Revises: 20260726_035
Create Date: 2026-07-27 19:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app_prontocardio.settings import Settings

revision: str = '20260727_036'
down_revision: str | Sequence[str] | None = '20260726_035'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_NAME = Settings().POSTGRES_SCHEMA


def upgrade() -> None:
    op.add_column(
        'conciliacoes_faturamento_remessas',
        sa.Column(
            'valor_impostos',
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text('0'),
        ),
        schema=SCHEMA_NAME,
    )
    op.create_check_constraint(
        'ck_conciliacoes_remessas_impostos_nao_negativo',
        'conciliacoes_faturamento_remessas',
        'valor_impostos >= 0',
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_constraint(
        'ck_conciliacoes_remessas_impostos_nao_negativo',
        'conciliacoes_faturamento_remessas',
        schema=SCHEMA_NAME,
        type_='check',
    )
    op.drop_column(
        'conciliacoes_faturamento_remessas',
        'valor_impostos',
        schema=SCHEMA_NAME,
    )
