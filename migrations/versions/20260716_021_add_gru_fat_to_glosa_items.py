"""add billing group snapshot to denial items

Revision ID: 20260716_021
Revises: 20260716_020
Create Date: 2026-07-16 00:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260716_021'
down_revision: Union[str, Sequence[str], None] = '20260716_020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.add_column(
        'registros_glosa',
        sa.Column('cd_gru_fat', sa.Integer(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'registros_glosa',
        sa.Column('ds_gru_fat', sa.String(), nullable=True),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_column(
        'registros_glosa',
        'ds_gru_fat',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'registros_glosa',
        'cd_gru_fat',
        schema=SCHEMA_NAME,
    )
