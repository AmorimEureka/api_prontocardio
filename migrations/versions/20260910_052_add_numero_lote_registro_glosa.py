"""add optional batch number to glosa records

Revision ID: 20260910_052
Revises: 20260903_051
Create Date: 2026-09-10 17:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260910_052'
down_revision: Union[str, Sequence[str], None] = '20260903_051'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
TABLE_NAME = 'registros_glosa'


def upgrade() -> None:
    op.add_column(
        TABLE_NAME,
        sa.Column('numero_lote', sa.String(length=255), nullable=True),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_column(TABLE_NAME, 'numero_lote', schema=SCHEMA_NAME)
