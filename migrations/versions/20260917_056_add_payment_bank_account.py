"""add bank agency and account to accounts payable payments

Revision ID: 20260917_056
Revises: 20260917_055
Create Date: 2026-09-17 15:45:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260917_056'
down_revision: Union[str, Sequence[str], None] = '20260917_055'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.add_column(
        'contas_pagar_pagamentos',
        sa.Column('agencia', sa.String(length=30), nullable=True),
        schema=SCHEMA_NAME,
    )
    op.add_column(
        'contas_pagar_pagamentos',
        sa.Column('numero_conta', sa.String(length=50), nullable=True),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_column(
        'contas_pagar_pagamentos',
        'numero_conta',
        schema=SCHEMA_NAME,
    )
    op.drop_column(
        'contas_pagar_pagamentos',
        'agencia',
        schema=SCHEMA_NAME,
    )
