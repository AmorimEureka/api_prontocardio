"""add reconciliation denial item snapshot fields

Revision ID: 20260716_019
Revises: 20260715_018
Create Date: 2026-07-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260716_019'
down_revision: Union[str, Sequence[str], None] = '20260715_018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.add_column(
        'registros_glosa',
        sa.Column('descricao_item', sa.String(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'registros_glosa',
        sa.Column('data_alta', sa.DateTime(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'registros_glosa',
        sa.Column('data_lancamento', sa.DateTime(), nullable=True),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_column(
        'registros_glosa',
        'data_lancamento',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'registros_glosa',
        'data_alta',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'registros_glosa',
        'descricao_item',
        schema=SCHEMA_NAME,
    )
