"""allow multiple recurso reconciliation rounds

Revision ID: 20260712_016
Revises: 20260712_015
Create Date: 2026-07-12 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = '20260712_016'
down_revision: Union[str, Sequence[str], None] = '20260712_015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
OLD_CONSTRAINT = 'uq_conciliacoes_remessas_cd_remessa_tipo'
NEW_CONSTRAINT = 'uq_conciliacoes_remessas_conciliacao_remessa'


def upgrade() -> None:
    op.drop_constraint(
        OLD_CONSTRAINT,
        'conciliacoes_faturamento_remessas',
        schema=SCHEMA_NAME,
        type_='unique',
    )
    op.create_unique_constraint(
        NEW_CONSTRAINT,
        'conciliacoes_faturamento_remessas',
        ['conciliacao_id', 'cd_remessa'],
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
        ['cd_remessa', 'tp_conciliacao'],
        schema=SCHEMA_NAME,
    )
