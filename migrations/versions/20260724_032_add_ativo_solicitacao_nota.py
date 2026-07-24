"""add ativo to solicitacao nota

Revision ID: 20260724_032
Revises: 20260724_031
Create Date: 2026-07-24 10:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260724_032'
down_revision: Union[str, Sequence[str], None] = '20260724_031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'


def upgrade() -> None:
    op.add_column(
        'solicitacao_nota',
        sa.Column(
            'ativo',
            sa.Boolean(),
            server_default=sa.text('true'),
            nullable=False,
        ),
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_column(
        'solicitacao_nota',
        'ativo',
        schema=SCHEMA_NAME,
    )
