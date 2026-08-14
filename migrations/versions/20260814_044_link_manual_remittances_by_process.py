"""link manual IPM remittances at process level

Revision ID: 20260814_044
Revises: 20260814_043
Create Date: 2026-08-14 15:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260814_044'
down_revision: Union[str, Sequence[str], None] = '20260814_043'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
TABLE_NAME = 'associacoes_remessas_ipm_manuais'


def upgrade() -> None:
    op.drop_constraint(
        'uq_assoc_remessa_ipm_manual_processo_nr',
        TABLE_NAME,
        schema=SCHEMA_NAME,
        type_='unique',
    )
    op.drop_column(TABLE_NAME, 'nr', schema=SCHEMA_NAME)
    op.create_unique_constraint(
        'uq_assoc_remessa_ipm_manual_processo_remessa',
        TABLE_NAME,
        ['numero_processo', 'competencia_producao', 'cd_remessa'],
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_assoc_remessa_ipm_manual_processo_remessa',
        TABLE_NAME,
        schema=SCHEMA_NAME,
        type_='unique',
    )
    op.add_column(
        TABLE_NAME,
        sa.Column('nr', sa.String(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.execute(
        f"UPDATE {SCHEMA_NAME}.{TABLE_NAME} SET nr = cd_remessa::TEXT"
    )
    op.alter_column(TABLE_NAME, 'nr', nullable=False, schema=SCHEMA_NAME)
    op.create_unique_constraint(
        'uq_assoc_remessa_ipm_manual_processo_nr',
        TABLE_NAME,
        ['numero_processo', 'competencia_producao', 'nr'],
        schema=SCHEMA_NAME,
    )
