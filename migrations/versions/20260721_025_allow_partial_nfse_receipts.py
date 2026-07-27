"""allow multiple partial receipts per reconciled invoice

Revision ID: 20260721_025
Revises: 20260717_024
Create Date: 2026-07-21 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = '20260721_025'
down_revision: Union[str, Sequence[str], None] = '20260717_024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
UNIQUE_CONSTRAINT = 'uq_recebimento_conciliacao_remessa'
LOOKUP_INDEX = 'ix_recebimentos_remessas_conciliacao_remessa'


def upgrade() -> None:
    op.drop_constraint(
        UNIQUE_CONSTRAINT,
        'recebimentos_remessas',
        schema=SCHEMA_NAME,
        type_='unique',
    )
    op.create_index(
        LOOKUP_INDEX,
        'recebimentos_remessas',
        ['conciliacao_id', 'cd_remessa'],
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_index(
        LOOKUP_INDEX,
        table_name='recebimentos_remessas',
        schema=SCHEMA_NAME,
    )
    op.create_unique_constraint(
        UNIQUE_CONSTRAINT,
        'recebimentos_remessas',
        ['conciliacao_id', 'cd_remessa'],
        schema=SCHEMA_NAME,
    )
