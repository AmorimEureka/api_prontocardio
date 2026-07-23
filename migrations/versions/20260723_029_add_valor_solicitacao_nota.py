"""add valor solicitacao nota

Revision ID: 20260723_029
Revises: 20260723_028
Create Date: 2026-07-23 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260723_029'
down_revision: Union[str, Sequence[str], None] = '20260723_028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.add_column(
        'solicitacao_nota',
        sa.Column(
            'valor_nota',
            sa.Numeric(precision=14, scale=2),
            nullable=True,
        ),
        schema=SCHEMA_NAME,
    )
    op.create_check_constraint(
        'ck_solicitacao_nota_valor_nota',
        'solicitacao_nota',
        'valor_nota IS NULL OR valor_nota > 0',
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_constraint(
        'ck_solicitacao_nota_valor_nota',
        'solicitacao_nota',
        schema=SCHEMA_NAME,
        type_='check',
    )
    op.drop_column(
        'solicitacao_nota',
        'valor_nota',
        schema=SCHEMA_NAME,
    )
