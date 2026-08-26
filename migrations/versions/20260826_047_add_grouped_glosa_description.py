"""add grouped description to glosa records

Revision ID: 20260826_047
Revises: 20260821_046
Create Date: 2026-08-26 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260826_047'
down_revision: Union[str, Sequence[str], None] = '20260821_046'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
TABLE_NAME = 'registros_glosa'
COLUMN_NAME = 'descricao_glosa_agrupada'


def upgrade() -> None:
    op.add_column(
        TABLE_NAME,
        sa.Column(COLUMN_NAME, sa.String(), nullable=True),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_column(TABLE_NAME, COLUMN_NAME, schema=SCHEMA_NAME)
