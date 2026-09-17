# ruff: noqa: E501

"""create accounts payable operational management

Revision ID: 20260917_054
Revises: 20260912_053
Create Date: 2026-09-17 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260917_054'
down_revision: Union[str, Sequence[str], None] = '20260912_053'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.create_table(
        'contas_pagar_tratamentos',
        sa.Column('codigo_fornecedor', sa.BigInteger(), nullable=False),
        sa.Column(
            'critico',
            sa.Boolean(),
            server_default=sa.text('false'),
            nullable=False,
        ),
        sa.Column(
            'pagamento_imediato',
            sa.Numeric(15, 2),
            server_default='0',
            nullable=False,
        ),
        sa.Column(
            'status',
            sa.String(length=30),
            server_default='PENDENTE',
            nullable=False,
        ),
        sa.Column('responsavel', sa.String(length=150), nullable=True),
        sa.Column('proxima_acao', sa.String(length=255), nullable=True),
        sa.Column('data_proxima_acao', sa.Date(), nullable=True),
        sa.Column('condicao_negociada', sa.Text(), nullable=True),
        sa.Column('observacao', sa.Text(), nullable=True),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column(
            'data_criacao',
            sa.DateTime(),
            server_default=sa.text("timezone('America/Sao_Paulo', now())"),
            nullable=False,
        ),
        sa.Column(
            'data_atualizacao',
            sa.DateTime(),
            server_default=sa.text("timezone('America/Sao_Paulo', now())"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDENTE', 'CONTATO', 'NEGOCIACAO', 'ACORDADO')",
            name='ck_contas_pagar_tratamento_status',
        ),
        sa.CheckConstraint(
            'pagamento_imediato >= 0',
            name='ck_contas_pagar_pagamento_imediato',
        ),
        sa.ForeignKeyConstraint(
            ['usuario_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
            ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('codigo_fornecedor'),
        schema=SCHEMA_NAME,
    )
    op.create_table(
        'contas_pagar_snapshots',
        sa.Column('data_referencia', sa.Date(), nullable=False),
        sa.Column('valor_vencido', sa.Numeric(15, 2), nullable=False),
        sa.Column('novos_vencidos', sa.Numeric(15, 2), nullable=False),
        sa.Column('valor_corrente', sa.Numeric(15, 2), nullable=False),
        sa.Column('fornecedores_vencidos', sa.Integer(), nullable=False),
        sa.Column(
            'data_registro',
            sa.DateTime(),
            server_default=sa.text("timezone('America/Sao_Paulo', now())"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('data_referencia'),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_table('contas_pagar_snapshots', schema=SCHEMA_NAME)
    op.drop_table('contas_pagar_tratamentos', schema=SCHEMA_NAME)
