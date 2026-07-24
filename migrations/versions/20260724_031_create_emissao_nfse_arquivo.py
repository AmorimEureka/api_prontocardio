"""create emissao nfse arquivo

Revision ID: 20260724_031
Revises: 20260723_030
Create Date: 2026-07-24 00:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260724_031'
down_revision: Union[str, Sequence[str], None] = '20260723_030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
LOCAL_NOW = sa.text("timezone('America/Sao_Paulo', now())")


def upgrade() -> None:
    op.create_table(
        'emissao_nfse_arquivo',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('emissao_nfse_id', sa.Integer(), nullable=False),
        sa.Column('nome_arquivo', sa.String(length=255), nullable=False),
        sa.Column('tipo_mime', sa.String(length=100), nullable=False),
        sa.Column('conteudo', sa.LargeBinary(), nullable=False),
        sa.Column('tamanho_bytes', sa.BigInteger(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            server_default=LOCAL_NOW,
            nullable=False,
        ),
        sa.Column(
            'data_atualizacao',
            sa.DateTime(),
            server_default=LOCAL_NOW,
            nullable=False,
        ),
        sa.CheckConstraint(
            'tamanho_bytes >= 0',
            name='ck_emissao_nfse_arquivo_tamanho',
        ),
        sa.ForeignKeyConstraint(
            ['emissao_nfse_id'],
            [f'{SCHEMA_NAME}.emissao_nfse.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'emissao_nfse_id',
            name='uq_emissao_nfse_arquivo_emissao',
        ),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_table('emissao_nfse_arquivo', schema=SCHEMA_NAME)
