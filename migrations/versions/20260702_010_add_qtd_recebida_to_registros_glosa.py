"""add qtd_recebida to registros_glosa

Revision ID: 20260702_010
Revises: 20260622_009
Create Date: 2026-07-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '20260702_010'
down_revision: Union[str, Sequence[str], None] = '20260622_009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.add_column(
        'registros_glosa',
        sa.Column('qtd_recebida', sa.Numeric(12, 2), nullable=True),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_column(
        'registros_glosa',
        'qtd_recebida',
        schema=SCHEMA_NAME,
    )
