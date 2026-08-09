"""track imported demonstrativo ipm denial rows

Revision ID: 20260809_038
Revises: 20260729_037
Create Date: 2026-08-09 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260809_038'
down_revision: Union[str, Sequence[str], None] = '20260729_037'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
INDEX_NAME = 'ix_registros_glosa_demo_ipm_registro_glosa'


def upgrade() -> None:
    op.create_table(
        'registros_glosa_demonstrativo_ipm',
        sa.Column('id_registro', sa.String(), nullable=False),
        sa.Column('registro_glosa_id', sa.Integer(), nullable=False),
        sa.Column(
            'data_importacao',
            sa.DateTime(),
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['registro_glosa_id'],
            [f'{SCHEMA_NAME}.registros_glosa.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id_registro'),
        schema=SCHEMA_NAME,
    )
    op.create_index(
        INDEX_NAME,
        'registros_glosa_demonstrativo_ipm',
        ['registro_glosa_id'],
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_index(
        INDEX_NAME,
        table_name='registros_glosa_demonstrativo_ipm',
        schema=SCHEMA_NAME,
    )
    op.drop_table(
        'registros_glosa_demonstrativo_ipm',
        schema=SCHEMA_NAME,
    )
