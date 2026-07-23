"""allow recurso and acato for the same follow-up item

Revision ID: 20260722_026
Revises: 20260721_025
Create Date: 2026-07-22 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = '20260722_026'
down_revision: Union[str, Sequence[str], None] = '20260721_025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_NAME = 'api_prontocardio'
CONSTRAINT_NAME = 'uq_registro_glosa_conciliacao_item'


def upgrade() -> None:
    op.drop_constraint(
        CONSTRAINT_NAME,
        'registros_glosa',
        schema=SCHEMA_NAME,
        type_='unique',
    )
    op.create_unique_constraint(
        CONSTRAINT_NAME,
        'registros_glosa',
        [
            'conciliacao_remessa_id',
            'conta',
            'cd_lancamento',
            'sn_glosado',
        ],
        schema=SCHEMA_NAME,
    )


def downgrade() -> None:
    op.drop_constraint(
        CONSTRAINT_NAME,
        'registros_glosa',
        schema=SCHEMA_NAME,
        type_='unique',
    )
    op.create_unique_constraint(
        CONSTRAINT_NAME,
        'registros_glosa',
        ['conciliacao_remessa_id', 'conta', 'cd_lancamento'],
        schema=SCHEMA_NAME,
    )
