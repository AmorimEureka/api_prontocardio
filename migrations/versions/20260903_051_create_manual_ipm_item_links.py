"""create manual IPM item links

Revision ID: 20260903_051
Revises: 20260826_048
Create Date: 2026-09-03 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260903_051'
down_revision: Union[str, Sequence[str], None] = '20260826_048'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
TABLE_NAME = 'associacoes_itens_ipm_manuais'


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('glosa_id_registro', sa.String(), nullable=False),
        sa.Column('numero_processo', sa.String(length=100), nullable=False),
        sa.Column('competencia_producao', sa.String(length=7), nullable=False),
        sa.Column('nr', sa.String(length=100), nullable=False),
        sa.Column('cd_remessa', sa.BigInteger(), nullable=False),
        sa.Column('conta', sa.BigInteger(), nullable=False),
        sa.Column('cd_lancamento', sa.BigInteger(), nullable=False),
        sa.Column(
            'criterio_correspondencia', sa.String(length=100), nullable=True
        ),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
            nullable=False,
        ),
        sa.Column(
            'data_atualizacao',
            sa.DateTime(),
            server_default=sa.text(
                "timezone('America/Sao_Paulo', now())"
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['usuario_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
            ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'glosa_id_registro', name='uq_assoc_item_ipm_manual_glosa'
        ),
        sa.UniqueConstraint(
            'cd_remessa',
            'conta',
            'cd_lancamento',
            name='uq_assoc_item_ipm_manual_lancamento',
        ),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_table(TABLE_NAME, schema=SCHEMA_NAME)
