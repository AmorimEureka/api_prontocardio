"""link analytical denial records to reconciliation remittances

Revision ID: 20260715_018
Revises: 20260713_017
Create Date: 2026-07-15 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260715_018'
down_revision: Union[str, Sequence[str], None] = '20260713_017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
FK_NAME = 'fk_registros_glosa_conciliacao_remessa'
UNIQUE_NAME = 'uq_registro_glosa_conciliacao_item'
INDEX_NAME = 'ix_registros_glosa_conciliacao_remessa'


def upgrade() -> None:
    op.add_column(
        'registros_glosa',
        sa.Column('cd_lancamento', sa.Integer(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'registros_glosa',
        sa.Column('qtd_registro', sa.Numeric(12, 2), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'registros_glosa',
        sa.Column('conciliacao_remessa_id', sa.Integer(), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.create_foreign_key(
        FK_NAME,
        'registros_glosa',
        'conciliacoes_faturamento_remessas',
        ['conciliacao_remessa_id'],
        ['id'],
        source_schema=SCHEMA_NAME,
        referent_schema=SCHEMA_NAME,
        ondelete='SET NULL',
    )
    op.create_unique_constraint(
        UNIQUE_NAME,
        'registros_glosa',
        ['conciliacao_remessa_id', 'conta', 'cd_lancamento'],
        schema=SCHEMA_NAME,
    )
    op.create_index(
        INDEX_NAME,
        'registros_glosa',
        ['conciliacao_remessa_id'],
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_index(
        INDEX_NAME,
        table_name='registros_glosa',
        schema=SCHEMA_NAME,
    )
    op.drop_constraint(
        UNIQUE_NAME,
        'registros_glosa',
        schema=SCHEMA_NAME,
        type_='unique',
    )
    op.drop_constraint(
        FK_NAME,
        'registros_glosa',
        schema=SCHEMA_NAME,
        type_='foreignkey',
    )
    op.drop_column(
        'registros_glosa',
        'conciliacao_remessa_id',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'registros_glosa',
        'qtd_registro',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'registros_glosa',
        'cd_lancamento',
        schema=SCHEMA_NAME,
    )
