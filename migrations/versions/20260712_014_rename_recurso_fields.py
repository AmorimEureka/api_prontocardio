"""rename recurso fields in registros_glosa

Revision ID: 20260712_014
Revises: 20260712_013
Create Date: 2026-07-12 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = '20260712_014'
down_revision: Union[str, Sequence[str], None] = '20260712_013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.alter_column(
        'registros_glosa',
        'qtd_glosada',
        new_column_name='qtd_recursado',
        schema=SCHEMA_NAME,
    )
    op.alter_column(
        'registros_glosa',
        'valor_glosado',
        new_column_name='valor_recursado',
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.alter_column(
        'registros_glosa',
        'valor_recursado',
        new_column_name='valor_glosado',
        schema=SCHEMA_NAME,
    )
    op.alter_column(
        'registros_glosa',
        'qtd_recursado',
        new_column_name='qtd_glosada',
        schema=SCHEMA_NAME,
    )
