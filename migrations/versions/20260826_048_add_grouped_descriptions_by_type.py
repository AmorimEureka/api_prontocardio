"""add grouped descriptions by treatment type

Revision ID: 20260826_048
Revises: 20260826_047
Create Date: 2026-08-26 14:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260826_048'
down_revision: Union[str, Sequence[str], None] = '20260826_047'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
TABLE_NAME = 'registros_glosa'
RESOURCE_COLUMN = 'descricao_recurso_agrupada'
ACCEPT_COLUMN = 'descricao_acato_agrupada'


def upgrade() -> None:
    op.add_column(
        TABLE_NAME,
        sa.Column(RESOURCE_COLUMN, sa.String(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        TABLE_NAME,
        sa.Column(ACCEPT_COLUMN, sa.String(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.execute(
        sa.text(
            f'''UPDATE {SCHEMA_NAME}.{TABLE_NAME}
                SET {RESOURCE_COLUMN} = descricao_glosa_agrupada
                WHERE dt_recurso IS NOT NULL
                  AND sn_glosado <> 'not'
                  AND descricao_glosa_agrupada IS NOT NULL'''
        )
    )
    op.execute(
        sa.text(
            f'''UPDATE {SCHEMA_NAME}.{TABLE_NAME}
                SET {ACCEPT_COLUMN} = descricao_glosa_agrupada
                WHERE dt_recurso IS NOT NULL
                  AND sn_glosado = 'not'
                  AND descricao_glosa_agrupada IS NOT NULL'''
        )
    )


def downgrade() -> None:
    op.drop_column(TABLE_NAME, ACCEPT_COLUMN, schema=SCHEMA_NAME)
    op.drop_column(TABLE_NAME, RESOURCE_COLUMN, schema=SCHEMA_NAME)
