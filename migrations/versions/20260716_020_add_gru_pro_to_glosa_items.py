"""add procedure group snapshot to denial items

Revision ID: 20260716_020
Revises: 20260716_019
Create Date: 2026-07-16 00:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260716_020'
down_revision: Union[str, Sequence[str], None] = '20260716_019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.add_column(
        'registros_glosa',
        sa.Column('cd_gru_pro', sa.Integer(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'registros_glosa',
        sa.Column('ds_gru_pro', sa.String(), nullable=True),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_column(
        'registros_glosa',
        'ds_gru_pro',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'registros_glosa',
        'cd_gru_pro',
        schema=SCHEMA_NAME,
    )
