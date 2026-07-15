"""allow a remittance reconciliation for an open recurso

Revision ID: 20260712_015
Revises: 20260712_014
Create Date: 2026-07-12 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260712_015'
down_revision: Union[str, Sequence[str], None] = '20260712_014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
OLD_CONSTRAINT = 'uq_conciliacoes_faturamento_remessas_cd_remessa'
NEW_CONSTRAINT = 'uq_conciliacoes_remessas_cd_remessa_tipo'


def upgrade() -> None:
    op.add_column(
        'conciliacoes_faturamento_remessas',
        sa.Column(
            'tp_conciliacao',
            sa.String(),
            server_default=sa.text("'faturamento'"),
            nullable=False,
        ),
        schema=SCHEMA_NAME,
    )
    op.drop_constraint(
        OLD_CONSTRAINT,
        'conciliacoes_faturamento_remessas',
        schema=SCHEMA_NAME,
        type_='unique',
    )
    op.create_unique_constraint(
        NEW_CONSTRAINT,
        'conciliacoes_faturamento_remessas',
        ['cd_remessa', 'tp_conciliacao'],
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_constraint(
        NEW_CONSTRAINT,
        'conciliacoes_faturamento_remessas',
        schema=SCHEMA_NAME,
        type_='unique',
    )
    op.create_unique_constraint(
        OLD_CONSTRAINT,
        'conciliacoes_faturamento_remessas',
        ['cd_remessa'],
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'conciliacoes_faturamento_remessas',
        'tp_conciliacao',
        schema=SCHEMA_NAME,
    )
