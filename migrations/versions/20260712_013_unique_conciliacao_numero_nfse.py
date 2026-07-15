"""make conciliacao numero nfse unique

Revision ID: 20260712_013
Revises: 20260712_012
Create Date: 2026-07-12 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = '20260712_013'
down_revision: Union[str, Sequence[str], None] = '20260712_012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
CONSTRAINT_NAME = 'uq_conciliacoes_faturamento_numero_nfse'


def upgrade() -> None:
    op.create_unique_constraint(
        CONSTRAINT_NAME,
        'conciliacoes_faturamento',
        ['numero_nfse'],
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_constraint(
        CONSTRAINT_NAME,
        'conciliacoes_faturamento',
        schema=SCHEMA_NAME,
        type_='unique',
    )
