# ruff: noqa: E501

"""create accounts payable title payments

Revision ID: 20260917_055
Revises: 20260917_054
Create Date: 2026-09-17 15:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260917_055'
down_revision: Union[str, Sequence[str], None] = '20260917_054'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.create_table(
        'contas_pagar_pagamentos',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('codigo_fornecedor', sa.BigInteger(), nullable=False),
        sa.Column('codigo_parcela', sa.BigInteger(), nullable=False),
        sa.Column('data_pagamento', sa.Date(), nullable=False),
        sa.Column('valor_pago', sa.Numeric(15, 2), nullable=False),
        sa.Column('banco', sa.String(length=150), nullable=False),
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
            'valor_pago > 0',
            name='ck_contas_pagar_pagamento_valor_positivo',
        ),
        sa.ForeignKeyConstraint(
            ['usuario_id'],
            [f'{SCHEMA_NAME}.usuarios_api.id'],
            ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA_NAME,
    )
    op.create_index(
        'ix_contas_pagar_pagamentos_parcela',
        'contas_pagar_pagamentos',
        ['codigo_fornecedor', 'codigo_parcela'],
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_contas_pagar_pagamentos_parcela',
        table_name='contas_pagar_pagamentos',
        schema=SCHEMA_NAME,
    )
    op.drop_table('contas_pagar_pagamentos', schema=SCHEMA_NAME)
