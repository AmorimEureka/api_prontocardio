"""prevent duplicate active nfse issuance

Revision ID: 20260723_030
Revises: 20260723_029
Create Date: 2026-07-23 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '20260723_030'
down_revision: Union[str, Sequence[str], None] = '20260723_029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
INDEX_NAME = 'uq_emissao_nfse_solicitacao_ativa'


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        'emissao_nfse',
        ['solicitacao_nota_id'],
        unique=True,
        schema=SCHEMA_NAME,
        postgresql_where=sa.text(
            "status IN ('PENDENTE', 'PROCESSANDO', 'EMITIDA')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        INDEX_NAME,
        table_name='emissao_nfse',
        schema=SCHEMA_NAME,
    )
